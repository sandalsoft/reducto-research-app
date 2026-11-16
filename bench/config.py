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
PRICE_PER_PAGE = {"classify": 7.50 / 1000, "parse": 10.0 / 1000, "extract": 20.0 / 1000}

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

EMAIL_SCHEMA = {
    "type": "object",
    "properties": {
        "sender": {"type": "string", "description": "The From: name and/or address of the first (top) message, exactly as printed"},
        "recipients_to": {"type": "array", "items": {"type": "string"},
                          "description": "Every To: recipient of the first (top) message, exactly as printed"},
        "recipients_cc": {"type": "array", "items": {"type": "string"},
                          "description": "Every Cc: recipient of the first (top) message, exactly as printed"},
        "sent_date": {"type": "string", "description": "The Sent/Date header value of the first (top) message, exactly as printed"},
        "subject": {"type": "string", "description": "The Subject line of the first (top) message, exactly as printed"},
        "body": {"type": "string",
                 "description": "Full body text of the first (top) message, verbatim, excluding headers and footers"},
    },
}
FIELDS = tuple(EMAIL_SCHEMA["properties"])
CORE_FIELDS = ("sender", "sent_date", "subject")  # must be right for a record to be usable
HEADER_FIELDS = ("sender", "recipients_to", "recipients_cc", "sent_date", "subject")  # scorable vs ground truth
EXTRACT_PROMPT = ("These pages are an email or email thread. Describe the first (top) message. "
                  "Copy values exactly as printed; leave a field empty if it is not present.")


def load_env() -> None:
    """Load REDUCTO_API_KEY from the nearest .env above this package (never printed)."""
    load_dotenv(find_dotenv())
