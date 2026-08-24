"""Owned editing, rescoring, validation, and final resume download."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import fitz
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.job_model import Job
from app.models.optimized_resume_model import OptimizedResume
from app.models.resume_model import Resume
from app.services.ats_scoring import build_score_view, calculate_weighted_ats_score
from app.services.final_resume_validator import validate_final_resume
from app.services.resume_document_service import (
    document_to_plain_text,
    merge_presentation,
    plain_text_to_document,
)
from app.services.resume_structure_parser import parse_resume_structure
from app.services.skill_confirmation_service import (
    build_canonical_supported_evidence,
    load_confirmed_skills,
)
from app.services.template_preservation_service import OUTPUT_DIR, render_optimized_document


router = APIRouter(prefix="/api/optimizer", tags=["Optimized Resume Editing"])


def _owned(db: Session, user_id: int, job_id: int, optimized_resume_id: int):
    optimized = db.query(OptimizedResume).filter(
        OptimizedResume.id == optimized_resume_id,
        OptimizedResume.user_id == user_id,
        OptimizedResume.job_id == job_id,
    ).first()
    if not optimized:
        raise HTTPException(status_code=404, detail="Optimized resume not found for this user and job.")
    resume = db.query(Resume).filter(Resume.id == optimized.resume_id, Resume.user_id == user_id).first()
    job = db.query(Job).filter(Job.id == job_id, Job.user_id == user_id).first()
    if not resume or not job:
        raise HTTPException(status_code=404, detail="Source resume or selected job was not found.")
    return optimized, resume, job


def _load_stored_document(optimized: OptimizedResume, plain_text: str) -> dict:
    for raw in (optimized.edited_resume_json, optimized.generated_resume_json):
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("document"), dict):
            return parsed["document"]
        if isinstance(parsed, dict) and parsed.get("header") and parsed.get("sections"):
            return parsed
    return plain_text_to_document(plain_text or "")


def _resolve_edited_payload(payload: dict, generated: str, optimized: OptimizedResume) -> tuple[str, dict]:
    if payload.get("action") == "reset":
        text = generated or ""
        return text, plain_text_to_document(text)

    document = payload.get("document")
    if document is None and isinstance(payload.get("edited_resume"), dict):
        candidate = payload["edited_resume"]
        if candidate.get("header") or candidate.get("sections"):
            document = candidate

    if isinstance(document, dict) and (document.get("header") is not None or document.get("sections") is not None):
        # Presentation-only merge: never wipe content when only template fields change.
        existing = _load_stored_document(optimized, generated)
        presentation = {
            key: document.get(key)
            for key in ("templateId", "pageSize", "fontFamily", "fontSize", "spacing", "align")
            if key in document
        }
        if payload.get("presentation_only"):
            document = merge_presentation(existing, presentation)
        text = document_to_plain_text(document)
        if not text.strip():
            raise HTTPException(status_code=400, detail="Edited resume document is empty.")
        return text, document

    value = payload.get("edited_resume_text", payload.get("edited_resume"))
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise HTTPException(status_code=400, detail="Edited resume text is required.")
        return text, plain_text_to_document(text)
    if isinstance(value, dict):
        text = str(value.get("plain_text") or value.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="Edited resume text is required.")
        return text, plain_text_to_document(text)

    raise HTTPException(status_code=400, detail="Edited resume text is required.")


def _score_payload(score: dict) -> dict:
    return build_score_view(score)


def _render_pdf(plain_text: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)  # US Letter
    margin = 54
    rect = fitz.Rect(margin, margin, 612 - margin, 792 - margin)
    page.insert_textbox(
        rect,
        plain_text or "",
        fontsize=10,
        fontname="helv",
        align=fitz.TEXT_ALIGN_LEFT,
    )
    doc.save(destination)
    doc.close()
    return destination


def _truthful_improve(text: str, *, context: str = "bullet") -> str:
    """Conservative rewrite: tighten wording without inventing facts."""
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return cleaned
    # Remove weak fillers; keep tokens intact.
    cleaned = re.sub(r"\b(responsible for|duties included|helped with|worked on)\b", "Delivered", cleaned, flags=re.I)
    cleaned = re.sub(r"\b(various|several|multiple)\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -")
    if context == "summary" and not cleaned.endswith("."):
        cleaned = f"{cleaned}."
    if context == "bullet" and cleaned and cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned


@router.patch("/resume/{user_id}/{job_id}")
def update_optimized_resume(user_id: int, job_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    optimized_id = int(payload.get("optimized_resume_id") or 0)
    optimized, resume, job = _owned(db, user_id, job_id, optimized_id)
    generated = optimized.generated_resume_text or optimized.optimized_text
    edited, document = _resolve_edited_payload(payload, generated, optimized)
    persist = payload.get("persist", True) is not False
    score_only = payload.get("score_only") is True or payload.get("action") == "score"
    skip_score = payload.get("skip_score") is True or payload.get("action") == "autosave"

    original_structure = parse_resume_structure(resume.parsed_text or "")
    result = validate_final_resume(
        original_text=resume.parsed_text or "",
        generated_text=edited,
        original_structure=original_structure,
        generated_sections=parse_resume_structure(edited).get("sections") or [],
        unsupported_keywords=[],
        selected_technologies=[],
    )
    confirmations = load_confirmed_skills(db, user_id=user_id, job_id=job_id)
    score = None
    score_view = None
    if not skip_score:
        supported = build_canonical_supported_evidence(
            verified_resume_skills=[],
            confirmation_rows=confirmations,
        )
        score = calculate_weighted_ats_score(
            edited, job,
            confirmed_skills=supported["scoring_evidence"],
        )
        score_view = _score_payload(score)

    if score_only or not persist:
        return {
            "message": "Score recalculated" if not skip_score else "Preview ready",
            "optimized_resume_id": optimized.id,
            "persisted": False,
            "generated_score": optimized.generated_score,
            "edited_score": (score["match_percentage"] if score else optimized.edited_score),
            "score_change_after_editing": ((score["match_percentage"] if score else optimized.edited_score or 0) - (optimized.generated_score or 0)),
            "validation_passed": result.get("status") == "passed",
            "safe_to_download": result.get("status") == "passed",
            "validation": result,
            "final_text": edited,
            "document": document,
            "score": score_view,
        }

    final_path = None if optimized.final_file_path == optimized.generated_file_path else optimized.final_file_path
    rendered = render_optimized_document(resume.stored_file_path, resume.file_type, edited, output_path=final_path)

    optimized.edited_resume_text = edited
    optimized.final_resume_text = edited
    optimized.edited_resume_json = json.dumps({"document": document, "plain_text": edited})
    if score is not None:
        optimized.edited_score = score["match_percentage"]
    optimized.user_modified = edited != generated
    optimized.last_edited_at = datetime.now(timezone.utc)
    optimized.final_file_path = rendered["file_path"]
    optimized.validation_status = result.get("status")
    db.add(optimized)
    db.commit()
    db.refresh(optimized)
    return {
        "message": "Optimized resume updated successfully",
        "optimized_resume_id": optimized.id,
        "user_modified": optimized.user_modified,
        "persisted": True,
        "generated_score": optimized.generated_score,
        "edited_score": optimized.edited_score,
        "score_change_after_editing": (optimized.edited_score or 0) - (optimized.generated_score or 0),
        "validation_passed": result.get("status") == "passed",
        "safe_to_download": result.get("status") == "passed",
        "validation": result,
        "template_preserved": optimized.template_preserved,
        "source_file_type": optimized.source_file_type,
        "template_note": rendered["template_note"],
        "final_text": optimized.final_resume_text,
        "document": document,
        "score": score_view,
    }


@router.get("/resume/{user_id}/{job_id}/{optimized_resume_id}")
def get_editable_resume(user_id: int, job_id: int, optimized_resume_id: int, db: Session = Depends(get_db)):
    optimized, resume, job = _owned(db, user_id, job_id, optimized_resume_id)
    final_text = optimized.final_resume_text or optimized.edited_resume_text or optimized.generated_resume_text or optimized.optimized_text or ""
    document = _load_stored_document(optimized, final_text)
    confirmations = load_confirmed_skills(db, user_id=user_id, job_id=job_id)
    supported = build_canonical_supported_evidence(
        verified_resume_skills=[],
        confirmation_rows=confirmations,
    )
    score = calculate_weighted_ats_score(
        final_text, job,
        confirmed_skills=supported["scoring_evidence"],
    )
    return {
        "optimized_resume_id": optimized.id,
        "generated_text": optimized.generated_resume_text or optimized.optimized_text,
        "edited_text": optimized.edited_resume_text,
        "final_text": final_text,
        "document": document,
        "generated_score": optimized.generated_score,
        "edited_score": optimized.edited_score if optimized.edited_score is not None else score["match_percentage"],
        "original_score": optimized.original_score,
        "template_preserved": optimized.template_preserved,
        "source_file_type": optimized.source_file_type,
        "user_modified": optimized.user_modified,
        "validation_status": optimized.validation_status,
        "score": _score_payload(score),
    }


@router.post("/resume/{user_id}/{job_id}/improve-snippet")
def improve_snippet(user_id: int, job_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    optimized_id = int(payload.get("optimized_resume_id") or 0)
    optimized, resume, job = _owned(db, user_id, job_id, optimized_id)
    original = str(payload.get("text") or "").strip()
    if not original:
        raise HTTPException(status_code=400, detail="Snippet text is required.")
    context = str(payload.get("context") or "bullet")
    suggested = _truthful_improve(original, context=context)
    # Soft guard: suggested tokens should mostly come from original + resume + JD.
    allow = set(re.findall(r"[A-Za-z0-9+#./-]{2,}", f"{original}\n{resume.parsed_text or ''}\n{job.description or ''}".lower()))
    invented = [
        token for token in re.findall(r"[A-Za-z0-9+#./-]{3,}", suggested.lower())
        if token not in allow and token not in {"delivered", "improved", "optimized", "supported", "enabled"}
    ]
    if invented:
        suggested = original
    return {
        "original": original,
        "suggested": suggested,
        "changed": suggested != original,
        "constrained": True,
        "note": "Suggestion is a wording polish only; no new skills, employers, metrics, or claims were added.",
    }


@router.get("/resume/{user_id}/{job_id}/{optimized_resume_id}/download")
def download_final_resume(
    user_id: int,
    job_id: int,
    optimized_resume_id: int,
    format: str = Query("docx"),
    db: Session = Depends(get_db),
):
    optimized, resume, job = _owned(db, user_id, job_id, optimized_resume_id)
    if optimized.validation_status != "passed":
        raise HTTPException(status_code=400, detail="Final validation must pass before download.")
    plain = optimized.final_resume_text or optimized.edited_resume_text or optimized.generated_resume_text or ""
    name = parse_resume_structure(resume.parsed_text or "").get("name") or "Candidate"
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", f"{name}_{job.title}_Optimized").strip("_") or "Optimized_Resume"
    fmt = (format or "docx").lower()

    if fmt == "pdf":
        destination = OUTPUT_DIR / f"{uuid4()}.pdf"
        _render_pdf(plain, destination)
        return FileResponse(destination, media_type="application/pdf", filename=f"{base}.pdf")

    path = Path(optimized.final_file_path or optimized.generated_file_path or "")
    if not path.is_file():
        # Rebuild DOCX from latest edited text so downloads never lag behind edits.
        rendered = render_optimized_document(resume.stored_file_path, resume.file_type, plain)
        path = Path(rendered["file_path"])
        optimized.final_file_path = str(path)
        db.add(optimized)
        db.commit()
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=f"{base}.docx",
    )
