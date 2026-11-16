# Friction log (observed while building this benchmark, 2026-11-15)

Everything here was observed first-hand in this build, not taken from marketing copy.

## What was easy
- **Key → first successful call in one step.** `GET /jobs?limit=1` verified the key on the first try.
- **First pipeline worked unchanged.** `pip install reductoai` (SDK 0.27.0, Python 3.12) and the chain
  upload → classify → parse (r-1) → extract via `jobid://` worked with no debugging on 3 random pages.
- **Small surface.** 4 API calls per document. `jobid://` chaining reuses the parse, so there is no re-upload
  or re-OCR for extraction.
- **Typed SDK responses.** Pydantic models, `studio_link` on every job, built-in retries (`max_retries`).

## Friction / gotchas
1. **Parse still defaults to the legacy model** until 2026-12-07; `settings.model="r-1"` must be passed explicitly.
2. **No cost in responses.** `usage.credits` is `None` on every endpoint for this account. Pricing moved to
   per-page USD on 2026-09-01, so cost had to be computed from page counts × list price.
3. **Classify confidences are saturated and independent.** `response_confidence.categories[]` (not shown in the
   agent guide's response shape) returned only 0.0/1.0 values, and one document scored `email=1.0` *and*
   `news_article=1.0`. These are not calibrated probabilities, so they can't drive a routing threshold.
4. **Extract confidence type differs from the docs.** With citations on, `confidence` came back as `"high"`/`"low"`
   although the docs list `numerical_confidence` as default true. Numeric values live in
   `granular_confidence.extract_confidence` (plus `parse_confidence`).
5. **Response shape changes with settings.** The extract `result` is a list by default but a dict when citations
   are on. Scalar fields are `{value, citations}`, while array fields are bare lists whose items are each wrapped.
6. **Parse output is markdown with HTML entities** (`&lt;`) and light punctuation normalization (`....` → `...`).
   Downstream text comparison needs entity unescaping.
7. **Docs disagree.** `reducto.ai/SKILL.md` recommends `settings.array_extract`, but the agent guide marks it
   deprecated in favor of `deep_extract`.
8. **Extract latency is spiky.** 1.8 s to 17.4 s for similar 2–3 page emails in the pilot.
9. **(Documented, not exercised)** Edit takes `document_url`, not `input` like every other endpoint.
