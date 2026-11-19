"""Draw extract citation bounding boxes onto the corpus page JPGs.

Coordinates are Reducto's normalized 0..1 boxes. This is how you see whether a field
came from the outer header, an inner forward, or a table cell — the schema lesson
the scalar extract could not teach."""
from pathlib import Path

from PIL import Image, ImageDraw

from . import checks
from .config import FIELDS, IMAGES_DIR

# One hue per field so a page with many boxes stays readable.
_COLORS = {
    "sender": (220, 50, 47),
    "recipients_to": (38, 139, 210),
    "recipients_cc": (42, 161, 152),
    "sent_date": (181, 137, 0),
    "subject": (108, 113, 196),
    "body": (133, 153, 0),
}


def citation_boxes(fields) -> list[dict]:
    """Flatten every cited bbox on every message into overlay draw records."""
    out = []
    for mi, msg in enumerate(checks.as_messages(fields)):
        for name in FIELDS:
            value, _ = checks.unwrap(msg.get(name))
            for ci, cit in enumerate(checks.citations(msg.get(name))):
                bbox = cit.get("bbox") or {}
                page = bbox.get("original_page") or bbox.get("page")
                if not bbox or not page:
                    continue
                out.append({"message": mi, "field": name, "n": ci, "page": int(page),
                            "bbox": bbox, "type": cit.get("type"),
                            "content": (cit.get("content") or "")[:80],
                            "value": value, "label": f"m{mi}.{name}"})
    return out


def _rect(bbox: dict, w: int, h: int) -> tuple[int, int, int, int]:
    x0 = int(bbox.get("left", 0) * w)
    y0 = int(bbox.get("top", 0) * h)
    x1 = x0 + max(2, int(bbox.get("width", 0) * w))
    y1 = y0 + max(2, int(bbox.get("height", 0) * h))
    return x0, y0, x1, y1


def draw_page(image_path: Path, boxes: list[dict], dest: Path) -> Path:
    im = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(im)
    w, h = im.size
    for b in boxes:
        color = _COLORS.get(b["field"], (0, 0, 0))
        x0, y0, x1, y1 = _rect(b["bbox"], w, h)
        draw.rectangle([x0, y0, x1, y1], outline=color, width=3)
        ty = y0 - 12 if y0 > 12 else y1 + 1
        draw.text((x0, ty), b["label"], fill=color)
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.save(dest)
    return dest


def overlay_record(doc_id: str, variant: str, fields, out_dir: Path,
                   images_dir: Path = IMAGES_DIR) -> list[Path]:
    """Write one JPG per page that has at least one citation. Missing page images are skipped."""
    by_page: dict[int, list] = {}
    for b in citation_boxes(fields):
        by_page.setdefault(b["page"], []).append(b)
    written = []
    for page, boxes in sorted(by_page.items()):
        src = images_dir / f"{doc_id}_page_{page}.jpg"
        if not src.exists():
            continue
        dest = out_dir / doc_id / f"{variant}_p{page}.jpg"
        written.append(draw_page(src, boxes, dest))
    return written


def write_overlays(records: list[dict], out_dir: Path, images_dir: Path = IMAGES_DIR) -> dict:
    """Draw overlays for every successful extract. Returns counts for the report."""
    n_docs, n_pages, n_boxes = 0, 0, 0
    for r in records:
        if r.get("error") or not r.get("fields"):
            continue
        boxes = citation_boxes(r["fields"])
        paths = overlay_record(r["id"], r["variant"], r["fields"], out_dir, images_dir)
        if paths:
            n_docs += 1
            n_pages += len(paths)
            n_boxes += len(boxes)
    index = out_dir / "index.md"
    if n_docs:
        lines = ["# Citation overlays", "",
                 f"{n_boxes} boxes on {n_pages} pages ({n_docs} document-runs).",
                 "Label `m{k}.{field}` is message index k in the extract array.", ""]
        for p in sorted(out_dir.glob("*/*.jpg")):
            rel = p.relative_to(out_dir)
            lines.append(f"- `{rel}`")
        index.write_text("\n".join(lines) + "\n")
    return {"docs": n_docs, "pages": n_pages, "boxes": n_boxes, "dir": str(out_dir)}
