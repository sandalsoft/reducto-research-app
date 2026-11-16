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
    out = {}
    for name in FIELDS:
        v, confs = unwrap((fields or {}).get(name))
        filled = not is_empty(v)
        items = v if isinstance(v, list) else [v]
        out[name] = {"filled": filled,
                     "grounded": all(grounded(i, text) for i in items if not is_empty(i)) if filled else None,
                     "min_conf": min(confs) if confs else None}
    return out


def auto_accept(report: dict, fields: dict, min_conf: float) -> bool:
    """Straight-through-processing rule: core fields filled, grounded in the parse, confident,
    and the date parses. No reported confidence counts as not confident (conservative)."""
    for name in CORE_FIELDS:
        r = report[name]
        if not (r["filled"] and r["grounded"]) or r["min_conf"] is None or r["min_conf"] < min_conf:
            return False
    return valid_date(unwrap((fields or {}).get("sent_date"))[0])


def _canon(v):
    v = unwrap(v)[0]
    if isinstance(v, list):
        return sorted(norm(i) for i in v if not is_empty(i))
    return "" if is_empty(v) else norm(v)


def agreement(a: dict, b: dict) -> float:
    """Fraction of fields identical between two extractions (repeat run, or digital vs image)."""
    return sum(_canon((a or {}).get(f)) == _canon((b or {}).get(f)) for f in FIELDS) / len(FIELDS)
