# Architecture

## Purpose

Benchmark Reducto on a paired digital/scan email corpus. The pipeline is the product under test; scoring is local and
deterministic so a schema change can be re-run without re-parsing.

## Data flow

```
data/inbox/*.pdf  +  data/images/*_page_n.jpg
        │
        ▼
  corpus.discover / sample / build_scan_pdf
        │
        ▼
  pipeline: upload → classify → parse(r-1) → extract(deep_extract, citations, jobid://)
        │
        ▼
  output/ground_truth.jsonl
  output/results.jsonl
  output/repeat.jsonl
        │
        ▼
  report.build  →  output/report.md
                   output/emails.csv          (one row per extracted message)
                   output/overlays/<id>/*.jpg (citation bboxes on page images)
```

`python -m bench reextract` starts at Extract: it reads `parse_job_id` from `results.jsonl` and overwrites `fields`.
That is the cheap way to change the schema.

## Extract schema

The document is a **list of messages**, not one top email.

```
{ messages: [ { sender, recipients_to, recipients_cc, sent_date, subject, body }, ... ] }
```

`checks.as_messages` also accepts the legacy flat object so old `results.jsonl` rows still score and overlay.

## Ground truth and matching

- `header_fields`: first same-line value per label (legacy, used for first-message core).
- `header_blocks`: a repeated header label starts a new message. Two-column tables still fool this; that is scored, not hidden.
- `match_messages`: greedy 1-1 on sender+date+subject (`pair_score` ≥ 0.5 vs GT; ≥ 0 for agreement).
- STP is per message. Accepted extras (unmatched extracts) count as incorrect.

## Citations

Extract returns `{value, citations[]}` with normalized `bbox` (`left/top/width/height/page` in 0..1).
`overlay.citation_boxes` walks every message and field; `draw_page` paints `m{k}.{field}` on the JPG.

## Cost

List prices in `config.PRICE_PER_PAGE`. Deep Extract is $40/1,000 pages. `Spend` is a thread-safe running total and a
hard stop. The API does not return credits on this account.

## Package map

| module | owns |
|---|---|
| `config` | paths, prices, classify/extract schemas, prompt |
| `corpus` | filesystem pairing, scan PDF construction, PDF text layer |
| `pipeline` | Reducto SDK calls; never raises |
| `runner` | sampling, concurrency, spend, jsonl, reextract |
| `checks` | all accuracy functions (pure) |
| `overlay` | bbox → JPG |
| `report` | markdown + csv from jsonl; no API |
| `stats` | percentile / summarize |
