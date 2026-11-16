"""Turn output/*.jsonl into output/report.md (+ emails.csv). No API calls; safe to rerun."""
import csv
import json
from collections import Counter
from pathlib import Path

from . import checks
from .config import CORE_FIELDS, HEADER_FIELDS, NOTES_DIR, OUT_DIR, PRICE_PER_PAGE, ROOT
from .runner import read_jsonl
from .stats import percentile, summarize

VARIANTS = ("digital", "image")
THRESHOLDS = (0.8, 0.9, 0.95)


def _pct(x):
    return "n/a" if x is None else f"{100 * x:.1f}%"


def _s(x):
    return "n/a" if x is None else f"{x:.2f}s"


def _table(headers, rows) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _cost(r) -> float:
    return sum(PRICE_PER_PAGE[k] * (r.get(f"{k}_pages") or 0) for k in PRICE_PER_PAGE)


def _ocr(r, gt):
    hyp, ref = checks.clean(r.get("text", "")), checks.clean(gt["text"])
    return {"cer": checks.cer(hyp, ref), "wer": checks.wer(hyp, ref), "recall": checks.word_recall(hyp, ref)}


def _core_correct(r, gt):
    """All scorable core header fields match ground truth (None if nothing scorable)."""
    scorable = [f for f in CORE_FIELDS if gt["headers"].get(f)]
    if not scorable or not r.get("fields"):
        return None
    return all(checks.field_match(r["fields"].get(f), gt["headers"][f]) for f in scorable)


def _min_core_conf(r):
    confs = [c for f in CORE_FIELDS for c in checks.unwrap((r.get("fields") or {}).get(f))[1]]
    return min(confs) if confs else None


def section_run(gt, res, meta) -> str:
    ok = [r for r in res if r["error"] is None]
    errs = Counter((r["error"]["stage"], r["error"]["type"], r["error"].get("status")) for r in res if r["error"])
    rows = []
    for v in VARIANTS:
        vr = [r for r in ok if r["variant"] == v]
        rows.append([v, len(vr), sum(r.get("parse_pages") or 0 for r in vr),
                     sum(1 for r in vr if r.get("fields") is not None), f"${sum(map(_cost, vr)):.2f}"])
    out = [
        "## 1. Run summary",
        f"- Documents: **{len(gt)}** House Oversight PDFs, first **{meta.get('max_pages', '?')}** pages each; "
        f"**{sum(g['is_email'] for g in gt.values())}** are emails by ground-truth label.",
        f"- Each document is run twice: **digital** (original PDF with text layer) and **image** "
        "(image-only PDF built from the supplied page JPGs, i.e. a simulated scan).",
        f"- Runs: {len(ok)}/{len(res)} succeeded. Errors: "
        + (", ".join(f"{s}/{t}/{st}: {n}" for (s, t, st), n in errs.items()) if errs else "none") + ".",
        f"- Parse model `{meta.get('parse_model')}`, SDK `reductoai {meta.get('reductoai_sdk')}`, "
        f"concurrency {meta.get('concurrency')}, seed {meta.get('seed')}.",
        "",
        _table(["variant", "runs ok", "pages parsed", "extractions", "cost (list price)"], rows),
    ]
    return "\n".join(out)


def section_performance(res) -> str:
    ok = [r for r in res if r["error"] is None]
    rows = []
    for v in VARIANTS:
        for stage in ("upload", "classify", "parse", "extract", "end-to-end"):
            def vals(phase):
                rs = [r for r in ok if r["variant"] == v and r.get("phase") == phase]
                if stage == "end-to-end":
                    return [r["t1"] - r["t0"] for r in rs]
                return [r.get(f"{stage}_s") for r in rs if r.get(f"{stage}_s")]
            b, ld = summarize(vals("baseline")), summarize(vals("load"))
            rows.append([v, stage, _s(b.get("p50")), _s(ld.get("p50")), _s(ld.get("p90")),
                         _s(ld.get("p95")), _s(ld.get("max")), ld.get("n", 0)])
    per_page = {v: summarize([r["parse_s"] / r["parse_pages"] for r in ok
                              if r["variant"] == v and r.get("parse_pages")]) for v in VARIANTS}
    load = [r for r in ok if r.get("phase") == "load"]
    lines = ["## 2. Performance",
             "Baseline = documents run one at a time (uncontended); load = remaining documents at the configured "
             "concurrency, both variants interleaved.", "",
             _table(["variant", "stage", "baseline p50", "load p50", "load p90", "load p95", "load max", "n (load)"],
                    rows), "",
             "- Parse seconds per page (p50): " + ", ".join(f"{v} {_s(per_page[v].get('p50'))}" for v in VARIANTS) + "."]
    if load:
        wall = max(r["t1"] for r in load) - min(r["t0"] for r in load)
        pages = sum(r.get("parse_pages") or 0 for r in load)
        lines.append(f"- Throughput under load: **{60 * pages / wall:.0f} pages/min** "
                     f"({60 * len(load) / wall:.1f} document-runs/min, full pipeline incl. upload) over {wall:.0f}s.")
    return "\n".join(lines)


def section_ocr(gt, res) -> str:
    ok = [r for r in res if r["error"] is None]
    rows, worst = [], []
    for v in VARIANTS:
        m = [(r["id"], _ocr(r, gt[r["id"]])) for r in ok if r["variant"] == v]
        cers = [x["cer"] for _, x in m]
        if not cers:
            continue
        rows.append([v, len(m), _pct(_mean(cers)), _pct(percentile(cers, 50)), _pct(percentile(cers, 90)),
                     _pct(_mean([x["wer"] for _, x in m])), _pct(_mean([x["recall"] for _, x in m])),
                     sum(c > 0.02 for c in cers)])
        worst += [(v, i, x) for i, x in m]
    worst = sorted(worst, key=lambda t: -t[2]["cer"])[:6]
    return "\n".join([
        "### 3a. Text fidelity vs ground truth (PDF text layer)",
        "CER/WER = edit distance after removing markdown/HTML markup on both sides. Word recall ignores order, "
        "so a high CER with high recall means reading-order or duplication differences, not misread characters.",
        "", _table(["variant", "docs", "mean CER", "median CER", "p90 CER", "mean WER", "word recall", "docs CER>2%"],
                   rows), "",
        "Worst documents (inspect in Studio via `output/results.jsonl` → `studio_link`):", "",
        _table(["variant", "doc", "CER", "WER", "word recall"],
               [[v, i, _pct(x["cer"]), _pct(x["wer"]), _pct(x["recall"])] for v, i, x in worst])])


def section_classify(gt, res) -> str:
    ok = [r for r in res if r["error"] is None]
    rows, dist = [], {}
    for v in VARIANTS:
        vr = [r for r in ok if r["variant"] == v]
        m = checks.prf([r.get("category") == "email" for r in vr], [gt[r["id"]]["is_email"] for r in vr])
        rows.append([v, len(vr), _pct(m["precision"]), _pct(m["recall"]), _pct(m["accuracy"]),
                     m["tp"], m["fp"], m["fn"]])
        dist[v] = Counter(r.get("category") for r in vr)
    pairs = [(a, b) for a in ok for b in ok if a["id"] == b["id"] and a["variant"] == "digital" and b["variant"] == "image"]
    same = _mean([a.get("category") == b.get("category") for a, b in pairs])
    confs = [c for r in ok for c in (r.get("confidences") or {}).values()]
    saturated = _mean([c in (0.0, 1.0) for c in confs])
    multi = sum(1 for r in ok if sum(1 for c in (r.get("confidences") or {}).values() if c >= 0.99) > 1)
    return "\n".join([
        "### 3b. Classification (is it an email?)",
        "Ground-truth label: the first page of the PDF text layer has ≥2 distinct email header lines "
        "(From/To/Sent/Date/Subject/Cc). This is a heuristic label, so FP/FN rows deserve a manual look.", "",
        _table(["variant", "docs", "precision", "recall", "accuracy", "TP", "FP", "FN"], rows), "",
        "- Category distribution: " + "; ".join(f"{v}: " + ", ".join(f"{k} {n}" for k, n in dist[v].most_common())
                                                 for v in VARIANTS) + ".",
        f"- Same category on digital vs image of the same document: {_pct(same)}.",
        f"- Confidence values that are exactly 0 or 1: {_pct(saturated)}; runs with more than one category at "
        f"≥0.99: {multi}. Saturated, non-exclusive confidences can't serve as a routing threshold."])


def section_extract(gt, res, rep) -> str:
    ok = [r for r in res if r["error"] is None and r.get("fields") is not None]
    rows = []
    for v in VARIANTS:
        vr = [r for r in ok if r["variant"] == v]
        row = [v]
        for f in HEADER_FIELDS:
            scored = [checks.field_match(r["fields"].get(f), gt[r["id"]]["headers"][f])
                      for r in vr if gt[r["id"]]["headers"].get(f)]
            row.append(f"{_pct(_mean(scored))} (n={len(scored)})")
        core = [c for c in (_core_correct(r, gt[r["id"]]) for r in vr) if c is not None]
        row.append(f"{_pct(_mean(core))} (n={len(core)})")
        rows.append(row)
    ground = []
    for v in VARIANTS:
        reps = [checks.field_report(r["fields"], r.get("text", "")) for r in ok if r["variant"] == v]
        g = [x["grounded"] for rep_ in reps for x in rep_.values() if x["filled"]]
        ground.append(f"{v} {_pct(_mean(g))}")
    by = {(r["id"], r["variant"]): r for r in ok}
    cross = [checks.agreement(by[(i, "digital")]["fields"], by[(i, "image")]["fields"])
             for i, v in by if v == "digital" and (i, "image") in by]
    rep_ok = [r for r in rep if r["error"] is None and (r["id"], r["variant"]) in by]
    repeat = [checks.agreement(by[(r["id"], r["variant"])]["fields"], r["fields"]) for r in rep_ok]
    return "\n".join([
        "### 3c. Field extraction vs ground truth headers",
        "Ground truth = the first same-line value of each header label on page 1 of the text layer "
        "(fields whose value is on another line or blank are not scored). Match = exact after normalizing "
        "case, punctuation, whitespace and list order; one misread character counts as wrong. "
        "`core` = sender + sent_date + subject all correct.", "",
        _table(["variant", *HEADER_FIELDS, "core (all correct)"], rows), "",
        f"- Extracted values found verbatim-ish in the parse output (grounding, all fields incl. body): {', '.join(ground)}.",
        f"- Digital vs image extraction agreement (same fields, same doc): mean {_pct(_mean(cross))} over {len(cross)} docs.",
        f"- Repeatability (same parse, extraction run twice): mean field agreement {_pct(_mean(repeat))}; "
        f"identical outputs {sum(x == 1.0 for x in repeat)}/{len(repeat)}."])


def section_business(gt, res) -> str:
    ok = [r for r in res if r["error"] is None and r.get("fields") is not None]
    rows = []
    for v in VARIANTS:
        vr = [r for r in ok if r["variant"] == v]
        for t in THRESHOLDS:
            acc, rej = [], []
            for r in vr:
                rep_ = checks.field_report(r["fields"], r.get("text", ""))
                (acc if checks.auto_accept(rep_, r["fields"], t) else rej).append(_core_correct(r, gt[r["id"]]))
            acc_s, rej_s = [x for x in acc if x is not None], [x for x in rej if x is not None]
            rows.append([v, t, f"{_pct(len(acc) / len(vr)) if vr else 'n/a'} ({len(acc)}/{len(vr)})",
                         _pct(_mean(acc_s)), _pct(_mean(rej_s))])
    all_ok = [r for r in res if r["error"] is None]
    unit = []
    for v in VARIANTS:
        vr = [r for r in all_ok if r["variant"] == v]
        em = [r for r in vr if r.get("fields") is not None]
        unit.append([v, f"${_mean([_cost(r) for r in vr]) or 0:.4f}", f"${1000 * (_mean([_cost(r) for r in em]) or 0):.2f}",
                     f"{_mean([r.get('parse_pages') for r in vr]) or 0:.2f}"])
    return "\n".join([
        "## 4. Business fit",
        "### 4a. Straight-through processing (auto-accept without human review)",
        "Rule: sender, sent_date and subject are filled, found in the parse text, each has extract confidence ≥ "
        "threshold (`granular_confidence.extract_confidence`), and the date parses. 'Correct' = all scorable "
        "core fields match ground truth. A useful confidence signal shows accepted ≫ rejected correctness.", "",
        _table(["variant", "threshold", "auto-accepted", "correct when accepted", "correct when sent to review"],
               rows), "",
        "### 4b. Unit economics (list price; contracted rates differ; batch queue is 20% cheaper)",
        _table(["variant", "avg cost per doc (classify+parse[+extract])", "per 1,000 emails, full pipeline",
                "avg pages/doc"], unit), "",
        "### 4c. Product surface exercised",
        _table(["capability", "status"], [
            ["Classify (routing)", "exercised, see 3b"], ["Parse r-1 (digital + image)", "exercised, see 3a"],
            ["Extract + citations (`jobid://` chained)", "exercised, see 3c / 4a"],
            ["Split", "not exercised (documents are single threads; Split works on page ranges)"],
            ["Edit (form fill)", "not applicable to this corpus"],
            ["Async / webhooks / batch queue", "not exercised (sync calls with a thread pool)"]]),
        "", "Structured output for downstream use (e.g. eDiscovery review, entity/timeline building): "
            "`output/emails.csv`."])


def write_csv(gt, res, path: Path) -> None:
    ok = [r for r in res if r["error"] is None and r.get("fields") is not None and r["variant"] == "image"]
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "sender", "to", "cc", "sent_date", "subject", "body_chars", "min_core_conf",
                    "auto_accept@0.9", "core_matches_ground_truth", "studio_link"])
        for r in ok:
            v = {k: checks.unwrap(r["fields"].get(k))[0] for k in r["fields"]}
            rep_ = checks.field_report(r["fields"], r.get("text", ""))
            w.writerow([r["id"], v.get("sender"), "; ".join(v.get("recipients_to") or []),
                        "; ".join(v.get("recipients_cc") or []), v.get("sent_date"), v.get("subject"),
                        len(v.get("body") or ""), _min_core_conf(r), checks.auto_accept(rep_, r["fields"], 0.9),
                        _core_correct(r, gt[r["id"]]), r.get("studio_link")])


def section_doj_pilot(out_dir: Path) -> str:
    recs = read_jsonl(out_dir / "pilot_doj" / "screen.jsonl")
    if not recs:
        return ""
    cats = Counter(r.get("category") for r in recs if r["error"] is None)
    lat = summarize([r.get("classify_s") for r in recs])
    return "\n".join([
        "## Appendix: DOJ photo-heavy pilot (Classify only)",
        f"{len(recs)} random single-page DOJ 'EFTA' scans were classified while hunting for emails; none were emails. "
        "Distribution: " + ", ".join(f"{k} {n}" for k, n in cats.most_common()) + f". Classify p50 {_s(lat.get('p50'))}, "
        f"errors {sum(1 for r in recs if r['error'])}. Manual inspection of a sample confirmed photographs."])


def build(out_dir: Path = OUT_DIR, notes_dir: Path = NOTES_DIR) -> Path:
    gt = {r["id"]: r for r in read_jsonl(out_dir / "ground_truth.jsonl")}
    res = [r for r in read_jsonl(out_dir / "results.jsonl") if r["id"] in gt]
    rep = read_jsonl(out_dir / "repeat.jsonl")
    meta_path = out_dir / "run_meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    friction = notes_dir / "friction_log.md"
    pipeline_loc = len((ROOT / "bench" / "pipeline.py").read_text().splitlines()) if (ROOT / "bench" / "pipeline.py").exists() else "?"
    parts = [
        "# Reducto benchmark: House Oversight emails, digital vs scanned", "",
        "Questions: (a) ease of use, (b) performance, (c) accuracy, (d) ability to solve business problems.", "",
        section_run(gt, res, meta), "", section_performance(res), "",
        "## 3. Accuracy", section_ocr(gt, res), "", section_classify(gt, res), "", section_extract(gt, res, rep), "",
        (notes_dir / "mismatch_review.md").read_text() if (notes_dir / "mismatch_review.md").exists() else "", "",
        section_business(gt, res), "",
        "## 5. Ease of use",
        f"- Integration surface: 4 calls per document (upload, classify, parse, extract); the whole Reducto layer "
        f"of this app is `bench/pipeline.py` ({pipeline_loc} lines incl. timing and error capture).",
        "", friction.read_text() if friction.exists() else "(no friction log)", "",
        "## 6. Limitations",
        "- The image variant is a clean render (no skew, noise, stamps or redaction boxes): OCR numbers are an "
        "upper bound for real scans.",
        "- Email labels and header ground truth come from heuristics over the text layer; spot-check FP/FN rows.",
        "- Only the first pages of each document are processed; long threads are truncated.",
        "- One run, one seed, n≈100: treat differences of a few points as noise.",
        "- Cost uses public list prices computed from page counts, because the API returned no credit values.",
        "", section_doj_pilot(out_dir)]
    report = out_dir / "report.md"
    report.write_text("\n".join(parts).rstrip() + "\n")
    write_csv(gt, res, out_dir / "emails.csv")
    print(f"wrote {report} and {out_dir / 'emails.csv'}")
    return report
