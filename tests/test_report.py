import csv

from bench.report import build
from bench.runner import append_jsonl

EMAIL = "From: Jane Roe\nTo: John Doe\nSent: Monday, March 4, 2013 9:15 AM\nSubject: Lunch plans\n\nSee you."


def _fields(subject="Lunch plans", conf=0.95):
    w = lambda v: {"value": v, "citations": [{"confidence": "high",  # noqa: E731
                                               "granular_confidence": {"extract_confidence": conf}}]}
    return {"sender": w("Jane Roe"), "recipients_to": [w("John Doe")], "recipients_cc": [],
            "sent_date": w("Monday, March 4, 2013 9:15 AM"), "subject": w(subject), "body": w("See you.")}


def _run(doc, variant, phase, text, fields=None, category="email", t0=0.0):
    return {"id": doc, "variant": variant, "phase": phase, "error": None, "t0": t0, "t1": t0 + 4,
            "upload_s": 0.5, "classify_s": 1.5, "parse_s": 1.8, "extract_s": 2.0 if fields else None,
            "category": category, "confidences": {category: 1.0}, "classify_pages": 1, "parse_pages": 1,
            "extract_pages": 1 if fields else None, "text": text, "fields": fields, "studio_link": "s"}


def test_build_report_end_to_end(tmp_path):
    out, notes = tmp_path / "output", tmp_path / "notes"
    out.mkdir()
    notes.mkdir()
    (notes / "friction_log.md").write_text("# Friction log\n- example")
    hdr = {"sender": "Jane Roe", "recipients_to": "John Doe", "recipients_cc": None,
           "sent_date": "Monday, March 4, 2013 9:15 AM", "subject": "Lunch plans"}
    append_jsonl(out / "ground_truth.jsonl", [
        {"id": "E1", "text": EMAIL, "is_email": True, "headers": hdr, "pages_total": 1, "k_pages": 1},
        {"id": "N1", "text": "A news article about trade.", "is_email": False,
         "headers": dict.fromkeys(hdr), "pages_total": 1, "k_pages": 1}])
    append_jsonl(out / "results.jsonl", [
        _run("E1", "digital", "baseline", EMAIL, _fields()),
        _run("E1", "image", "load", EMAIL.replace("Lunch", "Lnch"), _fields("Lnch plans", 0.7), t0=1),
        _run("N1", "digital", "load", "A news article about trade.", category="news_article", t0=2),
        {"id": "N1", "variant": "image", "phase": "load", "error": {"stage": "parse", "type": "APIError", "status": 500}}])
    append_jsonl(out / "repeat.jsonl", [{"id": "E1", "variant": "image", "error": None, "fields": _fields("Lnch plans")}])

    report = build(out, notes).read_text()
    for heading in ("## 1. Run summary", "## 2. Performance", "### 3a.", "### 3b.", "### 3c.", "## 4. Business fit",
                    "## 5. Ease of use", "## 6. Limitations", "# Friction log"):
        assert heading in report
    assert "parse/APIError/500: 1" in report
    rows = list(csv.DictReader((out / "emails.csv").open()))
    assert [r["id"] for r in rows] == ["E1"] and rows[0]["core_matches_ground_truth"] == "False"
    assert rows[0]["msg_index"] == "0" and rows[0]["n_messages"] == "1"
    assert "Thread extraction" in report


def _thread_fields():
    w = lambda v: {"value": v, "citations": [{"confidence": "high",  # noqa: E731
                                               "granular_confidence": {"extract_confidence": 0.95},
                                               "bbox": {"left": 0.1, "top": 0.1, "width": 0.2, "height": 0.05,
                                                        "page": 1}}]}
    return {"messages": [
        {"sender": w("Jane Roe"), "recipients_to": [w("John Doe")], "recipients_cc": [],
         "sent_date": w("Monday, March 4, 2013 9:15 AM"), "subject": w("Lunch plans"), "body": w("See you.")},
        {"sender": w("John Doe"), "recipients_to": [w("Jane Roe")], "recipients_cc": [],
         "sent_date": w("Monday, March 4, 2013 10:00 AM"), "subject": w("Re: Lunch plans"), "body": w("OK.")},
    ]}


def test_build_report_thread_schema_explodes_csv(tmp_path):
    out, notes, images = tmp_path / "output", tmp_path / "notes", tmp_path / "images"
    out.mkdir()
    notes.mkdir()
    images.mkdir()
    (notes / "friction_log.md").write_text("# Friction log\n- example")
    thread = ("From: Jane Roe\nTo: John Doe\nSent: Monday, March 4, 2013 9:15 AM\nSubject: Lunch plans\n\nSee you.\n\n"
              "From: John Doe\nTo: Jane Roe\nSent: Monday, March 4, 2013 10:00 AM\nSubject: Re: Lunch plans\n\nOK.")
    hdr = {"sender": "Jane Roe", "recipients_to": "John Doe", "recipients_cc": None,
           "sent_date": "Monday, March 4, 2013 9:15 AM", "subject": "Lunch plans"}
    append_jsonl(out / "ground_truth.jsonl", [
        {"id": "E1", "text": thread, "is_email": True, "headers": hdr, "pages_total": 1, "k_pages": 1}])
    append_jsonl(out / "results.jsonl", [_run("E1", "image", "load", thread, _thread_fields())])
    from PIL import Image
    Image.new("RGB", (100, 80), (255, 255, 255)).save(images / "E1_page_1.jpg")
    report = build(out, notes, images).read_text()
    assert "gt messages" in report and "Deep Extract" in report
    rows = list(csv.DictReader((out / "emails.csv").open()))
    assert [r["msg_index"] for r in rows] == ["0", "1"]
    assert rows[0]["matched_gt"] == "True" and rows[1]["matched_gt"] == "True"
    assert (out / "overlays" / "E1" / "image_p1.jpg").exists()
