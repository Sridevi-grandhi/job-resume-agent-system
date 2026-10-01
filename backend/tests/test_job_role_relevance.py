"""Updated Job Search discovery relevance: the saved job title decides which
occupation a user sees, and shared tooling can never override it.

Two separate questions are under test here, and only the first one:
  1. DISCOVERY RELEVANCE — does this job belong in this user's search?
  2. RESUME MATCH SCORE — how well does the resume fit an already-relevant job?
A job with a measurable resume match score must still be rejected when it is
a different occupation, so nothing below asserts on match_score.

Everything is generic by construction: the same code path is exercised for
DevOps, Java Full Stack, SDET and Data Engineering preferences.
"""

from __future__ import annotations

from datetime import datetime, time as dt_time, timezone
from types import SimpleNamespace

import pytest

from app.models.discovered_job_model import DiscoveredJob
from app.models.user_preference_model import UserPreference
from app.services.job_relevance_filter import evaluate_relevance, filter_relevant_jobs
from app.services.role_family_service import evaluate_role_alignment

DEVOPS_PREFERENCE = SimpleNamespace(
    job_title="Devops", location="usa", experience="8",
    work_mode="Remote", job_type="Full-time", skills="aws",
)

# The shared-tooling laundry list at the heart of the bug: every one of these
# appears in DevOps, Java, React and Data Engineering postings alike.
SHARED_TOOLING_TEXT = "AWS, Kubernetes, Docker, CI/CD, Linux, Python, Terraform, Jenkins"


def _job(title, description="", *, skills="", location="Remote", work_mode="Remote", job_type="Full-time"):
    return SimpleNamespace(
        title=title, description=description, skills=skills,
        location=location, work_mode=work_mode, job_type=job_type,
    )


def _relevant(job, preference=DEVOPS_PREFERENCE) -> bool:
    return evaluate_relevance(job, preference).relevant


# ---------------------------------------------------------------------------
# 1-4: role-family titles the DevOps preference must accept
# ---------------------------------------------------------------------------

def test_devops_preference_accepts_devops_engineer():
    job = _job(
        "Senior DevOps Engineer",
        f"We need a DevOps Engineer to own deployment automation and infrastructure as code. {SHARED_TOOLING_TEXT}",
    )
    decision = evaluate_relevance(job, DEVOPS_PREFERENCE)
    assert decision.relevant is True
    assert decision.alignment == "TITLE_MATCH"


def test_devops_preference_accepts_cloud_devops_engineer():
    job = _job("Cloud DevOps Engineer", f"Cloud operations and DevOps tooling. {SHARED_TOOLING_TEXT}")
    decision = evaluate_relevance(job, DEVOPS_PREFERENCE)
    assert decision.relevant is True
    assert decision.alignment == "TITLE_MATCH"


def test_devops_preference_accepts_sre_when_description_supports_it():
    job = _job(
        "Site Reliability Engineer",
        "Own service reliability engineering, incident response, on-call rotations and observability "
        f"for production systems. {SHARED_TOOLING_TEXT}",
    )
    decision = evaluate_relevance(job, DEVOPS_PREFERENCE)
    assert decision.relevant is True
    assert decision.alignment == "ADJACENT_ROLE"


def test_devops_preference_accepts_platform_engineer_when_description_supports_it():
    job = _job(
        "Platform Engineer",
        "Build our internal developer platform: infrastructure automation, cluster operations and "
        f"self-service provisioning for product teams. {SHARED_TOOLING_TEXT}",
    )
    decision = evaluate_relevance(job, DEVOPS_PREFERENCE)
    assert decision.relevant is True
    assert decision.alignment == "ADJACENT_ROLE"


def test_devsecops_and_infrastructure_titles_are_also_accepted():
    for title in ("DevSecOps Engineer", "Cloud Infrastructure Engineer", "Infrastructure Engineer", "SRE"):
        job = _job(title, f"Infrastructure as code, cluster operations and observability. {SHARED_TOOLING_TEXT}")
        assert _relevant(job) is True, title


# ---------------------------------------------------------------------------
# 5-7: conflicting occupations the DevOps preference must reject
# ---------------------------------------------------------------------------

def test_devops_preference_rejects_java_full_stack_despite_shared_tooling():
    job = _job(
        "Senior Java Full-Stack Developer",
        "Design and implement Spring Boot microservices with a React front end. "
        f"Deployed on {SHARED_TOOLING_TEXT}.",
    )
    decision = evaluate_relevance(job, DEVOPS_PREFERENCE)
    assert decision.relevant is False
    assert decision.alignment == "TITLE_CONFLICT"
    assert "Java Full Stack" in decision.reason


def test_devops_preference_rejects_react_full_stack_despite_cicd_and_terraform():
    job = _job(
        "Senior React Full-stack Developer",
        "Build React and Node.js product features. Our stack also uses CI/CD, Terraform, Docker and AWS.",
    )
    decision = evaluate_relevance(job, DEVOPS_PREFERENCE)
    assert decision.relevant is False
    assert decision.alignment == "TITLE_CONFLICT"


def test_devops_preference_rejects_data_engineer_despite_aws():
    job = _job(
        "Senior Data Engineer",
        f"Build ETL data pipelines into our data warehouse with Spark and Airflow. {SHARED_TOOLING_TEXT}",
    )
    decision = evaluate_relevance(job, DEVOPS_PREFERENCE)
    assert decision.relevant is False
    assert decision.alignment == "TITLE_CONFLICT"


def test_devops_preference_rejects_other_occupations():
    for title in ("Java Developer", "Frontend Developer", "Full Stack Developer",
                  "Data Scientist", "QA Engineer", "SDET", "SAP ABAP Consultant"):
        job = _job(title, f"Our platform runs on {SHARED_TOOLING_TEXT}.")
        assert _relevant(job) is False, title


# ---------------------------------------------------------------------------
# 8-9: the same gate, a different preference (nothing is DevOps-specific)
# ---------------------------------------------------------------------------

def test_java_full_stack_preference_accepts_java_full_stack_developer():
    preference = SimpleNamespace(
        job_title="Java Full Stack", location="usa", experience="8",
        work_mode="Remote", job_type="Full-time", skills="Java, Spring Boot, React",
    )
    job = _job(
        "Senior Java Full Stack Developer",
        "Spring Boot microservices and a React front end for enterprise customers.",
    )
    decision = evaluate_relevance(job, preference)
    assert decision.relevant is True
    assert decision.alignment == "TITLE_MATCH"


def test_java_full_stack_preference_rejects_devops_engineer_even_with_java_in_the_jd():
    preference = SimpleNamespace(
        job_title="Java Full Stack", location="usa", experience="8",
        work_mode="Remote", job_type="Full-time", skills="Java, Spring Boot, React",
    )
    job = _job(
        "DevOps Engineer",
        "Own our Kubernetes and Terraform infrastructure. You will support teams shipping Java "
        "and Spring Boot services, so familiarity with Java build tooling helps.",
    )
    decision = evaluate_relevance(job, preference)
    assert decision.relevant is False
    assert decision.alignment == "TITLE_CONFLICT"


def test_sdet_preference_sees_sdet_roles_not_devops():
    preference = SimpleNamespace(
        job_title="Senior SDET", location="Dallas, TX", experience="8",
        work_mode="Remote", job_type="Full-time", skills="Selenium, API Testing, Jenkins",
    )
    assert _relevant(_job("Senior SDET", "Build Selenium test automation frameworks."), preference) is True
    assert _relevant(_job("DevOps Engineer", f"Infrastructure as code. {SHARED_TOOLING_TEXT}"), preference) is False


def test_preferred_title_outside_the_taxonomy_still_gates_on_the_title():
    preference = SimpleNamespace(
        job_title="SAP Testing", location="usa", experience="8",
        work_mode="Remote", job_type="Full-time", skills="SAP, Testing",
    )
    assert _relevant(_job("SAP Testing Consultant", "Test SAP S/4HANA modules."), preference) is True
    assert _relevant(_job("QA Automation Engineer", "Selenium and Playwright test automation."), preference) is False


# ---------------------------------------------------------------------------
# 12: skills can refine relevance, never override the occupation
# ---------------------------------------------------------------------------

def test_skill_overlap_alone_cannot_admit_a_conflicting_role():
    # Every single saved skill appears in the job — and it is still rejected,
    # because the title is a different occupation.
    preference = SimpleNamespace(
        job_title="Devops", location="usa", experience="8", work_mode="Remote",
        job_type="Full-time", skills="AWS, Kubernetes, Docker, CI/CD, Linux, Python, Terraform",
    )
    job = _job(
        "Senior Java Full-Stack Developer",
        f"Java and Spring Boot services. Required tooling: {SHARED_TOOLING_TEXT}.",
        skills="AWS, Kubernetes, Docker, CI/CD, Linux, Python, Terraform",
    )
    decision = evaluate_relevance(job, preference)
    assert decision.relevant is False
    assert decision.score == 0.0


def test_generic_words_alone_do_not_create_a_match():
    for title in ("Cloud Support Technician", "Senior Engineer", "AWS Account Executive", "Senior Developer"):
        assert _relevant(_job(title, "We use the cloud and AWS.")) is False, title


def test_aggregator_skill_tag_lists_cannot_admit_a_conflicting_role():
    # Marketplaces attach one global technology tag list to every posting they
    # carry, so a React role advertises "terraform, infrastructure, Site
    # Reliability" exactly like their DevOps role does.
    tag_list = ("AWS, azure, terraform, kubernetes, infrastructure, Site Reliability, "
                "java, react, python, golang, data engineering")
    react_job = _job("Senior React Full-stack Developer", "Build product features.", skills=tag_list)
    devops_job = _job("Senior DevOps Engineer", "Keep environments reliable.", skills=tag_list)
    assert _relevant(react_job) is False
    assert _relevant(devops_job) is True


def test_filter_ranks_relevant_jobs_and_drops_the_rest():
    jobs = [
        _job("Senior Java Full-Stack Developer", f"Spring Boot and React. {SHARED_TOOLING_TEXT}"),
        _job("Senior DevOps Engineer", f"Deployment automation and infrastructure as code. {SHARED_TOOLING_TEXT}"),
        _job("Senior Data Engineer", f"ETL data pipelines. {SHARED_TOOLING_TEXT}"),
        _job("Site Reliability Engineer", f"Incident response and observability. {SHARED_TOOLING_TEXT}"),
    ]
    kept = filter_relevant_jobs(jobs, DEVOPS_PREFERENCE, top_n=40)
    assert [job.title for job in kept] == ["Senior DevOps Engineer", "Site Reliability Engineer"]


# ---------------------------------------------------------------------------
# 10-11: stored jobs are re-evaluated per user, never deleted or globally marked
# ---------------------------------------------------------------------------

def _seed_job(db_session, *, title, description, source_job_id, skills=""):
    job = DiscoveredJob(
        source="greenhouse", source_mode="live", source_job_id=source_job_id,
        fingerprint=f"fp-{source_job_id}", company=f"Employer {source_job_id}",
        title=title, location="Remote", work_mode="Remote", job_type="Full-time",
        description=description, skills=skills,
        direct_url=f"https://boards.greenhouse.io/employer/jobs/4821{source_job_id}",
        status="ACTIVE", content_hash=f"hash-{source_job_id}",
        first_seen_at=datetime.now(timezone.utc), last_seen_at=datetime.now(timezone.utc),
        last_checked_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    db_session.commit()
    return job


def _set_preference(db_session, user_id, job_title, skills="AWS, Kubernetes, Docker, CI/CD"):
    preference = db_session.query(UserPreference).filter(UserPreference.user_id == user_id).first()
    if preference is None:
        preference = UserPreference(
            user_id=user_id, location="usa", experience="8", work_mode="Remote",
            job_type="Full-time", schedule_time=dt_time(8, 0), timezone="America/Chicago",
            job_title=job_title, skills=skills,
        )
        db_session.add(preference)
    else:
        preference.job_title = job_title
        preference.skills = skills
    db_session.commit()
    return preference


@pytest.fixture()
def mixed_job_pool(db_session):
    return {
        "devops": _seed_job(
            db_session, source_job_id="1", title="Senior DevOps Engineer",
            description=f"Deployment automation and infrastructure as code. {SHARED_TOOLING_TEXT}",
        ),
        "java": _seed_job(
            db_session, source_job_id="2", title="Senior Java Full-Stack Developer",
            description=f"Spring Boot microservices with a React front end. {SHARED_TOOLING_TEXT}",
        ),
    }


def test_changing_preference_re_evaluates_stored_jobs_without_deleting_them(
    client, test_user, db_session, mixed_job_pool,
):
    _set_preference(db_session, test_user.id, "Devops")
    titles = [job["title"] for job in client.get(f"/api/job-discovery/jobs/{test_user.id}").json()["jobs"]]
    assert titles == ["Senior DevOps Engineer"]

    _set_preference(db_session, test_user.id, "Java Full Stack", skills="Java, Spring Boot, React")
    titles = [job["title"] for job in client.get(f"/api/job-discovery/jobs/{test_user.id}").json()["jobs"]]
    assert titles == ["Senior Java Full-Stack Developer"]

    # Hidden, never deleted: both postings are still in the shared pool.
    assert db_session.query(DiscoveredJob).count() == 2
    for job in mixed_job_pool.values():
        db_session.refresh(job)
        assert job.status == "ACTIVE"


def test_two_users_with_different_preferences_see_different_relevant_jobs(
    client, test_user, second_user, db_session, mixed_job_pool,
):
    _set_preference(db_session, test_user.id, "Devops")
    _set_preference(db_session, second_user.id, "Java Full Stack", skills="Java, Spring Boot, React")

    first = [job["title"] for job in client.get(f"/api/job-discovery/jobs/{test_user.id}").json()["jobs"]]
    second = [job["title"] for job in client.get(f"/api/job-discovery/jobs/{second_user.id}").json()["jobs"]]

    assert first == ["Senior DevOps Engineer"]
    assert second == ["Senior Java Full-Stack Developer"]


def test_top_job_matches_are_drawn_only_from_role_relevant_jobs(
    client, test_user, test_resume, db_session, mixed_job_pool,
):
    """The frontend's "Top Job Matches" is the head of this list, so a job
    from another occupation must not be in it even with a resume score."""
    _set_preference(db_session, test_user.id, "Devops")
    jobs = client.get(f"/api/job-discovery/jobs/{test_user.id}").json()["jobs"]
    assert [job["title"] for job in jobs] == ["Senior DevOps Engineer"]


def test_role_alignment_is_never_written_to_the_shared_job_row(db_session, mixed_job_pool):
    """Relevance depends on who is asking, so it must stay out of the globally
    shared DiscoveredJob row (which has no field for it, by design)."""
    java_job = mixed_job_pool["java"]
    evaluate_role_alignment(
        job_title=java_job.title, job_description=java_job.description, preferred_title="Devops",
    )
    assert not hasattr(java_job, "role_relevant")
    assert not hasattr(java_job, "role_family")


def test_preference_summary_is_returned_and_does_not_mutate_saved_preferences(
    client, test_user, db_session, mixed_job_pool,
):
    _set_preference(db_session, test_user.id, "Devops")
    response = client.get(
        f"/api/job-discovery/jobs/{test_user.id}",
        params={"search": "Engineer", "work_mode": "Remote"},
    )
    body = response.json()
    assert body["preference_summary"]["job_title"] == "Devops"
    assert body["preference_summary"]["location"] == "usa"
    assert db_session.query(UserPreference).filter_by(user_id=test_user.id).one().job_title == "Devops"


def test_duplicate_postings_collapse_to_the_best_verified_destination(
    client, test_user, db_session,
):
    _set_preference(db_session, test_user.id, "Devops")
    listing = _seed_job(
        db_session, source_job_id="agg", title="Senior DevOps Engineer",
        description="Deployment automation and infrastructure as code.",
    )
    listing.source = "jsearch"
    listing.company = "CloudCo"
    listing.direct_url = None
    listing.application_url = "https://www.linkedin.com/jobs/view/999"
    listing.source_url = "https://www.linkedin.com/jobs/view/999"
    listing.fingerprint = "fp-agg"
    db_session.commit()

    direct = _seed_job(
        db_session, source_job_id="ats", title="Senior DevOps Engineer",
        description="Deployment automation and infrastructure as code.",
    )
    direct.source = "greenhouse"
    direct.company = "CloudCo"
    direct.direct_url = "https://boards.greenhouse.io/cloudco/jobs/48219"
    direct.application_url = "https://boards.greenhouse.io/cloudco/jobs/48219"
    direct.source_url = "https://www.linkedin.com/jobs/view/999"
    direct.availability_status = "ACTIVE"
    direct.fingerprint = "fp-ats"
    db_session.commit()

    jobs = client.get(f"/api/job-discovery/jobs/{test_user.id}").json()["jobs"]
    assert [job["title"] for job in jobs] == ["Senior DevOps Engineer"]
    assert jobs[0]["action_label"] == "Apply on Company Site"
    assert jobs[0]["company"] == "CloudCo"
    assert db_session.query(DiscoveredJob).count() == 2


def test_employment_type_filter_matches_full_time_variants(client, test_user, db_session):
    _set_preference(db_session, test_user.id, "Devops")
    job = _seed_job(
        db_session, source_job_id="ft", title="Senior DevOps Engineer",
        description="Infrastructure as code and deployment automation.",
    )
    job.job_type = "Full Time"
    db_session.commit()
    titles = [
        item["title"]
        for item in client.get(
            f"/api/job-discovery/jobs/{test_user.id}",
            params={"employment_type": "Full-time"},
        ).json()["jobs"]
    ]
    assert titles == ["Senior DevOps Engineer"]

