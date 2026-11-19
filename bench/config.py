"""Constants shared across the benchmark: paths, prices, Reducto schemas."""
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
INBOX_DIR = DATA_DIR / "inbox"     # user-supplied digital PDFs (text layer = ground truth); read-only
IMAGES_DIR = DATA_DIR / "images"   # user-supplied page images, <ID>_page_<n>.jpg; read-only
SCAN_DIR = DATA_DIR / "scan_pdfs"  # generated: image-only PDFs built from IMAGES_DIR
OUT_DIR = ROOT / "output"
NOTES_DIR = ROOT / "notes"

# List prices in USD per page: https://docs.reducto.ai/reference/credit-usage.md (effective 2026-09-01).
# The API's `usage.credits` is None on this account, so cost is derived from page counts.
PRICE_PER_PAGE = {
    "classify": 7.50 / 1000,
    "parse": 10.0 / 1000,
    "extract": 20.0 / 1000,
    "deep_extract": 40.0 / 1000,  # agentic extract; 2× standard extract, parse included
}

CLASSIFY_SCHEMA = [
    {"category": "email", "criteria": [
        "Email header block with From, To, Sent/Date and Subject lines",
        "Message body formatted as an email, including replies or forwarded chains"]},
    {"category": "news_article", "criteria": ["Newspaper, magazine or wire-service article, op-ed or newsletter"]},
    {"category": "letter_or_memo", "criteria": ["Formal letter or memorandum with salutation, letterhead or signature"]},
    {"category": "legal_document", "criteria": ["Court filing, subpoena, agreement, affidavit or contract language"]},
    {"category": "financial_record", "criteria": ["Bank statement, invoice, receipt, ledger, wire or account listing"]},
    {"category": "other", "criteria": ["Anything else: lists, notes, schedules, books, manuscripts"]},
]

# Per-message fields. The extract schema is an array of these so a thread is N objects, not one
# "top" email. That is the point of the exercise: schema shape is the accuracy lever.
MESSAGE_PROPERTIES = {
    "sender": {"type": "string",
               "description": "From: name and/or address of this message, exactly as printed"},
    "recipients_to": {"type": "array", "items": {"type": "string"},
                      "description": "Every To: recipient of this message, exactly as printed"},
    "recipients_cc": {"type": "array", "items": {"type": "string"},
                      "description": "Every Cc: recipient of this message, exactly as printed"},
    "sent_date": {"type": "string",
                  "description": "Sent/Date header value of this message, exactly as printed"},
    "subject": {"type": "string",
                "description": "Subject line of this message, exactly as printed"},
    "body": {"type": "string",
             "description": "Body of this message only, verbatim, excluding headers, signatures of other messages, and quoted forwards"},
}
EMAIL_SCHEMA = {
    "type": "object",
    "properties": {
        "messages": {
            "type": "array",
            "description": ("Every distinct email in reading order, outermost first. "
                            "One item per From/Sent/Subject header block; do not merge replies or forwards."),
            "items": {"type": "object", "properties": MESSAGE_PROPERTIES},
        }
    },
}
FIELDS = tuple(MESSAGE_PROPERTIES)  # scored per message, not on the envelope
CORE_FIELDS = ("sender", "sent_date", "subject")  # must be right for a record to be usable
HEADER_FIELDS = ("sender", "recipients_to", "recipients_cc", "sent_date", "subject")  # scorable vs ground truth
EXTRACT_PROMPT = (
    "These pages are an email or email thread. Extract every distinct message as its own array item, "
    "outermost first. A message is one header block (From/To/Sent/Subject) plus the body that belongs "
    "to it. Copy values exactly as printed; leave a field empty if it is not present. "
    "Do not fold a forwarded or replied-to message into the outer one."
)


def load_env() -> None:
    """Load REDUCTO_API_KEY from the nearest .env above this package (never printed)."""
    load_dotenv(find_dotenv())
