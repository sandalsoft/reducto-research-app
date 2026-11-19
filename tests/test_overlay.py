from pathlib import Path

from PIL import Image

from bench.overlay import citation_boxes, draw_page, overlay_record, write_overlays


def _msg(sender="Jane"):
    return {"sender": {"value": sender, "citations": [
        {"type": "Key Value", "content": f"From: {sender}",
         "bbox": {"left": 0.1, "top": 0.1, "width": 0.4, "height": 0.1, "page": 1, "original_page": 1}}]},
            "subject": {"value": "Hi", "citations": [
                {"bbox": {"left": 0.1, "top": 0.4, "width": 0.5, "height": 0.1, "page": 1}}]}}


def test_citation_boxes_label_message_index():
    fields = {"messages": [_msg("A"), _msg("B")]}
    boxes = citation_boxes(fields)
    assert [b["label"] for b in boxes] == ["m0.sender", "m0.subject", "m1.sender", "m1.subject"]
    assert boxes[0]["page"] == 1 and boxes[0]["field"] == "sender"


def test_draw_page_writes_jpeg(tmp_path: Path):
    src = tmp_path / "in.jpg"
    Image.new("RGB", (80, 60), (255, 255, 255)).save(src)
    dest = tmp_path / "out.jpg"
    boxes = [{"field": "sender", "label": "m0.sender",
              "bbox": {"left": 0.1, "top": 0.1, "width": 0.5, "height": 0.2}}]
    draw_page(src, boxes, dest)
    assert dest.exists() and Image.open(dest).size == (80, 60)


def test_write_overlays_skips_missing_images_and_counts(tmp_path: Path):
    images, out = tmp_path / "images", tmp_path / "overlays"
    images.mkdir()
    Image.new("RGB", (40, 40), (255, 255, 255)).save(images / "D1_page_1.jpg")
    recs = [
        {"id": "D1", "variant": "image", "error": None, "fields": {"messages": [_msg()]}},
        {"id": "MISSING", "variant": "digital", "error": None, "fields": {"messages": [_msg()]}},
        {"id": "ERR", "variant": "image", "error": {"stage": "extract"}, "fields": None},
    ]
    stats = write_overlays(recs, out, images)
    assert stats["docs"] == 1 and stats["pages"] == 1 and stats["boxes"] == 2
    assert (out / "D1" / "image_p1.jpg").exists()
    assert overlay_record("MISSING", "digital", recs[1]["fields"], out, images) == []
