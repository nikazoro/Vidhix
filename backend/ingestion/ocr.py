import re
import unicodedata
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

_SUPPORTED_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "image/png",
    "image/jpeg",
    "image/jpg",
    "text/plain",
}


# ---------------------------------------------------------------------------
# Image preprocessing helpers
# ---------------------------------------------------------------------------

def _preprocess_image(image: "PIL.Image.Image") -> "PIL.Image.Image":  # type: ignore[name-defined]
    """Grayscale → threshold → return cleaned image for better OCR accuracy."""
    from PIL import ImageFilter, ImageOps  # type: ignore

    img = ImageOps.grayscale(image)
    
    def _threshold(x: int) -> int:
        return 0 if x < 128 else 255
    # Simple binarisation threshold
    img = img.point(_threshold, "1")
    img = img.convert("L")

    # Light sharpening to help OCR
    img = img.filter(ImageFilter.SHARPEN)

    return img

# ---------------------------------------------------------------------------
# Extractor implementations
# ---------------------------------------------------------------------------

def _extract_from_pdf(file_path: Path) -> str:
    from pdf2image import convert_from_path  # type: ignore
    import pytesseract  # type: ignore

    log.info("ocr_pdf_start", path=str(file_path))
    pages = convert_from_path(str(file_path), dpi=200)
    page_texts: list[str] = []
    for i, page in enumerate(pages, start=1):
        cleaned = _preprocess_image(page)
        text = pytesseract.image_to_string(cleaned, lang="eng", config="--psm 6")
        page_texts.append(text)
        log.debug("ocr_pdf_page_done", page=i, chars=len(text))
    return "\n\n".join(page_texts)


def _extract_from_image(file_path: Path) -> str:
    from PIL import Image  # type: ignore
    import pytesseract  # type: ignore

    log.info("ocr_image_start", path=str(file_path))
    img = Image.open(file_path)
    cleaned = _preprocess_image(img)
    text = pytesseract.image_to_string(cleaned, lang="eng", config="--psm 6")
    log.debug("ocr_image_done", chars=len(text))
    return text


def _extract_from_docx(file_path: Path) -> str:
    import docx  # type: ignore

    log.info("ocr_docx_start", path=str(file_path))
    doc = docx.Document(str(file_path))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    # Also extract table text
    for table in doc.tables:
        for row in table.rows:
            row_text = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
            if row_text:
                paragraphs.append(row_text)
    return "\n\n".join(paragraphs)


def _extract_from_text(file_path: Path) -> str:
    log.info("ocr_text_start", path=str(file_path))
    return file_path.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Text normalisation
# ---------------------------------------------------------------------------

def _clean_text(text: str) -> str:
    # Normalise unicode
    text = unicodedata.normalize("NFKC", text)
    # Remove null bytes and other control characters except newlines/tabs
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    # Collapse runs of whitespace on a line but preserve paragraph breaks
    lines = text.splitlines()
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in lines]
    # Collapse 3+ blank lines to 2
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return cleaned.strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def extract_text_from_file(file_path: Path, mime_type: str) -> str:
    """
    Extract and clean text from a file.
    Supports: PDF, DOCX, PNG, JPEG, plain text.
    Returns cleaned, normalised text string.
    """
    if mime_type not in _SUPPORTED_MIME_TYPES:
        raise ValueError(
            f"Unsupported MIME type: {mime_type!r}. "
            f"Supported types: {sorted(_SUPPORTED_MIME_TYPES)}"
        )

    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    if mime_type == "application/pdf":
        raw = _extract_from_pdf(file_path)
    elif mime_type in ("image/png", "image/jpeg", "image/jpg"):
        raw = _extract_from_image(file_path)
    elif mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        raw = _extract_from_docx(file_path)
    else:
        raw = _extract_from_text(file_path)

    cleaned = _clean_text(raw)
    log.info(
        "ocr_complete",
        path=str(file_path),
        mime_type=mime_type,
        raw_chars=len(raw),
        clean_chars=len(cleaned),
    )
    return cleaned