from pathlib import Path

from bench.corpus import Doc
from bench.runner import Spend, _local_path, append_jsonl, read_jsonl, write_jsonl


def test_spend_math_and_guard():
    s = Spend(max_usd=1.0)
    s.add("parse", 10)
    assert abs(s.usd - 0.10) < 1e-9 and not s.over()
    s.add_record({"classify_pages": 3, "parse_pages": 3, "extract_pages": None})
    assert abs(s.usd - (0.10 + 3 * 0.0075 + 3 * 0.01)) < 1e-9
    s.add("extract", 50)
    assert s.over()


def test_spend_deep_extract_is_double_extract():
    s = Spend(max_usd=10)
    s.add_record({"extract_pages": 10, "deep_extract": True})
    assert abs(s.usd - 0.40) < 1e-9
    s2 = Spend(max_usd=10)
    s2.add_record({"extract_pages": 10})
    assert abs(s2.usd - 0.20) < 1e-9


def test_jsonl_roundtrip(tmp_path):
    p = tmp_path / "x.jsonl"
    assert read_jsonl(p) == []
    append_jsonl(p, [{"a": 1}, {"b": 2}])
    append_jsonl(p, [{"c": 3}])
    assert read_jsonl(p) == [{"a": 1}, {"b": 2}, {"c": 3}]
    write_jsonl(p, [{"z": 1}])
    assert read_jsonl(p) == [{"z": 1}]


def test_local_path_uses_digital_pdf():
    pdf = Path("/tmp/does-not-need-to-exist.pdf")
    d = Doc("D1", pdf, ())
    assert _local_path({"id": "D1", "variant": "digital"}, 3, {"D1": d}) == pdf
    assert _local_path({"id": "NOPE", "variant": "digital"}, 3, {"D1": d}) is None
