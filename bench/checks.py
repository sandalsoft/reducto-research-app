"""Pure accuracy functions: OCR error vs ground truth, header-field matching, classification
metrics, plus internal-consistency checks (grounding, confidence, repeatability)."""
import html
import re
from collections import Counter

from dateutil import parser as dtparser
from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

from .config import CORE_FIELDS, FIELDS

_HEADER = re.compile(r"^\W*(from|to|sent|date|subject|cc)\s*:", re.I | re.M)
_HEADER_LINE = re.compile(r"^[^\w\n]*(from|to|cc|sent|date|subject)[ \t]*:[ \t]*(.*)$", re.I | re.M)
_LABEL_TO_FIELD = {"from": "sender", "to": "recipients_to", "cc": "recipients_cc",
                   "sent": "sent_date", "date": "sent_date", "subject": "subject"}
_CONF_WORDS = {"high": 1.0, "low": 0.0}
# Known tags on one line only: a bare <[^>]+> would eat "Jane <a@b.com> ... > quoted reply" spans.
_HTML_TAG = re.compile(
    r"</?(?:br|p|div|span|table|thead|tbody|tr|td|th|b|i|u|em|strong|sup|sub|ul|ol|li|h[1-6])\b[^<>\n]*>", re.I)


# ---------- ground truth & OCR error ----------

def looks_like_email(text: str) -> bool:
    return len({m.group(1).lower() for m in _HEADER.finditer(text or "")}) >= 2


def header_fields(text: str) -> dict:
    """Ground-truth header values of the first message: first same-line value per label.
    Labels whose value sits on another line (or is blank) are left None = not scorable."""
    out = dict.fromkeys(_LABEL_TO_FIELD.values())
    for m in _HEADER_LINE.finditer(text or ""):
        field, value = _LABEL_TO_FIELD[m.group(1).lower()], m.group(2).strip()
        if value and out[field] is None:
            out[field] = value
    return out


def header_blocks(text: str) -> list[dict]:
    """Every header cluster in reading order. A repeated *label* (From, To, …) starts a new
    message. Sent and Date both map to sent_date and may coexist in one block.
    Same-line values only, same as header_fields; two-column tables still fool this."""
    blocks, current, seen = [], dict.fromkeys(_LABEL_TO_FIELD.values()), set()
    for m in _HEADER_LINE.finditer(text or ""):
        label = m.group(1).lower()
        field, value = _LABEL_TO_FIELD[label], m.group(2).strip() or None
        if label in seen:
            blocks.append(current)
            current, seen = dict.fromkeys(_LABEL_TO_FIELD.values()), set()
        seen.add(label)
        if value and current[field] is None:
            current[field] = value
    if seen:
        blocks.append(current)
    return blocks


def clean(text: str) -> str:
    """Symmetric normalization applied to both OCR output and reference: drop markdown/HTML
    markup that Parse adds, collapse whitespace. Case and punctuation are kept."""
    t = text or ""
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)                 # images
    t = _HTML_TAG.sub(" ", t)                                    # real html tags only (<br>, tables)
    t = html.unescape(t)                                         # &lt; -> < (after tag removal)
    t = re.sub(r"^[\s|:\-]*-{3,}[\s|:\-]*$", " ", t, flags=re.M)  # table separator rows
    t = re.sub(r"^\s{0,3}#{1,6}\s+", "", t, flags=re.M)          # headings
    t = re.sub(r"\\([\\`*_{}\[\]()#+\-.!|>])", r"\1", t)          # markdown escapes
    t = t.replace("**", " ").replace("__", " ").replace("`", " ").replace("|", " ")
    return re.sub(r"\s+", " ", t).strip()


def cer(hyp: str, ref: str) -> float:
    return Levenshtein.distance(hyp, ref) / max(1, len(ref))


def wer(hyp: str, ref: str) -> float:
    r = ref.split()
    return Levenshtein.distance(hyp.split(), r) / max(1, len(r))


def word_recall(hyp: str, ref: str) -> float:
    """Share of reference words present in the output, ignoring order (separates recognition
    errors from reading-order differences)."""
    r = Counter(ref.split())
    return sum((Counter(hyp.split()) & r).values()) / max(1, sum(r.values()))


def prf(pred: list[bool], truth: list[bool]) -> dict:
    tp = sum(p and t for p, t in zip(pred, truth))
    fp = sum(p and not t for p, t in zip(pred, truth))
    fn = sum(t and not p for p, t in zip(pred, truth))
    tn = len(pred) - tp - fp - fn
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else None
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": precision, "recall": recall,
            "f1": f1, "accuracy": (tp + tn) / len(pred) if pred else None}


# ---------- extraction ----------

def norm(s) -> str:
    # "_" is \w, but underscore runs here are separator/redaction rules, not content
    return re.sub(r"\s+", " ", re.sub(r"[^\w@]+|_+", " ", str(s).lower())).strip()


def _confidences(citations) -> list[float]:
    """Numeric confidence per citation. The API returns `confidence` as "high"/"low" even with
    numerical_confidence on; the number lives in granular_confidence.extract_confidence."""
    out = []
    for c in citations or []:
        if not isinstance(c, dict):
            continue
        granular = (c.get("granular_confidence") or {}).get("extract_confidence")
        conf = granular if isinstance(granular, (int, float)) else c.get("confidence")
        if isinstance(conf, str):
            conf = _CONF_WORDS.get(conf.lower())
        if isinstance(conf, (int, float)):
            out.append(float(conf))
    return out


def _unwrap_list(items) -> tuple[list, list[float]]:
    pairs = [unwrap(i) for i in items]
    return [v for v, _ in pairs], [c for _, cs in pairs for c in cs]


def unwrap(field):
    """Extract with citations wraps values as {value, citations}; array items are wrapped one by one.
    Returns (plain value, numeric confidences)."""
    if isinstance(field, dict) and "value" in field:
        value, confs = field["value"], _confidences(field.get("citations"))
        if isinstance(value, list):
            items, item_confs = _unwrap_list(value)
            return items, confs + item_confs
        return value, confs
    if isinstance(field, list):
        return _unwrap_list(field)
    return field, []


def as_messages(fields) -> list[dict]:
    """Normalize extract output to a list of per-message dicts.
    New schema: `{messages: [...]}`. Legacy: a single flat message object."""
    if not fields or not isinstance(fields, dict):
        return []
    if "messages" in fields:
        raw, _ = unwrap(fields["messages"])
        if isinstance(raw, list):
            return [m for m in raw if isinstance(m, dict)]
        return [raw] if isinstance(raw, dict) else []
    return [fields]


def citations(field) -> list[dict]:
    """Raw citation dicts on a wrapped field or a list of wrapped items."""
    if isinstance(field, dict) and "citations" in field:
        return [c for c in (field.get("citations") or []) if isinstance(c, dict)]
    if isinstance(field, list):
        return [c for item in field for c in citations(item)]
    return []


def _plain(msg: dict, name: str):
    v = (msg or {}).get(name)
    if isinstance(v, str):
        return v
    return unwrap(v)[0]


def pair_score(extracted: dict, truth: dict) -> float:
    """0..1 similarity of core fields. Missing extract against a scorable GT field scores 0."""
    scores = []
    for name in CORE_FIELDS:
        tv = _plain(truth, name)
        if is_empty(tv):
            continue
        ev = _plain(extracted, name)
        scores.append(0.0 if is_empty(ev) else fuzz.token_sort_ratio(norm(_as_text(ev)), norm(_as_text(tv))) / 100.0)
    return sum(scores) / len(scores) if scores else 0.0


def match_messages(extracted: list, truths: list, min_score: float = 0.5) -> dict:
    """Greedy 1-1 assignment on pair_score. extras = unmatched extracts, misses = unmatched GT."""
    ranked = sorted(((pair_score(extracted[i], truths[j]), i, j)
                     for i in range(len(extracted)) for j in range(len(truths))), reverse=True)
    used_e, used_t, pairs = set(), set(), []
    for score, i, j in ranked:
        if score < min_score or i in used_e or j in used_t:
            continue
        pairs.append((i, j, score))
        used_e.add(i)
        used_t.add(j)
    return {"pairs": pairs,
            "extras": [i for i in range(len(extracted)) if i not in used_e],
            "misses": [j for j in range(len(truths)) if j not in used_t]}


def message_core_correct(extracted: dict, truth: dict) -> bool | None:
    scorable = [f for f in CORE_FIELDS if not is_empty(_plain(truth, f))]
    if not scorable:
        return None
    return all(field_match(extracted.get(f), _plain(truth, f)) for f in scorable)


def gt_blocks(gt: dict) -> list[dict]:
    """Prefer stored header_blocks; else parse the text layer."""
    if gt.get("header_blocks"):
        return gt["header_blocks"]
    return header_blocks(gt.get("text") or "")


def is_empty(v) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    if isinstance(v, list):
        return all(is_empty(x) for x in v)
    return False


def _as_text(v) -> str:
    return " ".join(str(i) for i in v if not is_empty(i)) if isinstance(v, list) else str(v)


def field_match(extracted, truth: str, threshold: float = 100) -> bool:
    """Extracted value vs ground truth: exact after normalizing case, punctuation, whitespace and
    list order (a single misread character counts as wrong)."""
    v = unwrap(extracted)[0]
    if is_empty(v) or not truth:
        return False
    return fuzz.token_sort_ratio(norm(_as_text(v)), norm(truth)) >= threshold


def grounded(value, text: str, threshold: float = 90) -> bool:
    """Is `value` (fuzzily) present in the parsed text? Guards against paraphrase/hallucination."""
    needle = norm(value)
    return bool(needle) and fuzz.partial_ratio(needle, norm(text)) >= threshold


def valid_date(s) -> bool:
    if not isinstance(s, str) or not s.strip():
        return False
    try:
        dtparser.parse(s, fuzzy=True)
        return True
    except (ValueError, OverflowError):
        return False


def field_report(fields: dict, text: str) -> dict:
    """Per-field fill/grounding/confidence for one message (first message if given a thread)."""
    msgs = as_messages(fields)
    return _field_report_one(msgs[0] if msgs else fields or {}, text)


def field_reports(fields: dict, text: str) -> list[dict]:
    return [_field_report_one(m, text) for m in as_messages(fields)]


def _field_report_one(msg: dict, text: str) -> dict:
    out = {}
    for name in FIELDS:
        v, confs = unwrap((msg or {}).get(name))
        filled = not is_empty(v)
        items = v if isinstance(v, list) else [v]
        out[name] = {"filled": filled,
                     "grounded": all(grounded(i, text) for i in items if not is_empty(i)) if filled else None,
                     "min_conf": min(confs) if confs else None}
    return out


def auto_accept(report: dict, fields: dict, min_conf: float) -> bool:
    """Straight-through-processing rule: core fields filled, grounded in the parse, confident,
    and the date parses. No reported confidence counts as not confident (conservative).
    `fields` may be a thread envelope or a single message."""
    msgs = as_messages(fields)
    msg = msgs[0] if msgs else (fields or {})
    for name in CORE_FIELDS:
        r = report[name]
        if not (r["filled"] and r["grounded"]) or r["min_conf"] is None or r["min_conf"] < min_conf:
            return False
    return valid_date(unwrap(msg.get("sent_date"))[0])


def auto_accept_messages(fields: dict, text: str, min_conf: float) -> list[bool]:
    """Per-message STP on a thread."""
    return [auto_accept(_field_report_one(m, text), m, min_conf) for m in as_messages(fields)]


def _canon(v):
    v = unwrap(v)[0]
    if isinstance(v, list):
        return sorted(norm(i) for i in v if not is_empty(i))
    return "" if is_empty(v) else norm(v)


def _message_agreement(a: dict, b: dict) -> float:
    return sum(_canon(a.get(f)) == _canon(b.get(f)) for f in FIELDS) / len(FIELDS)


def agreement(a: dict, b: dict) -> float:
    """Fraction of fields identical between two extractions (repeat run, or digital vs image).
    Threads are matched greedily; extras and misses count as 0."""
    ma, mb = as_messages(a), as_messages(b)
    if not ma and not mb:
        return 1.0
    matched = match_messages(ma, mb, min_score=0.0)
    scores = [_message_agreement(ma[i], mb[j]) for i, j, _ in matched["pairs"]]
    n = len(scores) + len(matched["extras"]) + len(matched["misses"])
    return (sum(scores) / n) if n else 0.0
