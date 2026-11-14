"""Extraction du texte des fichiers de cours, page par page."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

SUPPORTED_EXTENSIONS = (".pdf", ".pptx", ".docx", ".txt", ".md")

# En dessous, une page est considérée comme sans texte exploitable (page scannée, image seule).
MIN_PAGE_CHARS = 30
# Taille des « pages » fabriquées pour les formats qui n'en ont pas (Word, texte).
PSEUDO_PAGE_CHARS = 1500


@dataclass
class Page:
    number: int  # 1 = première page ou diapositive
    text: str


@dataclass
class Extraction:
    pages: list[Page] = field(default_factory=list)
    empty_pages: int = 0  # pages sans texte exploitable
    error: str = ""  # message en français si le fichier n'a pas pu être lu


_SPACES = re.compile(r"[ \t ]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def clean_text(text: str) -> str:
    """Espaces et sauts de ligne normalisés ; les caractères nuls sont retirés."""
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [_SPACES.sub(" ", line).strip() for line in text.split("\n")]
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def _finish(pages: list[Page]) -> Extraction:
    kept = [p for p in pages if len(p.text) >= MIN_PAGE_CHARS]
    return Extraction(pages=kept, empty_pages=len(pages) - len(kept))


def _extract_pdf(path: Path) -> Extraction:
    import pymupdf

    with pymupdf.open(path) as doc:
        if doc.needs_pass:
            return Extraction(error="PDF protégé par un mot de passe")
        pages = [
            Page(i, clean_text(page.get_text("text", sort=True))) for i, page in enumerate(doc, start=1)
        ]
    return _finish(pages)


def _shape_texts(shapes) -> list[str]:
    """Texte des formes d'une diapositive, groupes et tableaux compris."""
    out: list[str] = []
    for shape in shapes:
        if shape.shape_type == 6 and hasattr(shape, "shapes"):  # groupe de formes
            out.extend(_shape_texts(shape.shapes))
            continue
        if getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip():
            out.append(shape.text_frame.text)
        if getattr(shape, "has_table", False):
            for row in shape.table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                if any(cells):
                    out.append(" | ".join(cells))
    return out


def _extract_pptx(path: Path) -> Extraction:
    from pptx import Presentation

    presentation = Presentation(str(path))
    pages: list[Page] = []
    for i, slide in enumerate(presentation.slides, start=1):
        parts = _shape_texts(slide.shapes)
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame
            if notes is not None and notes.text.strip():
                parts.append("Notes : " + notes.text)
        pages.append(Page(i, clean_text("\n".join(parts))))
    return _finish(pages)


def _pseudo_pages(paragraphs: list[str]) -> list[Page]:
    """Regroupe des paragraphes en blocs de taille comparable à une page."""
    pages: list[Page] = []
    current: list[str] = []
    size = 0
    for paragraph in paragraphs:
        if current and size + len(paragraph) > PSEUDO_PAGE_CHARS:
            pages.append(Page(len(pages) + 1, clean_text("\n".join(current))))
            current, size = [], 0
        current.append(paragraph)
        size += len(paragraph) + 1
    if current:
        pages.append(Page(len(pages) + 1, clean_text("\n".join(current))))
    return pages


def _extract_docx(path: Path) -> Extraction:
    from docx import Document

    document = Document(str(path))
    paragraphs = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                paragraphs.append(" | ".join(cells))
    return _finish(_pseudo_pages(paragraphs))


def _extract_text(path: Path) -> Extraction:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    return _finish(_pseudo_pages(paragraphs))


_EXTRACTORS = {
    ".pdf": _extract_pdf,
    ".pptx": _extract_pptx,
    ".docx": _extract_docx,
    ".txt": _extract_text,
    ".md": _extract_text,
}


def extract_file(path: Path) -> Extraction:
    """Extrait le texte d'un fichier de cours.

    Ne lève jamais d'exception : un fichier illisible renvoie une ``Extraction``
    dont ``error`` explique le problème, pour que l'indexation continue.
    """
    extractor = _EXTRACTORS.get(path.suffix.lower())
    if extractor is None:
        return Extraction(error=f"format non pris en charge ({path.suffix or 'sans extension'})")
    try:
        return extractor(path)
    except Exception as exc:  # fichier corrompu, format inattendu...
        return Extraction(error=f"fichier illisible ({type(exc).__name__} : {exc})"[:300])
