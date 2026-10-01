"""Official career destinations outrank provider listings. No live network."""

from app.services.employer_career_service import clear_career_cache
from app.services.job_enrichment_service import EnrichmentResult
from app.services.job_normalization_service import (
    incoming_destination_wins,
    present_updated_job_action,
    resolve_apply_action,
)
from app.services.role_family_service import discovery_search_titles, evaluate_role_alignment


def _shown(**overrides):
    values = dict(
        source="apify_dice", company="Northwind Bank", title="Java Full Stack Developer",
        location="Richmond, VA", application_url=None, direct_url=None,
        source_url="https://www.dice.com/job-detail/4821301-java-full-stack-developer",
        unverified_application_url=None, career_page_url=None,
        availability_status="UNKNOWN", verified_destination_url=None,
        enrichment_diagnostics=None,
    )
    values.update(overrides)
    return present_updated_job_action(resolve_apply_action(values))


def test_exact_employer_posting_is_apply():
    action = _shown(
        application_url="https://careers.northwind.com/job/48213/java-full-stack-developer",
        direct_url="https://careers.northwind.com/job/48213/java-full-stack-developer",
        availability_status="ACTIVE",
    )
    assert action["action_label"] == "Apply on Company Site"
    assert action["url"].startswith("https://careers.northwind.com/job/")
    assert "dice.com" not in action["url"]


def test_known_careers_page_beats_provider_listing():
    action = _shown(career_page_url="https://careers.northwind.com/careers")
    assert action["action_label"] == "Open Career Page"
    assert action["url"] == "https://careers.northwind.com/careers"
    assert "dice.com" not in action["url"]


def test_exact_ats_posting_is_apply():
    url = "https://boards.greenhouse.io/northwind/jobs/48213"
    action = _shown(company="Northwind", application_url=url, direct_url=url, availability_status="ACTIVE")
    assert action["action_label"] == "Apply on Company Site"
    assert action["url"] == url


def test_provider_listing_only_is_view_source():
    action = _shown()
    assert action["action_label"] == "Apply on Dice"
    assert "dice.com" in action["url"]


def test_exact_job_replaces_generic_career_page():
    generic = _shown(career_page_url="https://careers.northwind.com/careers")
    assert generic["action_label"] == "Open Career Page"
    exact = "https://careers.northwind.com/job/48213/java-full-stack-developer"
    promoted = _shown(career_page_url="https://careers.northwind.com/careers", application_url=exact, direct_url=exact, availability_status="ACTIVE")
    assert promoted["action_label"] == "Apply on Company Site"
    assert promoted["url"] == exact


def test_provider_duplicate_does_not_overwrite_employer_destination():
    employer = "https://northwind.wd1.myworkdayjobs.com/en-US/Careers/job/Remote/Java-Developer_R48213"
    provider = "https://www.adzuna.com/details/48213019"
    assert incoming_destination_wins(provider, employer) is False
    action = _shown(application_url=employer, direct_url=employer, source_url=provider, availability_status="ACTIVE")
    assert action["action_label"] == "Apply on Company Site"
    assert action["url"] == employer


def test_closed_exact_posting_is_job_closed():
    action = _shown(
        application_url="https://careers.northwind.com/job/48213/java-full-stack-developer",
        availability_status="EXPIRED",
    )
    assert action["action_label"] == "Job Closed"


def test_role_family_stays_relevant_and_rejects_unrelated_roles():
    titles = discovery_search_titles("Java Full Stack Developer", "Java, Spring Boot")
    assert titles[0] == "Java Full Stack Developer"
    assert "Data Scientist" not in titles
    assert evaluate_role_alignment(
        job_title="Senior Data Scientist", job_description="Statistical models.",
        preferred_title="Java Full Stack Developer",
    ).relevant is False
    assert evaluate_role_alignment(
        job_title="Spring Boot Developer", job_description="Java services with Spring Boot.",
        preferred_title="Java Full Stack Developer",
    ).relevant is True


def test_card_and_details_share_the_career_page(db_session, monkeypatch):
    from app.services import job_change_detection_service as persistence
    from app.services.job_normalization_service import build_normalized_job

    clear_career_cache()
    monkeypatch.setattr(
        "app.services.job_change_detection_service.enrich_job_with_fallback",
        lambda *args, **kwargs: EnrichmentResult("REJECTED", "no_candidates"),
    )
    first = build_normalized_job(
        source="apify_dice", source_mode="live", source_job_id="1", company="Northwind Bank",
        title="Java Full Stack Developer", location="Richmond, VA",
        description="Build Java and Spring Boot services for customer banking.",
        source_url="https://www.dice.com/job-detail/4821301-java-full-stack-developer",
        career_page_url="https://careers.northwind.com/careers",
    )
    second = build_normalized_job(
        source="adzuna", source_mode="live", source_job_id="2", company="Northwind Bank",
        title="Java Developer", location="Richmond, VA",
        description="Java backend services for retail banking platforms.",
        source_url="https://www.adzuna.com/details/48213019",
    )
    persistence.process_source_jobs(db_session, "apify_dice", [first])
    persistence.process_source_jobs(db_session, "adzuna", [second])
    from app.models.discovered_job_model import DiscoveredJob

    rows = db_session.query(DiscoveredJob).filter_by(company="Northwind Bank").all()
    assert len(rows) == 2
    labels = {present_updated_job_action(resolve_apply_action(row))["action_label"] for row in rows}
    urls = {present_updated_job_action(resolve_apply_action(row))["url"] for row in rows}
    assert labels == {"Open Career Page"}
    assert urls == {"https://careers.northwind.com/careers"}
    card = present_updated_job_action(resolve_apply_action(rows[0]))
    details = present_updated_job_action(resolve_apply_action(rows[0]))
    assert card["action_label"] == details["action_label"]
    assert card["url"] == details["url"]


def test_users_keep_separate_preference_queries(db_session, test_user, second_user):
    from datetime import time

    from app.models.user_preference_model import UserPreference
    from app.services.broad_provider_query_service import get_active_preference_queries

    db_session.add_all([
        UserPreference(user_id=test_user.id, job_title="Java Full Stack Developer", location="Remote",
                       experience="5", work_mode="", job_type="", skills="Java, Spring Boot", schedule_time=time(8, 0)),
        UserPreference(user_id=second_user.id, job_title="Data Scientist", location="Remote",
                       experience="5", work_mode="", job_type="", skills="Python", schedule_time=time(8, 0)),
    ])
    db_session.commit()
    first = [item["job_title"] for item in get_active_preference_queries(db_session, user_id=test_user.id)]
    second = [item["job_title"] for item in get_active_preference_queries(db_session, user_id=second_user.id)]
    assert first[0] == "Java Full Stack Developer"
    assert "Data Scientist" not in first
    assert second[0] == "Data Scientist"
    assert "Java Full Stack Developer" not in second
