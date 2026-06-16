from __future__ import annotations

import re
import os

_cached_text: str | None = None


def parse_resume(pdf_path: str | None = None) -> str:
    global _cached_text
    if _cached_text is not None:
        return _cached_text

    if pdf_path is None:
        pdf_path = os.path.expanduser("~/job-agent/config/resume_master.pdf")

    try:
        import pypdf
        reader = pypdf.PdfReader(pdf_path)
        pages = [page.extract_text() or "" for page in reader.pages]
    except ImportError:
        from PyPDF2 import PdfReader
        reader = PdfReader(pdf_path)
        pages = [page.extract_text() or "" for page in reader.pages]

    text = "\n".join(pages)
    # Strip excessive whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)
    text = text.strip()
    _cached_text = text
    return text
