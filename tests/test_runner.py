from bench.runner import Spend, append_jsonl, read_jsonl


def test_spend_math_and_guard():
    s = Spend(max_usd=1.0)
    s.add("parse", 10)
    assert abs(s.usd - 0.10) < 1e-9 and not s.over()
    s.add_record({"classify_pages": 3, "parse_pages": 3, "extract_pages": None})
    assert abs(s.usd - (0.10 + 3 * 0.0075 + 3 * 0.01)) < 1e-9
    s.add("extract", 50)
    assert s.over()


def test_jsonl_roundtrip(tmp_path):
    p = tmp_path / "x.jsonl"
    assert read_jsonl(p) == []
    append_jsonl(p, [{"a": 1}, {"b": 2}])
    append_jsonl(p, [{"c": 3}])
    assert read_jsonl(p) == [{"a": 1}, {"b": 2}, {"c": 3}]
