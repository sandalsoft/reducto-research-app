from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader

from bench.corpus import Doc, build_scan_pdf, discover, sample
from bench.stats import percentile, summarize


def test_percentile_and_summarize():
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile([5], 95) == 5
    assert summarize([]) == {"n": 0}
    s = summarize([1.0, 2.0, 3.0, None])
    assert s["n"] == 3 and s["p50"] == 2.0 and s["max"] == 3.0


def _make(tmp_path: Path, stem: str, pages: int):
    inbox, images = tmp_path / "inbox", tmp_path / "images"
    inbox.mkdir(exist_ok=True)
    images.mkdir(exist_ok=True)
    (inbox / f"{stem}.txt.pdf").write_bytes(b"%PDF-1.4 stub")
    for n in range(1, pages + 1):
        Image.new("RGB", (40, 50), (255, 255, 255)).save(images / f"{stem}_page_{n}.jpg")
    return inbox, images


def test_discover_pairs_pdfs_with_numerically_ordered_pages(tmp_path):
    inbox, images = _make(tmp_path, "HOUSE_OVERSIGHT_000001", 11)
    _make(tmp_path, "HOUSE_OVERSIGHT_000002", 2)
    docs = discover(inbox, images)
    assert [d.id for d in docs] == ["HOUSE_OVERSIGHT_000001", "HOUSE_OVERSIGHT_000002"]
    assert [p.name for p in docs[0].images[:3]] == [
        "HOUSE_OVERSIGHT_000001_page_1.jpg", "HOUSE_OVERSIGHT_000001_page_2.jpg",
        "HOUSE_OVERSIGHT_000001_page_3.jpg"]
    assert docs[0].images[-1].name.endswith("_page_11.jpg")


def test_discover_rejects_pdf_without_images(tmp_path):
    inbox, images = _make(tmp_path, "HOUSE_OVERSIGHT_000001", 1)
    (inbox / "HOUSE_OVERSIGHT_000009.txt.pdf").write_bytes(b"%PDF")
    with pytest.raises(ValueError, match="000009"):
        discover(inbox, images)


def test_sample_is_seeded_and_capped():
    docs = [Doc(f"D{i}", Path(f"{i}.pdf"), ()) for i in range(10)]
    assert sample(docs, 5, 1) == sample(docs, 5, 1)
    assert sample(docs, 5, 1) != sample(docs, 5, 2)
    assert len(sample(docs, 50, 1)) == 10


def test_build_scan_pdf_is_image_only_and_page_capped(tmp_path):
    inbox, images = _make(tmp_path, "HOUSE_OVERSIGHT_000001", 5)
    doc = discover(inbox, images)[0]
    out = build_scan_pdf(doc, 3, tmp_path / "scans")
    reader = PdfReader(str(out))
    assert len(reader.pages) == 3
    assert all(not (p.extract_text() or "").strip() for p in reader.pages)
