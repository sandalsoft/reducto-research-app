# AGENTS.md

## What this repo is

A local Reducto benchmark (`reducto-bench`) on House Oversight email PDFs. Digital PDF text layer = ground truth.
Each doc is run as native PDF and as an image-only "scan".

## Commands

```bash
uv sync
uv run python -m bench run --n 100 --max-pages 3 --concurrency 8 --max-usd 30
uv run python -m bench reextract --concurrency 8 --max-usd 30
uv run python -m bench report
uv run pytest --cov=bench
```

`REDUCTO_API_KEY` is loaded from the nearest `.env` above this package. Never read, print, or commit `.env`.

## Invariants

- Failures are recorded on the result dict (`error`), never raised out of `pipeline.py`.
- Do not commit `data/`, `output/`, or `.env`. They contain the public-release documents and run artifacts.
- Extract schema is `messages[]` with Deep Extract + citations. Score threads with `checks.match_messages`, not row-1 vs row-1.
- `jobid://` chaining is required after Parse. Schema experiments should use `bench reextract`, not `--fresh`, unless parse settings changed.
- Overlays are generated at report time from citations already in jsonl; they must not call the API.

## Where to change things

- Schema / prompt / prices: `bench/config.py`
- Accuracy math: `bench/checks.py` (keep it pure; tests live in `tests/test_checks.py`)
- API calls: `bench/pipeline.py` only
- Citation drawing: `bench/overlay.py`
- Report tables: `bench/report.py`

## Progress

- Baseline: classify + parse r-1 + scalar extract, digital vs image, STP, unit economics.
- Current: thread extract (`messages[]`, Deep Extract), greedy message matching, citation bbox overlays, `reextract` on cached parse jobs.
