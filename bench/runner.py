"""Orchestration: run every sampled document through Reducto twice (digital PDF and image-only
'scan' PDF), with a sequential baseline, a concurrent load phase, and a list-price spend cap."""
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from importlib import metadata

from . import checks, pipeline
from .config import IMAGES_DIR, INBOX_DIR, OUT_DIR, PRICE_PER_PAGE, SCAN_DIR
from .corpus import build_scan_pdf, discover, ground_truth_pages, sample

VARIANTS = ("digital", "image")


class Spend:
    """Thread-safe running cost at list price, used as a hard stop."""

    def __init__(self, max_usd: float):
        self.max_usd, self.usd, self._lock = max_usd, 0.0, threading.Lock()

    def add(self, kind: str, pages) -> None:
        with self._lock:
            self.usd += PRICE_PER_PAGE[kind] * (pages or 0)

    def over(self) -> bool:
        return self.usd >= self.max_usd - 1e-9

    def add_record(self, rec: dict) -> None:
        self.add("classify", rec.get("classify_pages"))
        self.add("parse", rec.get("parse_pages"))
        kind = "deep_extract" if rec.get("deep_extract") else "extract"
        self.add(kind, rec.get("extract_pages"))


def read_jsonl(path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def append_jsonl(path, recs) -> None:
    with path.open("a") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")


def _run_pool(fn, items, workers, label, sink):
    with ThreadPoolExecutor(max(1, workers)) as ex:
        futures = [ex.submit(fn, *args) for args in items]
        for n, fut in enumerate(as_completed(futures), 1):
            sink([fut.result()])
            if n % 10 == 0 or n == len(futures):
                print(f"  {label}: {n}/{len(futures)}", file=sys.stderr, flush=True)


def write_jsonl(path, recs) -> None:
    """Atomic replace. Used when reextract rewrites fields in place."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    tmp.replace(path)


def write_ground_truth(docs, k, path) -> dict:
    """PDF text layer for the first k pages = exact ground truth for both variants."""
    existing = {r["id"]: r for r in read_jsonl(path)}
    for d in docs:
        if d.id not in existing:
            pages = ground_truth_pages(d.pdf, k)
            text = "\n".join(pages)
            rec = {"id": d.id, "pages_total": len(d.images), "k_pages": len(pages), "text": text,
                   "is_email": checks.looks_like_email(pages[0] if pages else ""),
                   "headers": checks.header_fields(pages[0] if pages else ""),
                   "header_blocks": checks.header_blocks(text)}
            append_jsonl(path, [rec])
            existing[d.id] = rec
        elif "header_blocks" not in existing[d.id]:
            existing[d.id]["header_blocks"] = checks.header_blocks(existing[d.id].get("text") or "")
    return existing


def run_all(args) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    paths = {k: OUT_DIR / f"{k}.jsonl" for k in ("ground_truth", "results", "repeat")}
    if args.fresh:
        for p in paths.values():
            p.unlink(missing_ok=True)
    client, spend = pipeline.make_client(), Spend(args.max_usd)
    docs = sample(discover(INBOX_DIR, IMAGES_DIR), args.n, args.seed)
    gt = write_ground_truth(docs, args.max_pages, paths["ground_truth"])
    started = time.time()
    print(f"{len(docs)} docs, first {args.max_pages} pages each, {sum(g['is_email'] for g in gt.values())} "
          f"ground-truth emails; budget ${args.max_usd:.2f}", file=sys.stderr)

    done = {(r["id"], r["variant"]) for r in read_jsonl(paths["results"]) if r["error"] is None}
    jobs = []
    for i, d in enumerate(docs):
        for v in VARIANTS:
            if (d.id, v) not in done:
                path = d.pdf if v == "digital" else build_scan_pdf(d, args.max_pages, SCAN_DIR)
                jobs.append((d.id, v, path, "baseline" if i < args.baseline_n else "load"))

    def work(doc_id, variant, path, phase):
        if spend.over():
            return {"id": doc_id, "variant": variant, "phase": phase,
                    "error": {"stage": "budget", "type": "BudgetExceeded"}}
        rec = pipeline.run_variant(client, doc_id, variant, path, args.max_pages, gt[doc_id]["is_email"])
        spend.add_record(rec)
        return {**rec, "phase": phase}

    sink = lambda recs: append_jsonl(paths["results"], recs)  # noqa: E731
    base = [j for j in jobs if j[3] == "baseline"]
    load = [j for j in jobs if j[3] == "load"]
    print(f"[1/2] baseline: {len(base)} runs sequentially, then {len(load)} runs at concurrency "
          f"{args.concurrency}", file=sys.stderr)
    _run_pool(work, base, 1, "baseline", sink)
    _run_pool(work, load, args.concurrency, "load", sink)

    print(f"[2/2] repeatability: re-extract {args.repeat_n} image-variant emails", file=sys.stderr)
    repeated = {r["id"] for r in read_jsonl(paths["repeat"])}
    cands = [r for r in read_jsonl(paths["results"]) if r["error"] is None and r["variant"] == "image"
             and r.get("fields") is not None and r["id"] not in repeated][:args.repeat_n]

    def rerun(r):
        out = pipeline.repeat_extract(client, r)
        spend.add("extract", out.get("extract_pages"))
        return out

    if not spend.over():
        _run_pool(rerun, [(r,) for r in cands], args.concurrency, "repeat",
                  lambda recs: append_jsonl(paths["repeat"], recs))

    meta = {"n_docs": len(docs), "seed": args.seed, "max_pages": args.max_pages,
            "concurrency": args.concurrency, "baseline_docs": args.baseline_n, "max_usd": args.max_usd,
            "spend_usd_this_invocation": round(spend.usd, 4),
            "wall_s_this_invocation": round(time.time() - started, 1),
            "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"), "reductoai_sdk": metadata.version("reductoai"),
            "python": sys.version.split()[0], "parse_model": "r-1"}
    (OUT_DIR / "run_meta.json").write_text(json.dumps(meta, indent=2))
    print(f"done. spend this run ${spend.usd:.2f} (list price). Next: python -m bench report", file=sys.stderr)


def _local_path(rec: dict, max_pages: int, docs: dict):
    d = docs.get(rec["id"])
    if d is None:
        return None
    return d.pdf if rec["variant"] == "digital" else build_scan_pdf(d, max_pages, SCAN_DIR)


def reextract_all(args) -> None:
    """Re-run Extract with the current schema. Reuses parse jobs when they still exist;
    otherwise re-uploads the local file. Failed calls leave the previous extract in place."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "results.jsonl"
    recs = read_jsonl(path)
    cands = [r for r in recs if r.get("error") is None and r.get("fields") is not None]
    if args.n:
        seen, ids = set(), []
        for r in cands:
            if r["id"] not in seen:
                seen.add(r["id"])
                ids.append(r["id"])
            if len(ids) >= args.n:
                break
        cands = [r for r in cands if r["id"] in set(ids)]
    meta_path = OUT_DIR / "run_meta.json"
    max_pages = json.loads(meta_path.read_text()).get("max_pages", 3) if meta_path.exists() else 3
    docs_by_id = {d.id: d for d in discover(INBOX_DIR, IMAGES_DIR)}
    client, spend = pipeline.make_client(), Spend(args.max_usd)
    print(f"reextract {len(cands)} runs, budget ${args.max_usd:.2f}, deep_extract=True", file=sys.stderr)
    by_key = {(r["id"], r["variant"]): r for r in recs}

    def work(rec):
        if spend.over():
            return rec
        try:
            out = pipeline.extract(client, rec.get("parse_job_id"), rec.get("file_id"))
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "status_code", None) != 404:
                print(f"  reextract fail {rec['id']} {rec['variant']}: {type(exc).__name__}: {exc}",
                      file=sys.stderr, flush=True)
                return rec
            local = _local_path(rec, max_pages, docs_by_id)
            if local is None or not local.exists():
                print(f"  reextract fail {rec['id']} {rec['variant']}: cache expired, no local file",
                      file=sys.stderr, flush=True)
                return rec
            try:
                with pipeline.timed() as t:
                    up = client.upload(file=local)
                out = pipeline.extract(client, None, up.file_id)
                out["file_id"] = up.file_id
                out["upload_s"] = t.s
                print(f"  re-uploaded {rec['id']} {rec['variant']}", file=sys.stderr, flush=True)
            except Exception as exc2:  # noqa: BLE001
                print(f"  reextract fail {rec['id']} {rec['variant']}: {type(exc2).__name__}: {exc2}",
                      file=sys.stderr, flush=True)
                return rec
        spend.add("deep_extract", out.get("extract_pages"))
        return {**rec, **out, "error": None}

    def sink(chunk):
        for r in chunk:
            by_key[(r["id"], r["variant"])] = r
            state = "ok" if r.get("extract_input") else "unchanged"
            print(f"  reextract {r['id']} {r['variant']}: "
                  f"{len(checks.as_messages(r.get('fields')))} messages ({state})",
                  file=sys.stderr, flush=True)

    _run_pool(work, [(r,) for r in cands], args.concurrency, "reextract", sink)
    write_jsonl(path, [by_key[k] for k in [(r["id"], r["variant"]) for r in recs] if k in by_key])
    print(f"done. spend this run ${spend.usd:.2f}. Next: python -m bench report", file=sys.stderr)
