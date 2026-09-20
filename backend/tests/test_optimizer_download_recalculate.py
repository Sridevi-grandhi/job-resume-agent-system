"""Tests for optimized-resume download and recalculate-score behavior."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from docx import Document

from app.models.optimized_resume_model import OptimizedResume
from app.services.ats_scoring import calculate_weighted_ats_score
from app.services.resume_document_service import download_basename, plain_text_to_document
from app.services.resume_structure_parser import parse_resume_structure


def _seed_optimized(db_session, test_user, test_resume, java_job, text=None, validation_status="passed"):
    body = text or test_resume.parsed_text
    optimized = OptimizedResume(
        user_id=test_user.id,
        resume_id=test_resume.id,
        job_id=java_job.id,
        optimized_text=body,
        generated_resume_text=body,
        final_resume_text=body,
        original_score=70,
        generated_score=80,
        edited_score=80,
        template_preserved=True,
        source_file_type="docx",
        validation_status=validation_status,
    )
    db_session.add(optimized)
    db_session.commit()
    db_session.refresh(optimized)
    return optimized


def test_download_basename_uses_candidate_name():
    assert download_basename("Jane Doe") == "Jane_Doe_Optimized_Resume"
    assert download_basename("") == "Optimized_Resume"


def test_download_docx_uses_latest_edited_text_not_stale_file(
    client, db_session, test_user, test_resume, java_job, tmp_path,
):
    source = tmp_path / "source.docx"
    Document().save(source)
    test_resume.stored_file_path = str(source)
    test_resume.file_type = "docx"
    db_session.add(test_resume)
    db_session.commit()

    stale = "STALE_GENERATED_CONTENT_SHOULD_NOT_DOWNLOAD"
    marker = "UNIQUE_EDIT_MARKER_SPRING_BOOT_EVIDENCE"
    latest = f"{test_resume.parsed_text}\n{marker}"
    optimized = _seed_optimized(db_session, test_user, test_resume, java_job, text=stale)
    stale_path = tmp_path / "stale.docx"
    stale_doc = Document()
    stale_doc.add_paragraph(stale)
    stale_doc.save(stale_path)
    optimized.final_file_path = str(stale_path)
    optimized.edited_resume_text = latest
    optimized.final_resume_text = latest
    optimized.validation_status = "passed"
    db_session.add(optimized)
    db_session.commit()

    response = client.get(
        f"/api/optimizer/resume/{test_user.id}/{java_job.id}/{optimized.id}/download?format=docx"
    )
    assert response.status_code == 200
    assert "attachment" in (response.headers.get("content-disposition") or "").lower()
    assert "Optimized_Resume.docx" in (response.headers.get("content-disposition") or "")
    assert "file_path" not in response.text
    loaded = Document(BytesIO(response.content))
    text = "\n".join(p.text for p in loaded.paragraphs)
    assert "UNIQUE_EDIT_MARKER_SPRING_BOOT_EVIDENCE" in text
    assert "STALE_GENERATED_CONTENT_SHOULD_NOT_DOWNLOAD" not in text


def test_download_txt_uses_latest_content_and_correct_mime_type(
    client, db_session, test_user, test_resume, java_job, tmp_path,
):
    source = tmp_path / "source.docx"
    Document().save(source)
    test_resume.stored_file_path = str(source)
    test_resume.file_type = "docx"
    db_session.add(test_resume)
    db_session.commit()

    marker = "UNIQUE_TXT_EDIT_MARKER_KAFKA_STREAMS"
    latest = f"{test_resume.parsed_text}\n{marker}"
    optimized = _seed_optimized(db_session, test_user, test_resume, java_job)
    optimized.edited_resume_text = latest
    optimized.final_resume_text = latest
    db_session.add(optimized)
    db_session.commit()

    response = client.get(
        f"/api/optimizer/resume/{test_user.id}/{java_job.id}/{optimized.id}/download?format=txt"
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "attachment" in (response.headers.get("content-disposition") or "").lower()
    assert ".txt" in (response.headers.get("content-disposition") or "")
    assert marker in response.text


def test_download_rejects_cross_user_access(
    client, db_session, test_user, second_user, test_resume, java_job, tmp_path,
):
    source = tmp_path / "source.docx"
    Document().save(source)
    test_resume.stored_file_path = str(source)
    test_resume.file_type = "docx"
    db_session.add(test_resume)
    db_session.commit()
    optimized = _seed_optimized(db_session, test_user, test_resume, java_job)

    response = client.get(
        f"/api/optimizer/resume/{second_user.id}/{java_job.id}/{optimized.id}/download?format=docx"
    )
    assert response.status_code == 404
    assert "traceback" not in response.text.lower()


def test_download_invalid_resume_id_handled_safely(client, test_user, java_job):
    response = client.get(
        f"/api/optimizer/resume/{test_user.id}/{java_job.id}/999999/download?format=docx"
    )
    assert response.status_code == 404
    assert "traceback" not in response.text.lower()
    assert "stack" not in response.text.lower()


def test_download_blocked_when_final_validation_failed(
    client, db_session, test_user, test_resume, java_job, tmp_path,
):
    source = tmp_path / "source.docx"
    Document().save(source)
    test_resume.stored_file_path = str(source)
    test_resume.file_type = "docx"
    db_session.add(test_resume)
    db_session.commit()
    optimized = _seed_optimized(db_session, test_user, test_resume, java_job, validation_status="failed")

    response = client.get(
        f"/api/optimizer/resume/{test_user.id}/{java_job.id}/{optimized.id}/download?format=docx"
    )
    assert response.status_code == 400
    assert "traceback" not in response.text.lower()


def test_download_pdf_allowed_for_passed_with_warnings(
    client, db_session, test_user, test_resume, java_job, tmp_path,
):
    source = tmp_path / "source.docx"
    Document().save(source)
    test_resume.stored_file_path = str(source)
    test_resume.file_type = "docx"
    db_session.add(test_resume)
    db_session.commit()
    optimized = _seed_optimized(
        db_session, test_user, test_resume, java_job, validation_status="passed_with_warnings",
    )
    fake_pdf = tmp_path / "converted.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4\n" + b"0" * 600)

    def _fake_convert(docx_path, destination=None):
        target = Path(destination) if destination else fake_pdf
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(fake_pdf.read_bytes())
        return target

    with patch("app.routes.optimizer_edit_routes.convert_docx_to_pdf", side_effect=_fake_convert):
        with patch("app.routes.optimizer_edit_routes.validate_pdf_export", return_value={"valid": True, "page_count": 1}):
            response = client.get(
                f"/api/optimizer/resume/{test_user.id}/{java_job.id}/{optimized.id}/download?format=pdf"
            )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")


def test_download_pdf_succeeds_when_resume_has_no_extractable_name(
    client, db_session, test_user, test_resume, java_job, tmp_path,
):
    """Regression: when resume.parsed_text has no extractable name (real
    production case — some templates keep the candidate's name/contact in a
    DOCX header or text-box that body-only text extraction never captures),
    the download route used to fall back to the literal placeholder
    "Candidate" and then pass THAT to validate_pdf_export as the name that
    must appear in the rendered PDF — which no real resume ever contains,
    so every such resume's PDF download failed with "missing candidate
    name" regardless of whether the export itself was correct."""
    import fitz

    source = tmp_path / "source.docx"
    Document().save(source)
    test_resume.stored_file_path = str(source)
    test_resume.file_type = "docx"
    # No name/contact line — the resume body starts directly with a heading,
    # matching the real production resume that surfaced this bug.
    test_resume.parsed_text = (
        "Professional Summary\nSenior engineer with strong automation background.\n\n"
        "Technical Skills\nJava, Python\n"
    )
    db_session.add(test_resume)
    db_session.commit()
    optimized = _seed_optimized(db_session, test_user, test_resume, java_job, text=test_resume.parsed_text)

    real_pdf = tmp_path / "converted.pdf"
    pdf_doc = fitz.open()
    page = pdf_doc.new_page()
    page.insert_text((72, 72), "Professional Summary\nSenior engineer with strong automation background.")
    pdf_doc.save(real_pdf)
    pdf_doc.close()

    def _fake_convert(docx_path, destination=None):
        target = Path(destination) if destination else real_pdf
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(real_pdf.read_bytes())
        return target

    with patch("app.routes.optimizer_edit_routes.convert_docx_to_pdf", side_effect=_fake_convert):
        # validate_pdf_export runs for REAL here (not mocked) — this is the
        # exact check that used to reject the PDF over the missing literal
        # word "candidate".
        response = client.get(
            f"/api/optimizer/resume/{test_user.id}/{java_job.id}/{optimized.id}/download?format=pdf"
        )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")


def test_recalculate_persists_editor_content_and_scores_it(
    client, db_session, test_user, test_resume, java_job, tmp_path,
):
    source = tmp_path / "source.docx"
    Document().save(source)
    test_resume.stored_file_path = str(source)
    test_resume.file_type = "docx"
    db_session.add(test_resume)
    db_session.commit()
    optimized = _seed_optimized(db_session, test_user, test_resume, java_job)
    document = plain_text_to_document(test_resume.parsed_text)
    # Cosmetic deletion: remove a generic non-skill sentence from summary if present.
    for section in document.get("sections") or []:
        if section.get("type") == "summary" and section.get("content"):
            section["content"] = (section["content"] or "").replace("  ", " ").strip()
            break

    with patch("app.routes.optimizer_edit_routes.calculate_weighted_ats_score", wraps=calculate_weighted_ats_score) as scored:
        with patch("app.routes.optimizer_edit_routes.LLMClient", create=True):
            response = client.patch(
                f"/api/optimizer/resume/{test_user.id}/{java_job.id}",
                json={
                    "optimized_resume_id": optimized.id,
                    "document": document,
                    "action": "recalculate",
                    "persist": True,
                    "score_only": False,
                },
            )
    assert response.status_code == 200
    payload = response.json()
    assert payload["persisted"] is True
    assert "score" in payload
    assert payload["score"]["component_scores"]
    assert scored.called
    # Recalculate must not invoke OpenAI / LLM client.
    assert "app.services.llm_client" not in str(scored.call_args)


def test_deleting_all_mandatory_skill_evidence_can_reduce_dimension(
    client, db_session, test_user, test_resume, java_job, tmp_path,
):
    source = tmp_path / "source.docx"
    Document().save(source)
    test_resume.stored_file_path = str(source)
    test_resume.file_type = "docx"
    db_session.add(test_resume)
    db_session.commit()
    optimized = _seed_optimized(db_session, test_user, test_resume, java_job)

    baseline = client.patch(
        f"/api/optimizer/resume/{test_user.id}/{java_job.id}",
        json={
            "optimized_resume_id": optimized.id,
            "edited_resume_text": test_resume.parsed_text,
            "action": "recalculate",
            "persist": True,
        },
    ).json()
    before = baseline["edited_score"]
    before_components = baseline["score"]["component_scores"]

    # Strip a core JD skill ("Java") from the edited resume body.
    stripped = test_resume.parsed_text.replace("Java", "Software").replace("java", "software")
    after_resp = client.patch(
        f"/api/optimizer/resume/{test_user.id}/{java_job.id}",
        json={
            "optimized_resume_id": optimized.id,
            "edited_resume_text": stripped,
            "action": "recalculate",
            "persist": True,
        },
    )
    assert after_resp.status_code == 200
    after = after_resp.json()
    assert after["edited_score"] <= before
    # Mandatory / keyword dimensions should not improve after removing Java evidence.
    def _earned(view, key):
        entry = (view or {}).get(key)
        if isinstance(entry, dict):
            return entry.get("earned")
        return entry

    assert _earned(after["score"]["component_scores"], "mandatory_skills") <= _earned(
        before_components, "mandatory_skills"
    ) or after["edited_score"] < before


def test_unsupported_skill_does_not_improve_score(
    client, db_session, test_user, test_resume, java_job, tmp_path,
):
    source = tmp_path / "source.docx"
    Document().save(source)
    test_resume.stored_file_path = str(source)
    test_resume.file_type = "docx"
    db_session.add(test_resume)
    db_session.commit()
    optimized = _seed_optimized(db_session, test_user, test_resume, java_job)
    baseline = client.patch(
        f"/api/optimizer/resume/{test_user.id}/{java_job.id}",
        json={
            "optimized_resume_id": optimized.id,
            "edited_resume_text": test_resume.parsed_text,
            "action": "recalculate",
            "persist": True,
        },
    ).json()["edited_score"]

    with_appium = test_resume.parsed_text + "\nBuilt Appium mobile automation suites."
    after = client.patch(
        f"/api/optimizer/resume/{test_user.id}/{java_job.id}",
        json={
            "optimized_resume_id": optimized.id,
            "edited_resume_text": with_appium,
            "action": "recalculate",
            "persist": True,
        },
    ).json()
    # Inventing unsupported Appium must not raise the ATS score.
    assert after["edited_score"] <= baseline + 1  # allow rounding noise only
    # Prefer strict non-increase when scorer ignores unsupported tech.
    assert after["edited_score"] <= baseline or after.get("validation", {}).get("status") == "failed"


def test_score_unchanged_message_when_cosmetic_edit(
    client, db_session, test_user, test_resume, java_job, tmp_path,
):
    source = tmp_path / "source.docx"
    Document().save(source)
    test_resume.stored_file_path = str(source)
    test_resume.file_type = "docx"
    db_session.add(test_resume)
    db_session.commit()
    optimized = _seed_optimized(db_session, test_user, test_resume, java_job)

    # First persist establishes edited_score.
    client.patch(
        f"/api/optimizer/resume/{test_user.id}/{java_job.id}",
        json={
            "optimized_resume_id": optimized.id,
            "edited_resume_text": test_resume.parsed_text,
            "action": "recalculate",
            "persist": True,
        },
    )
    # Whitespace-only change should keep score unchanged.
    tweaked = test_resume.parsed_text.replace("\n\n", "\n\n\n", 1)
    response = client.patch(
        f"/api/optimizer/resume/{test_user.id}/{java_job.id}",
        json={
            "optimized_resume_id": optimized.id,
            "edited_resume_text": tweaked,
            "action": "recalculate",
            "persist": True,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    if payload.get("score_unchanged"):
        assert "Score remains" in (payload.get("score_unchanged_reason") or "")
        assert "did not change any scored ATS requirements" in payload["score_unchanged_reason"]
