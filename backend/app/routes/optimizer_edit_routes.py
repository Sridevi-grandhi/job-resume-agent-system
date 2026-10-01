"""Owned editing, rescoring, validation, and final resume download."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.job_model import Job
from app.models.optimized_resume_model import OptimizedResume
from app.models.resume_model import Resume
from app.services.ats_scoring import build_score_view, calculate_weighted_ats_score
from app.services.ats_resume_generation_service import rewrite_summary_with_llm
from app.services.bullet_rewrite_service import guard_invented_tokens, is_near_identical, rewrite_bullet_with_ollama
from app.services.final_resume_validator import validate_final_resume
from app.services.resume_document_service import (
    document_sections_for_validation,
    document_to_plain_text,
    download_basename,
    merge_presentation,
    plain_text_to_document,
    sanitize_resume_public_text,
)
from app.services.resume_structure_parser import parse_resume_structure
from app.services.skill_confirmation_service import (
    build_canonical_supported_evidence,
    build_effective_evidence_decisions,
    load_effective_evidence_for_job,
)
from app.services.docx_pdf_export import convert_docx_to_pdf, validate_pdf_export
from app.services.template_preservation_service import OUTPUT_DIR, render_optimized_document


router = APIRouter(prefix="/api/optimizer", tags=["Optimized Resume Editing"])
logger = logging.getLogger(__name__)


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


def _score_payload(score: dict, evidence_decisions: dict[str, str] | None = None) -> dict:
    view = build_score_view(score)
    decisions = evidence_decisions or {}
    warnings = []
    user_confirmed_statuses = {
        "USER_CONFIRMED_PROFESSIONAL",
        "USER_CONFIRMED_PROJECT",
        "USER_CONFIRMED_CERTIFICATION",
        "USER_CONFIRMED_TRAINING",
    }

    def _decision_for(skill: str) -> str:
        from app.services.keyword_utils import normalize_keyword
        from app.services.skill_analysis_service import normalize_skill_name

        candidates = {
            skill,
            skill.lower(),
            normalize_skill_name(skill),
            normalize_keyword(skill),
            str(normalize_keyword(skill)).lower(),
        }
        for alias in candidates:
            if alias in decisions:
                return decisions[alias]
        return ""

    for skill in view.get("rejected_present_requirements") or []:
        warnings.append(f"{skill} — Rejected in Step 3 · No ATS credit")
    for skill in view.get("confirmed_present_requirements") or view.get("confirmed_skills_used") or []:
        status = _decision_for(skill)
        if status in user_confirmed_statuses:
            continue
    for skill in view.get("unverified_present_requirements") or []:
        status = _decision_for(skill)
        if status in user_confirmed_statuses:
            continue
        if status in {"REJECTED", "NOT_POSSESSED"}:
            warnings.append(f"{skill} — Rejected in Step 3 · No ATS credit")
            continue
        warnings.append(f"{skill} — Unverified manual skill · Confirm evidence to receive ATS credit")
    view["skill_credit_warnings"] = warnings
    return view


def _load_scoring_evidence(db: Session, *, user_id: int, job_id: int, resume_id: int, optimized):
    confirmations = load_effective_evidence_for_job(
        db,
        user_id=user_id,
        job_id=job_id,
        resume_id=resume_id,
        workflow_run_id=getattr(optimized, "workflow_run_id", None),
    )
    supported = build_canonical_supported_evidence(
        verified_resume_skills=[],
        confirmation_rows=confirmations,
    )
    decisions = build_effective_evidence_decisions(confirmations)
    return confirmations, supported, decisions


def _render_pdf(plain_text: str, destination: Path) -> Path:
    """Deprecated independent PDF builder — kept only as last-resort unreachable fallback."""
    raise RuntimeError("Independent PDF rendering is disabled; convert from finalized DOCX.")


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


def _safe_to_download(validation_status: str | None) -> bool:
    return (validation_status or "") in {"passed", "passed_with_warnings"}


def _document_version(text: str) -> str:
    """Stable content-based version for the editor/export consistency
    guard — the same text always yields the same version, so the editor,
    recalculate, and both download formats can be verified to agree on
    exactly which content they're each looking at."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _score_response_extras(
    *,
    previous_score: int | None,
    new_score: int | None,
    score_view: dict | None,
) -> dict:
    prev = previous_score if previous_score is not None else None
    current = new_score if new_score is not None else prev
    unchanged = (
        prev is not None
        and current is not None
        and int(prev) == int(current)
    )
    message = None
    if unchanged:
        message = (
            f"Score remains {int(current)}%. "
            "Your edits did not change any scored ATS requirements."
        )
    return {
        "previous_edited_score": prev,
        "score_unchanged": unchanged,
        "score_unchanged_reason": message,
        "score_breakdown": (score_view or {}).get("component_scores") if score_view else None,
    }


@router.patch("/resume/{user_id}/{job_id}")
def update_optimized_resume(user_id: int, job_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    optimized_id = int(payload.get("optimized_resume_id") or 0)
    optimized, resume, job = _owned(db, user_id, job_id, optimized_id)
    generated = optimized.generated_resume_text or optimized.optimized_text
    edited, document = _resolve_edited_payload(payload, generated, optimized)
    edited = sanitize_resume_public_text(edited)
    persist = payload.get("persist", True) is not False
    # Explicit score_only flag only — action=score/recalculate may still persist.
    score_only = payload.get("score_only") is True
    skip_score = payload.get("skip_score") is True or payload.get("action") == "autosave"
    action = str(payload.get("action") or "").lower()
    if action in {"score", "recalculate"}:
        skip_score = False

    original_structure = parse_resume_structure(resume.parsed_text or "")
    edited_structure = parse_resume_structure(edited)
    generated_sections = document_sections_for_validation(document) or (
        edited_structure.get("sections") or []
    )
    confirmations, supported, evidence_decisions = _load_scoring_evidence(
        db, user_id=user_id, job_id=job_id, resume_id=resume.id, optimized=optimized,
    )
    confirmed_text = "\n".join(
        str(getattr(row, "usage_description", None) or getattr(row, "work_completed", None) or "")
        for row in (confirmations or [])
    )
    result = validate_final_resume(
        original_text=resume.parsed_text or "",
        generated_text=edited,
        original_structure=original_structure,
        generated_sections=generated_sections,
        unsupported_keywords=[],
        selected_technologies=[],
        allowed_confirmed_skills=supported.get("confirmed_skill_names") or [],
        confirmed_evidence_text=confirmed_text,
    )
    previous_score = optimized.edited_score if optimized.edited_score is not None else optimized.generated_score
    score = None
    score_view = None
    if not skip_score:
        recalc_logger = logging.getLogger(__name__)
        document_hash = _document_version(edited)
        denied = list(supported.get("denied_skill_names") or [])
        recalc_logger.info(
            "ATS_RECALCULATE_START optimized_resume_id=%s job_id=%s resume_id=%s "
            "current_document_hash=%s evidence_gated=true",
            optimized.id,
            job_id,
            resume.id,
            document_hash,
        )
        score = calculate_weighted_ats_score(
            edited,
            job,
            confirmed_skills=supported["scoring_evidence"],
            original_resume_text=resume.parsed_text or "",
            denied_skills=denied,
            evidence_gated=True,
        )
        score_view = _score_payload(score, evidence_decisions)
        recalc_logger.info(
            "ATS_RECALCULATE_EVIDENCE confirmed_present=%s rejected_present=%s unverified_present=%s",
            score.get("confirmed_present_requirements") or score.get("confirmed_skills_used") or [],
            score.get("rejected_present_requirements") or [],
            score.get("unverified_present_requirements") or [],
        )
        recalc_logger.info(
            "ATS_RECALCULATE_COMPLETE optimized_resume_id=%s original_score=%s current_score=%s "
            "document_hash=%s",
            optimized.id,
            optimized.original_score,
            score.get("match_percentage"),
            document_hash,
        )

    extras = _score_response_extras(
        previous_score=previous_score,
        new_score=(score["match_percentage"] if score else None),
        score_view=score_view,
    )

    if score_only or not persist:
        return {
            "message": extras["score_unchanged_reason"] or (
                "Score recalculated" if not skip_score else "Preview ready"
            ),
            "optimized_resume_id": optimized.id,
            "persisted": False,
            "generated_score": optimized.generated_score,
            "edited_score": (score["match_percentage"] if score else optimized.edited_score),
            "score_change_after_editing": ((score["match_percentage"] if score else optimized.edited_score or 0) - (optimized.generated_score or 0)),
            "validation_passed": result.get("status") in {"passed", "passed_with_warnings"},
            "safe_to_download": _safe_to_download(result.get("status")),
            "validation": result,
            "final_text": edited,
            "document_version": _document_version(edited),
            "document": document,
            "score": score_view,
            **extras,
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
    document_version = _document_version(optimized.final_resume_text)
    save_logger = logging.getLogger(__name__)
    save_logger.info(
        "ATS_EDITOR_SAVE optimized_resume_id=%s resume_id=%s job_id=%s document_hash=%s persist=%s",
        optimized.id,
        resume.id,
        job_id,
        document_version,
        persist,
    )
    return {
        "message": extras["score_unchanged_reason"] or "Optimized resume updated successfully",
        "optimized_resume_id": optimized.id,
        "user_modified": optimized.user_modified,
        "persisted": True,
        "generated_score": optimized.generated_score,
        "edited_score": optimized.edited_score,
        "score_change_after_editing": (optimized.edited_score or 0) - (optimized.generated_score or 0),
        "validation_passed": result.get("status") in {"passed", "passed_with_warnings"},
        "safe_to_download": _safe_to_download(result.get("status")),
        "validation": result,
        "template_preserved": optimized.template_preserved,
        "source_file_type": optimized.source_file_type,
        "template_note": rendered["template_note"],
        "final_text": optimized.final_resume_text,
        "document_version": document_version,
        "document": document,
        "score": score_view,
        **extras,
    }


@router.get("/resume/{user_id}/{job_id}/{optimized_resume_id}")
def get_editable_resume(user_id: int, job_id: int, optimized_resume_id: int, db: Session = Depends(get_db)):
    optimized, resume, job = _owned(db, user_id, job_id, optimized_resume_id)
    final_text = optimized.final_resume_text or optimized.edited_resume_text or optimized.generated_resume_text or optimized.optimized_text or ""
    document = _load_stored_document(optimized, final_text)
    confirmations, supported, evidence_decisions = _load_scoring_evidence(
        db, user_id=user_id, job_id=job_id, resume_id=resume.id, optimized=optimized,
    )
    score = calculate_weighted_ats_score(
        final_text, job,
        confirmed_skills=supported["scoring_evidence"],
        original_resume_text=resume.parsed_text or "",
        denied_skills=list(supported.get("denied_skill_names") or []),
        evidence_gated=True,
    )
    current_score = score["match_percentage"]
    return {
        "optimized_resume_id": optimized.id,
        "generated_text": optimized.generated_resume_text or optimized.optimized_text,
        "edited_text": optimized.edited_resume_text,
        "final_text": final_text,
        "document_version": _document_version(final_text),
        "document": document,
        "generated_score": optimized.generated_score,
        "edited_score": current_score,
        "original_score": optimized.original_score,
        "template_preserved": optimized.template_preserved,
        "source_file_type": optimized.source_file_type,
        "user_modified": optimized.user_modified,
        "validation_status": optimized.validation_status,
        "safe_to_download": _safe_to_download(optimized.validation_status),
        "score": _score_payload(score, evidence_decisions),
    }


@router.post("/resume/{user_id}/{job_id}/improve-snippet")
def improve_snippet(user_id: int, job_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    """Bullet-level "Try Again" is Ollama-backed (see bullet_rewrite_service);
    Professional Summary "Try Again" uses the full-generation OpenAI client
    instead (see rewrite_summary_with_llm) — a multi-sentence rewrite is a
    bigger task than one bullet. Never returns a blank suggestion: any
    failure/empty/malformed LLM result falls back to a deterministic rewrite,
    and if even that produces nothing usable, the original text is returned
    unchanged with retry_failed=True so the frontend can keep the previous
    suggestion visible instead of blanking it. When the LLM is reachable but
    genuinely has no meaningfully different improvement to offer (even after
    one retry), no_change_recommended=True is returned instead of presenting
    a near-identical rewrite as an "AI improvement"."""
    optimized_id = int(payload.get("optimized_resume_id") or 0)
    optimized, resume, job = _owned(db, user_id, job_id, optimized_id)
    original = str(payload.get("text") or "").strip()
    if not original:
        raise HTTPException(status_code=400, detail="Snippet text is required.")
    context = str(payload.get("context") or "bullet")
    previous_suggestion = str(payload.get("previous_suggestion") or "").strip()
    resume_text = resume.parsed_text or ""
    job_description = job.description or ""

    confirmations, supported, _decisions = _load_scoring_evidence(
        db, user_id=user_id, job_id=job_id, resume_id=resume.id, optimized=optimized,
    )
    allowed_terms = sorted(set(supported.get("confirmed_skill_names") or []))

    if context == "summary":
        full_resume_text = (
            optimized.edited_resume_text or optimized.generated_resume_text or optimized.optimized_text or ""
        )
        suggested, provider_label, no_change_recommended = rewrite_summary_with_llm(
            current_summary=original,
            full_resume_text=full_resume_text,
            original_resume_text=resume_text,
            job=job,
            confirmed_skills=supported.get("scoring_evidence"),
            allowed_terms=allowed_terms,
            denied_skills=list(supported.get("denied_skill_names") or []),
            previous_suggestion=previous_suggestion,
        )
    else:
        suggested, provider_label, no_change_recommended = rewrite_bullet_with_ollama(
            original=original,
            context=context,
            job_title=job.title or "",
            job_description=job_description,
            allowed_terms=allowed_terms,
            previous_suggestion=previous_suggestion,
        )

    used_llm = suggested is not None

    if suggested is None and no_change_recommended:
        logger.info(
            "%s_NO_CHANGE_RECOMMENDED optimized_resume_id=%s context=%s",
            "SUMMARY" if context == "summary" else "BULLET", optimized.id, context,
        )
        suggested = previous_suggestion or original
    elif suggested is None:
        logger.info("BULLET_FALLBACK_USED optimized_resume_id=%s context=%s", optimized.id, context)
        suggested = _truthful_improve(original, context=context)

    suggested = guard_invented_tokens(
        suggested, original=original, resume_text=resume_text, job_description=job_description,
    )

    retry_failed = False
    if not no_change_recommended and previous_suggestion and is_near_identical(suggested, previous_suggestion):
        # Neither the LLM nor the deterministic rewrite produced anything new —
        # keep the previous suggestion rather than silently "changing" nothing.
        retry_failed = True
        suggested = previous_suggestion
        used_llm = False
    elif not no_change_recommended and not previous_suggestion and is_near_identical(suggested, original):
        # A FRESH request (no previous_suggestion yet) where even the
        # deterministic fallback landed back on the original wording — this
        # path doesn't go through rewrite_bullet_with_ollama's/
        # rewrite_summary_with_llm's own near-identical retry, so it must be
        # caught here too. This is a genuine "nothing meaningfully different
        # to offer," not a technical failure — the caller must never present
        # two identical strings as an "AI Improved" review.
        no_change_recommended = True
        used_llm = False

    return {
        "original": original,
        "suggested": suggested,
        "changed": suggested != original,
        "constrained": True,
        "used_llm": used_llm,
        "llm_provider": provider_label if used_llm else None,
        "retry_failed": retry_failed,
        "no_change_recommended": no_change_recommended,
        "note": (
            "This text already reads well — no meaningfully different improvement was found."
            if no_change_recommended
            else "Could not generate another meaningfully different suggestion. Please try again."
            if retry_failed
            else "Suggestion is a wording polish only; no new skills, employers, metrics, or claims were added."
        ),
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
    if not _safe_to_download(optimized.validation_status):
        raise HTTPException(
            status_code=400,
            detail="Final validation must pass before download.",
        )
    # Always use the latest edited/final text — never a stale generated snapshot.
    plain = sanitize_resume_public_text(
        optimized.final_resume_text
        or optimized.edited_resume_text
        or optimized.generated_resume_text
        or optimized.optimized_text
        or ""
    )
    if not plain.strip():
        raise HTTPException(status_code=400, detail="Optimized resume content is empty.")
    document_version = _document_version(plain)
    # "Candidate" below is a DISPLAY fallback for the download filename only
    # (e.g. some resumes' name/contact lives in a DOCX header or text-box
    # that body-only text parsing never captures, so extraction can
    # legitimately come back empty) — it must never be treated as a REAL
    # expected name to verify against the rendered PDF's own text. Passing
    # it through as `expected_name` made every such resume's PDF download
    # fail with "missing candidate name" (the literal word "candidate"
    # never appears in an actual resume body) regardless of whether the
    # export itself was correct.
    extracted_name = parse_resume_structure(resume.parsed_text or "").get("name") or ""
    name = extracted_name or "Candidate"
    base = download_basename(name)
    fmt = (format or "docx").lower()

    if fmt == "txt":
        export_logger = logging.getLogger(__name__)
        export_logger.info(
            "ATS_EXPORT optimized_resume_id=%s resume_id=%s job_id=%s document_hash=%s txt_success=true",
            optimized.id,
            resume.id,
            job_id,
            document_version,
        )
        return Response(
            content=plain,
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{base}.txt"',
                "X-Document-Version": document_version,
            },
        )

    # Canonical finalized DOCX first (template-preserving when source is DOCX).
    rendered = render_optimized_document(
        resume.stored_file_path,
        resume.file_type,
        plain,
        output_path=None,
    )
    docx_path = Path(rendered["file_path"])
    optimized.final_file_path = str(docx_path)
    db.add(optimized)
    db.commit()

    if fmt == "pdf":
        pdf_destination = OUTPUT_DIR / f"{uuid4()}.pdf"
        pdf_path = convert_docx_to_pdf(docx_path, pdf_destination)
        validate_pdf_export(
            pdf_path,
            expected_name=extracted_name or None,
            expected_headings=["summary", "skills", "experience", "technical", "professional"],
        )
        export_logger = logging.getLogger(__name__)
        export_logger.info(
            "ATS_EXPORT optimized_resume_id=%s resume_id=%s job_id=%s document_hash=%s docx_success=true pdf_success=true",
            optimized.id,
            resume.id,
            job_id,
            document_version,
        )
        return FileResponse(
            pdf_path,
            media_type="application/pdf",
            filename=f"{base}.pdf",
            content_disposition_type="attachment",
            headers={"X-Document-Version": document_version},
        )

    export_logger = logging.getLogger(__name__)
    export_logger.info(
        "ATS_EXPORT optimized_resume_id=%s resume_id=%s job_id=%s document_hash=%s docx_success=true pdf_success=false",
        optimized.id,
        resume.id,
        job_id,
        document_version,
    )
    return FileResponse(
        docx_path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=f"{base}.docx",
        content_disposition_type="attachment",
        headers={"X-Document-Version": document_version},
    )
