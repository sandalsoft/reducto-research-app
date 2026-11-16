"""The corpus: user-supplied digital PDFs (inbox/) paired with page images (images/).
The PDF text layer is exact ground truth for what an image of the same page says."""
import random
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from pypdf import PdfReader

_PAGE = re.compile(r"_page_(\d+)\.jpg$")


@dataclass(frozen=True)
class Doc:
    id: str
    pdf: Path
    images: tuple[Path, ...]  # ordered by page number


def discover(inbox: Path, images_dir: Path) -> list[Doc]:
    docs, missing = [], []
    for pdf in sorted(inbox.glob("*.pdf")):
        doc_id = pdf.name.split(".")[0]
        pages = sorted(images_dir.glob(f"{doc_id}_page_*.jpg"), key=lambda p: int(_PAGE.search(p.name).group(1)))
        if not pages:
            missing.append(doc_id)
        docs.append(Doc(doc_id, pdf, tuple(pages)))
    if missing:
        raise ValueError(f"no page images for: {', '.join(missing)}")
    return docs


def sample(docs: list[Doc], n: int, seed: int) -> list[Doc]:
    return random.Random(seed).sample(docs, min(n, len(docs)))


def ground_truth_pages(pdf: Path, k: int) -> list[str]:
    return [p.extract_text() or "" for p in PdfReader(str(pdf)).pages[:k]]


def build_scan_pdf(doc: Doc, k: int, out_dir: Path) -> Path:
    """Image-only PDF of the first k page images: the 'scanned' twin of the digital PDF."""
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{doc.id}.p{k}.scan.pdf"
    if not out.exists():
        imgs = [Image.open(p).convert("RGB") for p in doc.images[:k]]
        tmp = out.with_suffix(".part")
        imgs[0].save(tmp, format="PDF", save_all=True, append_images=imgs[1:], resolution=100)
        tmp.replace(out)
    return out
