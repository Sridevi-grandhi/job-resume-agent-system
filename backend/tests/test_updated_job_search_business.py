"""Updated Job Search business rules. No live provider calls."""

from types import SimpleNamespace

from app.services.job_normalization_service import present_updated_job_action, resolve_apply_action
from app.services.role_family_service import discovery_search_titles, evaluate_role_alignment


def _job(**overrides):
    values = dict(
        source="jsearch", company="Northwind", title="Java Full Stack Developer",
        location="Remote", application_url=None, direct_url=None, source_url=None,
        unverified_application_url=None, availability_status="UNKNOWN",
        verified_destination_url=None, enrichment_diagnostics=None,
    )
    values.update(overrides)
    return values


def test_role_family_search_includes_related_java_titles_and_not_unrelated_roles():
    titles = discovery_search_titles("Java Full Stack Developer", "Java, Spring Boot, React")
    assert titles[0] == "Java Full Stack Developer"
    assert "Spring Boot Developer" in titles
    assert len(titles) <= 2
    assert "Data Scientist" not in titles

    devops = discovery_search_titles("DevOps Engineer", "kubernetes")
    assert devops[0] == "DevOps Engineer"
    assert all("java" not in title.lower() for title in devops)


def test_unrelated_role_is_rejected_and_related_java_family_is_kept():
    rejected = evaluate_role_alignment(
        job_title="Senior Data Scientist", job_description="Machine learning models and statistics.",
        preferred_title="Java Full Stack Developer",
    )
    assert rejected.relevant is False
    kept = evaluate_role_alignment(
        job_title="Spring Boot Developer",
        job_description="Build Java services with Spring Boot and REST APIs.",
        preferred_title="Java Full Stack Developer",
    )
    assert kept.relevant is True


def test_exact_employer_posting_is_apply_and_provider_listing_is_view_source():
    employer = present_updated_job_action(resolve_apply_action(_job(
        application_url="https://careers.northwind.com/job/48213/java-full-stack-developer",
        availability_status="ACTIVE",
    )))
    assert employer["action_label"] == "Apply on Company Site"
    assert "dice.com" not in employer["url"]

    provider = present_updated_job_action(resolve_apply_action(_job(
        source="apify_dice",
        source_url="https://www.dice.com/job-detail/4821301-java-full-stack-developer",
    )))
    assert provider["action_label"] == "Apply on Dice"
    assert provider["action_label"] != "Apply on Company Site"

    careers = present_updated_job_action(resolve_apply_action(_job(
        application_url="https://careers.northwind.com/careers",
        availability_status="ACTIVE",
    )))
    assert careers["action_label"] == "Open Career Page"


def test_greenhouse_lever_and_workday_exact_jobs_are_apply():
    for url in (
        "https://boards.greenhouse.io/northwind/jobs/48213",
        "https://jobs.lever.co/northwind/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "https://northwind.wd1.myworkdayjobs.com/en-US/Careers/job/Remote/Java-Developer_R48213",
    ):
        action = present_updated_job_action(resolve_apply_action(_job(
            application_url=url, direct_url=url, availability_status="ACTIVE",
        )))
        assert action["action_label"] == "Apply on Company Site"
        assert action["url"] == url


def test_provider_duplicate_does_not_replace_employer_destination():
    from app.services.job_normalization_service import incoming_destination_wins

    employer = "https://northwind.wd1.myworkdayjobs.com/en-US/Careers/job/Remote/Java-Developer_R48213"
    provider = "https://www.adzuna.com/details/48213019"
    assert incoming_destination_wins(provider, employer) is False
    action = present_updated_job_action(resolve_apply_action(_job(
        application_url=employer, direct_url=employer, source_url=provider, availability_status="ACTIVE",
    )))
    assert action["action_label"] == "Apply on Company Site"
    assert action["url"] == employer


def test_closed_job_and_card_details_share_one_action():
    closed = present_updated_job_action(resolve_apply_action(_job(
        application_url="https://careers.northwind.com/job/48213/java-developer",
        availability_status="EXPIRED",
    )))
    assert closed["action_label"] == "Job Closed"
    job = SimpleNamespace(**_job(source="adzuna", source_url="https://www.adzuna.com/details/48213019"))
    card = present_updated_job_action(resolve_apply_action(job))
    details = present_updated_job_action(resolve_apply_action(job))
    assert card["action_label"] == details["action_label"] == "Apply on Adzuna"
    assert card["url"] == details["url"]


def test_query_expansion_stays_bounded():
    titles = discovery_search_titles("React Developer", "react", extra_limit=1)
    assert len(titles) <= 2
    assert titles[0] == "React Developer"
