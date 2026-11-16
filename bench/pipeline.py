"""Reducto calls. Each function returns a plain-dict record with timings and never raises:
failures are captured as `error` so the benchmark measures reliability instead of crashing."""
import os
import time
import types
from contextlib import contextmanager
from pathlib import Path

import httpx
from reducto import Reducto

from .config import CLASSIFY_SCHEMA, EMAIL_SCHEMA, EXTRACT_PROMPT, load_env


@contextmanager
def timed():
    t = types.SimpleNamespace(s=0.0)
    start = time.perf_counter()
    try:
        yield t
    finally:
        t.s = time.perf_counter() - start


def plain(o):
    return o.model_dump() if hasattr(o, "model_dump") else o


def error_info(stage: str, exc: Exception) -> dict:
    return {"stage": stage, "type": type(exc).__name__, "status": getattr(exc, "status_code", None),
            "message": str(exc)[:300]}


def make_client() -> Reducto:
    load_env()
    if not os.environ.get("REDUCTO_API_KEY"):
        raise SystemExit("REDUCTO_API_KEY is not set (expected in the project .env)")
    return Reducto(timeout=300, max_retries=2)


def _parse(client: Reducto, file_id: str, k: int) -> dict:
    with timed() as t:
        pr = client.parse.run(input=file_id, settings={"model": "r-1", "page_range": {"start": 1, "end": k}})
    if pr.result.type == "url":  # large results come back as a URL, not inline
        chunks = httpx.get(pr.result.url, timeout=60).json()
    else:
        chunks = [plain(c) for c in pr.result.chunks]
    blocks = [b for c in chunks for b in (c.get("blocks") or [])]
    return {"parse_s": t.s, "parse_job_id": pr.job_id, "parse_pages": pr.usage.num_pages,
            "studio_link": getattr(pr, "studio_link", None),
            "text": "\n\n".join(c.get("content", "") for c in chunks),
            "blocks": len(blocks), "low_conf_blocks": sum(1 for b in blocks if b.get("confidence") == "low")}


def extract(client: Reducto, parse_job_id: str) -> dict:
    with timed() as t:
        ex = client.extract.run(
            input=f"jobid://{parse_job_id}",  # reuse the parse: no re-upload, no re-OCR
            instructions={"schema": EMAIL_SCHEMA, "system_prompt": EXTRACT_PROMPT},
            settings={"citations": {"enabled": True}},
        )
    result = plain(ex.result)
    if isinstance(result, list):
        result = result[0] if result else {}
    return {"extract_s": t.s, "extract_job_id": ex.job_id, "extract_pages": ex.usage.num_pages,
            "fields": result}


def run_variant(client: Reducto, doc_id: str, variant: str, path: Path, k: int, do_extract: bool) -> dict:
    """upload -> classify -> parse (r-1) -> [extract via jobid://] on the first k pages."""
    rec = {"id": doc_id, "variant": variant, "error": None, "t0": time.time()}
    page_range = {"start": 1, "end": k}
    stage = "upload"
    try:
        with timed() as t:
            up = client.upload(file=path)
        rec.update(upload_s=t.s, file_id=up.file_id)
        stage = "classify"
        with timed() as t:
            cl = client.classify.run(input=up.file_id, classification_schema=CLASSIFY_SCHEMA, page_range=page_range)
        conf = plain(cl.response_confidence) or {}
        rec.update(classify_s=t.s, category=cl.result.category, classify_pages=cl.usage.num_pages,
                   confidences={c["category"]: c["confidence"] for c in conf.get("categories", [])})
        stage = "parse"
        rec.update(_parse(client, up.file_id, k))
        if do_extract:
            stage = "extract"
            rec.update(extract(client, rec["parse_job_id"]))
    except Exception as exc:  # noqa: BLE001 - benchmark must record, not crash
        rec["error"] = error_info(stage, exc)
    rec["t1"] = time.time()
    return rec


def repeat_extract(client: Reducto, rec: dict) -> dict:
    """Determinism check: run the same extraction again on the same parse."""
    out = {"id": rec["id"], "variant": rec["variant"], "error": None}
    try:
        out.update(extract(client, rec["parse_job_id"]))
    except Exception as exc:  # noqa: BLE001
        out["error"] = error_info("extract", exc)
    return out
