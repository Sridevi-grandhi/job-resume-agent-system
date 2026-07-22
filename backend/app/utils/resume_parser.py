from pathlib import Path

import fitz
from docx import Document


def parse_pdf(file_path: str) -> str:
    text = ""

    with fitz.open(file_path) as pdf_document:
        for page in pdf_document:
            text += page.get_text()

    return text.strip()


def parse_docx(file_path: str) -> str:
    document = Document(file_path)

    paragraphs = []

    for paragraph in document.paragraphs:
        if paragraph.text.strip():
            paragraphs.append(paragraph.text.strip())

    return "\n".join(paragraphs)


def parse_txt(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8", errors="ignore") as file:
        return file.read().strip()


def parse_resume_file(file_path: str) -> str:
    file_extension = Path(file_path).suffix.lower()

    if file_extension == ".pdf":
        return parse_pdf(file_path)

    if file_extension == ".docx":
        return parse_docx(file_path)

    if file_extension == ".txt":
        return parse_txt(file_path)

    raise ValueError("Unsupported file type. Please upload PDF, DOCX, or TXT.")