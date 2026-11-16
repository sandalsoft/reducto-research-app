from bench import checks

TEXT = """From: Jane Roe
To: John Doe; Ann Smith
Sent: Monday, March 4, 2013 9:15 AM
Subject: Lunch plans

Let's meet at noon on Thursday to talk about the project."""


def test_looks_like_email_needs_two_headers():
    assert checks.looks_like_email(TEXT)
    assert not checks.looks_like_email("Subject to the terms of this agreement")
    assert not checks.looks_like_email("")


def test_header_fields_first_same_line_value_wins():
    text = "From:\nJane Roe\nDate: Tue, 5 Mar 2013\nSubject: Hi\nTo: A\nSubject: Second"
    gt = checks.header_fields(text)
    assert gt["sender"] is None  # value not on the same line -> not scorable
    assert gt["sent_date"] == "Tue, 5 Mar 2013" and gt["subject"] == "Hi" and gt["recipients_to"] == "A"
    assert gt["recipients_cc"] is None


def test_field_match_handles_lists_order_and_punctuation():
    assert checks.field_match(["Ann Smith", "John Doe"], "John Doe; Ann Smith")
    assert checks.field_match("Lunch plans.", "Lunch plans")
    assert not checks.field_match("Dinner", "Lunch plans")
    assert not checks.field_match("Lnch plans", "Lunch plans")  # one misread char is wrong
    assert not checks.field_match(None, "Lunch plans")
    assert checks.field_match({"value": "Jane Roe", "citations": []}, "Jane Roe")


def test_clean_markdown_and_error_rates():
    md = "# Title\n\n**Bold** text | cell |\n|---|---|\n<br>more   text"
    assert checks.clean(md) == "Title Bold text cell more text"
    assert checks.cer("abcd", "abcd") == 0.0
    assert checks.cer("abxd", "abcd") == 0.25
    assert checks.wer("the cat sat", "the cat sat on") == 0.25
    assert checks.word_recall("sat cat the", "the cat sat") == 1.0  # order-insensitive
    assert checks.word_recall("", "the cat") == 0.0


def test_clean_unescapes_html_entities():
    assert checks.clean("GPS &lt; Subject &amp; more") == "GPS < Subject & more"


def test_clean_keeps_angle_bracket_addresses_and_quoted_replies():
    text = "From: Jane <jane@x.com>\nSent: Monday\n\n> quoted reply line\n> more"
    assert checks.clean(text) == "From: Jane <jane@x.com> Sent: Monday > quoted reply line > more"
    assert checks.clean("a<br/>b<td colspan=2>c</td>") == "a b c"


def test_field_match_ignores_underscore_rules():
    assert checks.field_match("LHS", "LHS________________________________")


def test_unwrap_prefers_granular_extract_confidence():
    field = {"value": "x", "citations": [{"confidence": "high",
                                          "granular_confidence": {"extract_confidence": 0.92, "parse_confidence": 1.0}}]}
    assert checks.unwrap(field) == ("x", [0.92])


def test_unwrap_list_of_wrapped_items():
    field = [{"value": "John Doe", "citations": [{"granular_confidence": {"extract_confidence": 0.8}}]},
             {"value": "Ann Smith", "citations": []}]
    v, c = checks.unwrap(field)
    assert v == ["John Doe", "Ann Smith"] and c == [0.8]
    assert checks.field_match(field, "Ann Smith; John Doe")
    assert checks.agreement({"recipients_to": field}, {"recipients_to": ["ann smith", "John Doe"]}) == 1.0


def test_prf():
    m = checks.prf([True, True, False, False], [True, False, True, False])
    assert m["tp"] == 1 and m["fp"] == 1 and m["fn"] == 1
    assert m["precision"] == 0.5 and m["recall"] == 0.5 and m["accuracy"] == 0.5


def test_grounded_and_unwrap():
    assert checks.grounded("Lunch plans", TEXT)
    assert not checks.grounded("Quarterly budget review", TEXT)
    v, c = checks.unwrap({"value": "x", "citations": [{"confidence": 0.9}, {"confidence": "low"}]})
    assert v == "x" and c == [0.9, 0.0]
    assert checks.unwrap(None) == (None, [])


def _fields(**over):
    base = {
        "sender": {"value": "Jane Roe", "citations": [{"confidence": 0.95}]},
        "recipients_to": {"value": ["John Doe", "Ann Smith"], "citations": [{"confidence": 0.9}]},
        "recipients_cc": {"value": [], "citations": []},
        "sent_date": {"value": "Monday, March 4, 2013 9:15 AM", "citations": [{"confidence": 0.9}]},
        "subject": {"value": "Lunch plans", "citations": [{"confidence": 0.92}]},
        "body": {"value": "Let's meet at noon on Thursday", "citations": [{"confidence": 0.9}]},
    }
    base.update(over)
    return base


def test_auto_accept_rules():
    f = _fields()
    rows = checks.field_report(f, TEXT)
    assert rows["recipients_cc"]["filled"] is False
    assert checks.auto_accept(rows, f, 0.8)
    assert not checks.auto_accept(rows, f, 0.99)
    bad = _fields(subject={"value": "Totally invented", "citations": [{"confidence": 0.99}]})
    assert not checks.auto_accept(checks.field_report(bad, TEXT), bad, 0.8)
    noconf = _fields(subject={"value": "Lunch plans", "citations": []})
    assert not checks.auto_accept(checks.field_report(noconf, TEXT), noconf, 0.8)


def test_agreement_ignores_case_whitespace_and_list_order():
    a = _fields()
    b = _fields(subject={"value": "  lunch   PLANS ", "citations": []},
                recipients_to={"value": ["Ann Smith", "John Doe"], "citations": []})
    assert checks.agreement(a, b) == 1.0
    c = _fields(subject={"value": "Other", "citations": []})
    assert abs(checks.agreement(a, c) - 5 / 6) < 1e-9
