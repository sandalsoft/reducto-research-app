# reducto-bench

Exercises the [Reducto](https://docs.reducto.ai) API on House Oversight Epstein-estate email documents. It answers four
questions: **(a) ease of use, (b) performance, (c) accuracy, (d) fit for business problems.**

Each document runs twice through the same pipeline: **upload → classify → parse (r-1) → extract (schema + citations,
chained via `jobid://`)**.

- **digital:** the original PDF, which has a text layer.
- **image:** an image-only PDF built from the page JPGs, i.e. a simulated scan.

The digital PDF's text layer is exact ground truth for both runs. That turns accuracy into real character/word error
rates and per-field correctness instead of proxies.

## Setup
```bash
uv sync                     # Python 3.12, reductoai SDK
# REDUCTO_API_KEY lives in ../.env (loaded automatically, never printed)
```
Inputs (not in git):
- `data/inbox/<ID>.txt.pdf`: digital PDFs.
- `data/images/<ID>_page_<n>.jpg`: one image per page.

## Run
```bash
uv run python -m bench run --n 100 --max-pages 3 --concurrency 8 --max-usd 30 --fresh
uv run python -m bench report        # -> output/report.md, output/emails.csv
uv run pytest --cov=bench            # unit tests (no API calls)
```
- `--max-usd` is a hard stop computed from page counts × list price, since the API returns no credit values.
- Runs resume: finished (doc, variant) pairs are skipped unless you pass `--fresh`.

## Layout
| file | role |
|---|---|
| `bench/corpus.py` | pair PDFs with page images, seeded sampling, build image-only "scan" PDFs, ground truth text |
| `bench/pipeline.py` | all Reducto calls, timed; failures recorded, never raised |
| `bench/runner.py` | sequential baseline + concurrent load phase, spend cap, repeatability re-runs |
| `bench/checks.py` | CER/WER, header ground truth, field matching, grounding, auto-accept rule |
| `bench/report.py` | builds the report from `output/*.jsonl` (no API calls) |
| `notes/friction_log.md` | developer-experience observations made while building this |

## Caveats
- The image variant is a clean render, so OCR results are an upper bound for real scans.
- Email labels and header ground truth come from heuristics over the text layer.
- `output/` contains document content from the public release. Keep it local.
