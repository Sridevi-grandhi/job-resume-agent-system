"""Convert finalized DOCX to PDF and validate export artifacts.

Canonical path: finalized DOCX → LibreOffice headless → PDF.
Never build PDF content independently from the DOCX.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

import fitz
from fastapi import HTTPException


logger = logging.getLogger(__name__)


def _find_soffice() -> str | None:
    for candidate in (
        shutil.which("soffice"),
        shutil.which("libreoffice"),
        "/usr/bin/soffice",
        "/usr/bin/libreoffice",
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def convert_docx_to_pdf(docx_path: str | Path, destination: str | Path | None = None) -> Path:
    """Convert DOCX to PDF via LibreOffice. Raises HTTPException on failure."""
    source = Path(docx_path)
    if not source.is_file():
        raise HTTPException(status_code=500, detail="Finalized DOCX is missing for PDF export.")
    soffice = _find_soffice()
    if not soffice:
        raise HTTPException(
            status_code=503,
            detail=(
                "PDF export requires LibreOffice (soffice) on the server. "
                "DOCX download remains available."
            ),
        )

    out_dir = Path(tempfile.mkdtemp(prefix="cb-pdf-"))
    try:
        command = [
            soffice,
            "--headless",
            "--nologo",
            "--nofirststartwizard",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out_dir),
            str(source),
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if completed.returncode != 0:
            logger.error(
                "DOCX_PDF_CONVERT_FAILED code=%s stderr=%s",
                completed.returncode,
                (completed.stderr or "")[:400],
            )
            raise HTTPException(status_code=500, detail="DOCX to PDF conversion failed.")
        produced = out_dir / f"{source.stem}.pdf"
        if not produced.is_file():
            pdfs = list(out_dir.glob("*.pdf"))
            if not pdfs:
                raise HTTPException(status_code=500, detail="DOCX to PDF conversion produced no file.")
            produced = pdfs[0]
        target = Path(destination) if destination else produced
        if target != produced:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(produced, target)
        return target
    finally:
        # Keep destination; clean temp conversion dir when different.
        if destination:
            shutil.rmtree(out_dir, ignore_errors=True)


def validate_pdf_export(pdf_path: str | Path, *, expected_name: str | None = None, expected_headings: list[str] | None = None) -> dict:
    """Ensure PDF is non-blank and contains expected identity/section signals."""
    path = Path(pdf_path)
    if not path.is_file():
        raise HTTPException(status_code=500, detail="Generated PDF file is missing.")
    size = path.stat().st_size
    if size < 500:
        raise HTTPException(status_code=500, detail="Generated PDF is empty or too small.")

    try:
        doc = fitz.open(path)
    except Exception as error:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Generated PDF could not be opened: {error}") from error

    try:
        page_count = doc.page_count
        if page_count < 1:
            raise HTTPException(status_code=500, detail="Generated PDF has no pages.")
        text = "\n".join((page.get_text("text") or "") for page in doc)
    finally:
        doc.close()

    compact = re_sub_ws(text)
    if len(compact) < 40:
        raise HTTPException(status_code=500, detail="Generated PDF appears blank (no extractable text).")
    if expected_name and expected_name.split()[0].lower() not in compact.lower():
        # Soft check on first name token
        raise HTTPException(status_code=500, detail="Generated PDF is missing candidate name.")
    if expected_headings:
        lowered = compact.lower()
        if not any(h.lower() in lowered for h in expected_headings if h):
            raise HTTPException(status_code=500, detail="Generated PDF is missing expected section headings.")

    return {
        "page_count": page_count,
        "size_bytes": size,
        "text_chars": len(compact),
        "valid": True,
    }


def re_sub_ws(text: str) -> str:
    import re

    return re.sub(r"\s+", " ", (text or "")).strip()
