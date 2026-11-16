### 3d. Manual review of the extraction mismatches (seed 42 run, reviewed 2026-11-15)

The automatic score in 3c (77.4% "core" correct) compares against heuristic ground truth. I checked every core-field
mismatch by hand. The digital and image variants failed on exactly the same documents, so none of these come from OCR.

| verdict | docs | what happened |
|---|---|---|
| **Ground truth wrong, Reducto right** | 020443, 020440, 030865, 031388, 033297, 033586 | The top header block is a two-column table (labels first, then values). Reducto read the table correctly; the line-based ground truth skipped it and took the *inner* message's headers, or cut a subject that wraps onto the next line. |
| **Reducto "fixed" the source text** | 026499, 030983, 030962, 030958 | The page itself shows the House's original OCR errors (`©` for `@`, `I` for `|`). Reducto output the intended character. That's wrong against the literal page, but right for a business user. |
| **Real Reducto error** | 019338, 028621 | 019338: the subject swallowed the next line (a phone number). 028621: one digit dropped from a garbled redaction string in the sender. |

Adjudicated core accuracy over the 53 scorable emails:
- **88.7%**, counting the source-text "fixes" as wrong.
- **96.2%**, counting them as right.

Other observations from the review:
- **Repeatability:** all 10 differences between repeat runs were in `body`. Header fields were identical every time.
- **Classify false negatives:** 031399, 032806, 019845 are emails whose body is a forwarded news article. Classify said
  `news_article` with `email=0.5`. That's a defensible call, and the only non-saturated confidences seen.
- **STP numbers in 4a:** they use the unadjusted ground truth, so "correct when accepted" is understated by the same
  ground-truth errors.
