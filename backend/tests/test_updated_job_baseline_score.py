"""Updated Job Search must display the unchanged Selected Jobs/ATS baseline."""
import socket
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests

from app.models.discovered_job_model import DiscoveredJob
from app.models.resume_model import Resume
from app.models.job_model import Job
from app.models.optimized_resume_model import OptimizedResume
from app.services import ats_scoring, job_match_service as matcher
from app.routes import job_discovery_routes as routes

JD = "We need a Java Full Stack Developer with Spring Boot, React, PostgreSQL, and AWS experience."
SKILLS = "Java, Spring Boot, React, PostgreSQL, AWS"


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("Scoring must not call providers or perform DNS")
    monkeypatch.setattr(requests.Session, "request", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setenv("JOB_MATCH_LLM_PROVIDER", "none")
    monkeypatch.setenv("COMPANY_CAREER_SOURCES", "[]")
    monkeypatch.setenv("APIFY_DICE_ENABLED", "false")


def seed_job(db, **overrides):
    values = dict(source="adzuna", source_mode="live", source_job_id="baseline-1", fingerprint="baseline-1",
        company="Infodyne Solutions", title="Java Full Stack Developer", location="Remote", work_mode="Remote",
        job_type="Full-time", description=JD, skills=SKILLS, content_hash="same-provider-hash",
        source_url="https://www.adzuna.com/details/123456", status="ACTIVE")
    values.update(overrides)
    row = DiscoveredJob(**values)
    db.add(row)
    db.commit()
    return row


def baseline(score=84):
    return dict(match_percentage=score, matched_keywords=["Java", "Spring Boot"], missing_keywords=["AWS"],
        required_skills=["Java", "Spring Boot", "AWS"], preferred_skills=["React"],
        confidence="HIGH", role_alignment="EXACT", recommendation="Review remaining gaps.",
        mandatory_requirements={"covered": 2, "total": 3}, preferred_requirements={"covered": 1, "total": 1})


def compute(db, job, text="original resume", user=1, resume=10, **kwargs):
    return matcher.get_or_compute_match(db, user_id=user, resume_id=resume, resume_text=text,
                                       discovered_job=job, use_llm=False, **kwargs)


def test_real_canonical_engine_matches_selected_jobs_and_ats_not_old_overlap_100(java_resume_text):
    from app.agents.matching_agent import calculate_match_score
    from app.services import ats_analyzer_service as ats, ats_optimization_workflow_service as workflow
    job = SimpleNamespace(title="Java Full Stack Developer", description=JD, skills_required=SKILLS)
    selected = calculate_match_score(java_resume_text, job)
    actual = matcher.deterministic_match(job_title=job.title, job_description=job.description,
        job_skills_csv=job.skills_required, resume_text=java_resume_text)
    assert ats.calculate_weighted_ats_score is ats_scoring.calculate_weighted_ats_score
    assert workflow.calculate_weighted_ats_score is ats_scoring.calculate_weighted_ats_score
    assert actual["match_score"] == selected["match_percentage"]
    # All title words/skills occur in the resume: the removed formula yielded
    # 100, while the canonical evidence-based scorer identifies weaker evidence.
    assert all(word.lower() in java_resume_text.lower() for word in job.title.split())
    assert all(skill.lower() in java_resume_text.lower() for skill in SKILLS.split(", "))
    assert actual["match_score"] < 100


def test_api_save_and_ats_handoff_preserve_84_despite_relevance_100(client, db_session, test_user, test_resume, test_preferences, monkeypatch):
    from app.agents import matching_agent
    from app.services import ats_optimization_workflow_service as workflow, ats_analyzer_service as ats
    scorer = MagicMock(return_value=baseline(84))
    for module in (ats_scoring, matching_agent, workflow, ats):
        monkeypatch.setattr(module, "calculate_weighted_ats_score", scorer)
    monkeypatch.setattr(routes.job_relevance_filter, "evaluate_relevance", lambda *args: SimpleNamespace(relevant=True, score=100))
    start_workflow = MagicMock(return_value={"workflow_status": "PENDING"})
    monkeypatch.setattr(routes, "start_workflow_for_saved_job", start_workflow)
    discovered = seed_job(db_session)
    payload = client.get(f"/api/job-discovery/jobs/{test_user.id}").json()
    assert payload["jobs"][0]["match"]["match_score"] == 84
    assert scorer.call_count == 1  # optional explanation pass reuses numeric cache
    start_workflow.assert_not_called()  # scoring never runs optimization
    saved = client.post(f"/api/job-discovery/save/{test_user.id}", json={"discovered_job_id": discovered.id}).json()
    row = db_session.query(Job).filter_by(id=saved["job_id"]).one()
    assert (row.title, row.description, row.skills_required) == (discovered.title, discovered.description, discovered.skills)
    assert client.post(f"/api/match/calculate/{test_user.id}").json()["matches"][0]["match_percentage"] == 84
    assert ats.calculate_weighted_ats_score(test_resume.parsed_text, row)["match_percentage"] == 84
    assert workflow.calculate_weighted_ats_score(test_resume.parsed_text, row)["match_percentage"] == 84
    start_workflow.assert_called_once()
    for call in scorer.call_args_list:
        assert call.args[0] == test_resume.parsed_text
        assert not call.kwargs  # no confirmed evidence or optimized-document mode


@pytest.mark.parametrize("score", [74, 84])
def test_java_role_stays_visible_below_100(client, db_session, test_user, test_resume, test_preferences, monkeypatch, score):
    monkeypatch.setattr(ats_scoring, "calculate_weighted_ats_score", lambda *args: baseline(score))
    seed_job(db_session)
    jobs = client.get(f"/api/job-discovery/jobs/{test_user.id}").json()["jobs"]
    assert len(jobs) == 1 and jobs[0]["match"]["match_score"] == score


@pytest.mark.parametrize("description", ["", " ", "Java Full Stack Developer", "Access denied. Sign in to continue to view this job description."])
def test_missing_or_incomplete_jd_is_unscored_even_with_old_100(db_session, monkeypatch, description):
    scorer = MagicMock(return_value=baseline(100))
    monkeypatch.setattr(ats_scoring, "calculate_weighted_ats_score", scorer)
    job = seed_job(db_session)
    cached = compute(db_session, job)
    assert cached.match_score == 100
    job.description = description
    assert compute(db_session, job) is None
    assert scorer.call_count == 1


@pytest.mark.parametrize("change", ["resume_id", "resume_text", "description", "skills", "title", "scorer_version", "legacy_cache"])
def test_cached_100_invalidated_for_every_scoring_input(db_session, monkeypatch, change):
    scorer = MagicMock(side_effect=[baseline(100), baseline(84)])
    monkeypatch.setattr(ats_scoring, "calculate_weighted_ats_score", scorer)
    job = seed_job(db_session)
    cached = compute(db_session, job)
    first_id = cached.id
    kwargs = {}
    if change == "resume_id":
        kwargs["resume"] = 11
    elif change == "resume_text":
        kwargs["text"] = "Changed original resume under the same ID"
    elif change in {"description", "skills", "title"}:
        setattr(job, change, getattr(job, change) + " changed")
    elif change == "scorer_version":
        monkeypatch.setattr(ats_scoring, "ANALYSIS_VERSION", "test-next-analysis")
    else:
        cached.matcher_version = "job-match-deterministic-v1"
    result = compute(db_session, job, **kwargs)
    assert result.id == first_id and result.match_score == 84 and scorer.call_count == 2
    assert len(result.matcher_version) <= 30


def test_unchanged_cache_and_explanation_upgrade_do_not_rescore(db_session, monkeypatch):
    scorer = MagicMock(return_value=baseline())
    monkeypatch.setattr(ats_scoring, "calculate_weighted_ats_score", scorer)
    job = seed_job(db_session)
    first = compute(db_session, job)
    stamp = first.calculated_at
    second = compute(db_session, job)
    assert second.calculated_at == stamp
    assert compute(db_session, job, force=True).match_score == 84
    scorer.assert_called_once()


def test_stale_cache_is_not_returned_when_new_baseline_fails(db_session, monkeypatch):
    monkeypatch.setattr(ats_scoring, "calculate_weighted_ats_score", MagicMock(side_effect=[baseline(100), ValueError("failed")]))
    job = seed_job(db_session)
    assert compute(db_session, job).match_score == 100
    assert compute(db_session, job, text="different original resume") is None


def test_different_users_and_resume_versions_never_share_score(db_session, monkeypatch):
    scorer = MagicMock(side_effect=[baseline(84), baseline(67), baseline(72)])
    monkeypatch.setattr(ats_scoring, "calculate_weighted_ats_score", scorer)
    job = seed_job(db_session)
    first = compute(db_session, job, user=1, resume=10)
    second = compute(db_session, job, user=2, resume=20, text="second user resume")
    assert first.id != second.id and (first.match_score, second.match_score) == (84, 67)
    assert compute(db_session, job, user=1, resume=11, text="new original resume").match_score == 72
    assert second.match_score == 67


def test_current_original_resume_uses_selected_jobs_order_and_ignores_optimized_draft(client, db_session, test_user, test_resume, monkeypatch):
    from app.services.ats_optimization_workflow_service import _latest_resume_for_user
    scorer = MagicMock(side_effect=lambda text, job: baseline(88 if text == "optimized draft" else 84 if text == "current original" else 72))
    monkeypatch.setattr(ats_scoring, "calculate_weighted_ats_score", scorer)
    job = seed_job(db_session)
    same_time = datetime.now(timezone.utc)
    test_resume.uploaded_at = same_time
    current = Resume(user_id=test_user.id, original_file_name="current.txt", stored_file_path="/test/current.txt",
                     file_type="txt", parsed_text="current original", uploaded_at=same_time)
    db_session.add(current)
    db_session.commit()
    saved = Job(user_id=test_user.id, title=job.title, company=job.company, description=JD,
                location=job.location, work_mode=job.work_mode, job_type=job.job_type,
                skills_required=SKILLS, source="User Selected Job")
    db_session.add(saved)
    db_session.flush()
    db_session.add(OptimizedResume(user_id=test_user.id, resume_id=current.id, job_id=saved.id, optimized_text="optimized draft"))
    db_session.commit()
    assert _latest_resume_for_user(db_session, test_user.id).id == current.id
    assert routes._latest_resume(test_user.id, db_session).id == current.id
    payload = client.get(f"/api/job-discovery/jobs/{test_user.id}").json()
    assert payload["jobs"][0]["match"]["match_score"] == 84
    assert scorer.call_args.args[0] == "current original"
    assert ats_scoring.calculate_weighted_ats_score("optimized draft", saved)["match_percentage"] == 88
    payload = client.get(f"/api/job-discovery/jobs/{test_user.id}").json()
    assert payload["jobs"][0]["match"]["match_score"] == 84
    db_session.delete(current)
    db_session.commit()
    assert client.get(f"/api/job-discovery/jobs/{test_user.id}").json()["jobs"][0]["match"]["match_score"] == 72


def test_no_resume_returns_unscored(db_session):
    assert compute(db_session, seed_job(db_session), resume=None, text=None) is None
