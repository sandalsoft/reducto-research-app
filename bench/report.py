"""Turn output/*.jsonl into output/report.md (+ emails.csv). No API calls; safe to rerun."""
import csv
import json
from collections import Counter
from pathlib import Path

from . import checks, overlay
from .config import CORE_FIELDS, FIELDS, HEADER_FIELDS, IMAGES_DIR, NOTES_DIR, OUT_DIR, PRICE_PER_PAGE, ROOT
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
    extract_kind = "deep_extract" if r.get("deep_extract") else "extract"
    return (PRICE_PER_PAGE["classify"] * (r.get("classify_pages") or 0)
            + PRICE_PER_PAGE["parse"] * (r.get("parse_pages") or 0)
            + PRICE_PER_PAGE[extract_kind] * (r.get("extract_pages") or 0))


def _ocr(r, gt):
    hyp, ref = checks.clean(r.get("text", "")), checks.clean(gt["text"])
    return {"cer": checks.cer(hyp, ref), "wer": checks.wer(hyp, ref), "recall": checks.word_recall(hyp, ref)}


def _core_correct(r, gt):
    """First-message core vs first-block headers (legacy scalar comparison)."""
    msgs = checks.as_messages(r.get("fields"))
    truth = (gt.get("headers") or {}) if gt else {}
    if not msgs:
        return None
    return checks.message_core_correct(msgs[0], truth)


def _min_core_conf(msg: dict):
    confs = [c for f in CORE_FIELDS for c in checks.unwrap((msg or {}).get(f))[1]]
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


def _thread_rows(vr, gt):
    """Aggregate greedy message-matching stats for one variant."""
    n_gt = n_ex = n_pair = n_extra = n_miss = 0
    field_hits = {f: [] for f in HEADER_FIELDS}
    cores = []
    for r in vr:
        blocks = checks.gt_blocks(gt[r["id"]])
        msgs = checks.as_messages(r.get("fields"))
        m = checks.match_messages(msgs, blocks)
        n_gt += len(blocks)
        n_ex += len(msgs)
        n_pair += len(m["pairs"])
        n_extra += len(m["extras"])
        n_miss += len(m["misses"])
        for i, j, _ in m["pairs"]:
            t = blocks[j]
            cores.append(checks.message_core_correct(msgs[i], t))
            for f in HEADER_FIELDS:
                if t.get(f):
                    field_hits[f].append(checks.field_match(msgs[i].get(f), t[f]))
    return {"n_gt": n_gt, "n_ex": n_ex, "n_pair": n_pair, "n_extra": n_extra, "n_miss": n_miss,
            "field_hits": field_hits, "cores": [c for c in cores if c is not None]}


def section_extract(gt, res, rep) -> str:
    ok = [r for r in res if r["error"] is None and r.get("fields") is not None]
    match_rows, field_rows, first_rows = [], [], []
    for v in VARIANTS:
        vr = [r for r in ok if r["variant"] == v]
        t = _thread_rows(vr, gt)
        denom = t["n_pair"] + t["n_extra"] + t["n_miss"]
        match_rows.append([v, len(vr), t["n_gt"], t["n_ex"], t["n_pair"], t["n_extra"], t["n_miss"],
                           _pct(t["n_pair"] / denom if denom else None)])
        field_rows.append([v] + [f"{_pct(_mean(t['field_hits'][f]))} (n={len(t['field_hits'][f])})"
                                 for f in HEADER_FIELDS]
                          + [f"{_pct(_mean(t['cores']))} (n={len(t['cores'])})"])
        first = [c for c in (_core_correct(r, gt[r["id"]]) for r in vr) if c is not None]
        first_rows.append([v, f"{_pct(_mean(first))} (n={len(first)})"])
    ground = []
    for v in VARIANTS:
        reps = [x for r in ok if r["variant"] == v for x in checks.field_reports(r["fields"], r.get("text", ""))]
        g = [row["grounded"] for rep_ in reps for row in rep_.values() if row["filled"]]
        ground.append(f"{v} {_pct(_mean(g))}")
    by = {(r["id"], r["variant"]): r for r in ok}
    cross = [checks.agreement(by[(i, "digital")]["fields"], by[(i, "image")]["fields"])
             for i, v in by if v == "digital" and (i, "image") in by]
    rep_ok = [r for r in rep if r["error"] is None and (r["id"], r["variant"]) in by]
    repeat = [checks.agreement(by[(r["id"], r["variant"])]["fields"], r["fields"]) for r in rep_ok]
    n_msg = [_mean([len(checks.as_messages(r["fields"])) for r in ok if r["variant"] == v]) for v in VARIANTS]
    return "\n".join([
        "### 3c. Thread extraction vs header-block ground truth",
        "Extract schema is `messages[]` (one object per header block), not a single top email. "
        "Ground truth = every cluster of From/To/Sent/Subject lines in the text layer; a repeated "
        "label starts a new message. Extracted messages are matched greedily on sender+date+subject "
        "(score ≥ 0.5). Match = exact after normalizing case, punctuation, whitespace and list order. "
        "`core` = sender + sent_date + subject all correct on a matched pair.", "",
        _table(["variant", "emails", "gt messages", "extracted", "matched", "extras", "misses",
                "match rate"], match_rows), "",
        _table(["variant", *HEADER_FIELDS, "core on matched"], field_rows), "",
        "First-message-only core (old scalar question: was the top email right?):", "",
        _table(["variant", "first-message core"], first_rows), "",
        f"- Mean messages extracted per email: " + ", ".join(f"{v} {m:.2f}" if m else f"{v} n/a"
                                                            for v, m in zip(VARIANTS, n_msg)) + ".",
        f"- Extracted values found verbatim-ish in the parse output (grounding, all fields incl. body): {', '.join(ground)}.",
        f"- Digital vs image extraction agreement (matched messages): mean {_pct(_mean(cross))} over {len(cross)} docs.",
        f"- Repeatability (same parse, extraction run twice): mean field agreement {_pct(_mean(repeat))}; "
        f"identical outputs {sum(x == 1.0 for x in repeat)}/{len(repeat)}."])


def section_business(gt, res, overlay_stats=None) -> str:
    ok = [r for r in res if r["error"] is None and r.get("fields") is not None]
    rows = []
    for v in VARIANTS:
        vr = [r for r in ok if r["variant"] == v]
        for t in THRESHOLDS:
            acc, rej = [], []
            for r in vr:
                blocks = checks.gt_blocks(gt[r["id"]])
                msgs = checks.as_messages(r["fields"])
                matched = {i: j for i, j, _ in checks.match_messages(msgs, blocks)["pairs"]}
                accepts = checks.auto_accept_messages(r["fields"], r.get("text", ""), t)
                for i, accepted in enumerate(accepts):
                    if i in matched:
                        correct = checks.message_core_correct(msgs[i], blocks[matched[i]])
                    else:
                        correct = False  # extra message: accepted extras are wrong
                    (acc if accepted else rej).append(correct)
            acc_s, rej_s = [x for x in acc if x is not None], [x for x in rej if x is not None]
            n = len(acc) + len(rej)
            rows.append([v, t, f"{_pct(len(acc) / n if n else None)} ({len(acc)}/{n})",
                         _pct(_mean(acc_s)), _pct(_mean(rej_s))])
    all_ok = [r for r in res if r["error"] is None]
    unit = []
    for v in VARIANTS:
        vr = [r for r in all_ok if r["variant"] == v]
        em = [r for r in vr if r.get("fields") is not None]
        unit.append([v, f"${_mean([_cost(r) for r in vr]) or 0:.4f}", f"${1000 * (_mean([_cost(r) for r in em]) or 0):.2f}",
                     f"{_mean([r.get('parse_pages') for r in vr]) or 0:.2f}"])
    overlay_line = ""
    if overlay_stats and overlay_stats.get("pages"):
        overlay_line = (f"{overlay_stats['boxes']} citation boxes drawn on {overlay_stats['pages']} pages "
                        f"({overlay_stats['docs']} document-runs) → `{overlay_stats['dir']}` / `index.md`.")
    elif overlay_stats:
        overlay_line = "No page images available to draw citation overlays."
    return "\n".join([
        "## 4. Business fit",
        "### 4a. Straight-through processing (auto-accept without human review)",
        "Rule, per message: sender, sent_date and subject are filled, found in the parse text, each has extract "
        "confidence ≥ threshold (`granular_confidence.extract_confidence`), and the date parses. "
        "'Correct' = the message matched a GT header block and all scorable core fields match. "
        "Accepted extras (unmatched extracts) count as incorrect. A useful confidence signal shows "
        "accepted ≫ rejected correctness.", "",
        _table(["variant", "threshold", "auto-accepted", "correct when accepted", "correct when sent to review"],
               rows), "",
        "### 4b. Unit economics (list price; contracted rates differ; batch queue is 20% cheaper)",
        "Deep Extract is $40/1,000 pages (2× standard Extract). Costs below use Deep Extract when "
        "`deep_extract` is set on the run record.",
        _table(["variant", "avg cost per doc (classify+parse[+extract])", "per 1,000 emails, full pipeline",
                "avg pages/doc"], unit), "",
        "### 4c. Product surface exercised",
        _table(["capability", "status"], [
            ["Classify (routing)", "exercised, see 3b"], ["Parse r-1 (digital + image)", "exercised, see 3a"],
            ["Extract + citations (`jobid://` chained)", "exercised, see 3c / 4a"],
            ["Deep Extract (`messages[]` schema)", "exercised, see 3c"],
            ["Citation bbox overlays", overlay_line or "generated by `bench report` → output/overlays/"],
            ["Split", "not exercised (messages share pages; Split returns page ranges)"],
            ["Edit (form fill)", "not applicable to this corpus"],
            ["Async / webhooks / batch queue", "not exercised (sync calls with a thread pool)"]]),
        "", "Structured output for downstream use (e.g. eDiscovery review, entity/timeline building): "
            "`output/emails.csv` (one row per extracted message)."])


def write_csv(gt, res, path: Path) -> None:
    ok = [r for r in res if r["error"] is None and r.get("fields") is not None and r["variant"] == "image"]
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "msg_index", "n_messages", "sender", "to", "cc", "sent_date", "subject",
                    "body_chars", "n_citations", "min_core_conf", "auto_accept@0.9", "matched_gt",
                    "core_matches_ground_truth", "studio_link"])
        for r in ok:
            msgs = checks.as_messages(r["fields"])
            blocks = checks.gt_blocks(gt[r["id"]]) if r["id"] in gt else []
            matched = {i: j for i, j, _ in checks.match_messages(msgs, blocks)["pairs"]}
            accepts = checks.auto_accept_messages(r["fields"], r.get("text", ""), 0.9)
            for i, msg in enumerate(msgs):
                v = {k: checks.unwrap(msg.get(k))[0] for k in FIELDS}
                n_cit = sum(len(checks.citations(msg.get(k))) for k in FIELDS)
                truth = blocks[matched[i]] if i in matched else None
                core = checks.message_core_correct(msg, truth) if truth else False
                to = v.get("recipients_to") or []
                cc = v.get("recipients_cc") or []
                w.writerow([r["id"], i, len(msgs), v.get("sender"),
                            "; ".join(to) if isinstance(to, list) else to,
                            "; ".join(cc) if isinstance(cc, list) else cc,
                            v.get("sent_date"), v.get("subject"),
                            len(v.get("body") or ""), n_cit, _min_core_conf(msg),
                            accepts[i] if i < len(accepts) else False,
                            i in matched, core, r.get("studio_link")])


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


def build(out_dir: Path = OUT_DIR, notes_dir: Path = NOTES_DIR, images_dir: Path = IMAGES_DIR) -> Path:
    gt = {r["id"]: r for r in read_jsonl(out_dir / "ground_truth.jsonl")}
    res = [r for r in read_jsonl(out_dir / "results.jsonl") if r["id"] in gt]
    rep = read_jsonl(out_dir / "repeat.jsonl")
    meta_path = out_dir / "run_meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    overlay_stats = overlay.write_overlays(res, out_dir / "overlays", images_dir)
    friction = notes_dir / "friction_log.md"
    pipeline_loc = len((ROOT / "bench" / "pipeline.py").read_text().splitlines()) if (ROOT / "bench" / "pipeline.py").exists() else "?"
    parts = [
        "# Reducto benchmark: House Oversight emails, digital vs scanned", "",
        "Questions: (a) ease of use, (b) performance, (c) accuracy, (d) ability to solve business problems.", "",
        section_run(gt, res, meta), "", section_performance(res), "",
        "## 3. Accuracy", section_ocr(gt, res), "", section_classify(gt, res), "", section_extract(gt, res, rep), "",
        (notes_dir / "mismatch_review.md").read_text() if (notes_dir / "mismatch_review.md").exists() else "", "",
        section_business(gt, res, overlay_stats), "",
        "## 5. Ease of use",
        f"- Integration surface: 4 calls per document (upload, classify, parse, extract); the whole Reducto layer "
        f"of this app is `bench/pipeline.py` ({pipeline_loc} lines incl. timing and error capture).",
        "- Schema change without re-parse: `python -m bench reextract` reuses `jobid://` parse jobs.",
        "", friction.read_text() if friction.exists() else "(no friction log)", "",
        "## 6. Limitations",
        "- The image variant is a clean render (no skew, noise, stamps or redaction boxes): OCR numbers are an "
        "upper bound for real scans.",
        "- Email labels and header-block ground truth come from heuristics over the text layer; two-column "
        "header tables and wrapped subjects still fool the GT, which is the original point of 3d.",
        "- Only the first pages of each document are processed; long threads are truncated.",
        "- One run, one seed, n≈100: treat differences of a few points as noise.",
        "- Cost uses public list prices computed from page counts, because the API returned no credit values. "
        "Deep Extract is billed at $40/1,000 pages.",
        "", section_doj_pilot(out_dir)]
    report = out_dir / "report.md"
    report.write_text("\n".join(parts).rstrip() + "\n")
    write_csv(gt, res, out_dir / "emails.csv")
    print(f"wrote {report}, {out_dir / 'emails.csv'}"
          + (f", overlays {overlay_stats['pages']} pages" if overlay_stats.get("pages") else ""))
    return report
