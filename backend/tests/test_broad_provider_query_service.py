"""Tests for building broad-provider (JSearch) search queries from real,
logged-in users' preferences — never hardcoded, always deduplicated."""

from __future__ import annotations

from datetime import time as dt_time

from app.models.user_preference_model import UserPreference
from app.services.broad_provider_query_service import get_active_preference_queries


def _pref(db_session, user, **overrides):
    defaults = dict(
        user_id=user.id,
        job_title="Java Full Stack Developer",
        location="Remote",
        experience="5",
        work_mode="Remote",
        job_type="Full-time",
        skills="Java, Spring Boot",
        schedule_time=dt_time(8, 0),
        timezone="America/Chicago",
    )
    defaults.update(overrides)
    row = UserPreference(**defaults)
    db_session.add(row)
    db_session.commit()
    return row


def test_builds_one_query_per_distinct_preference_combination(db_session, test_user):
    _pref(db_session, test_user)
    queries = get_active_preference_queries(db_session)
    assert queries[0]["job_title"] == "Java Full Stack Developer"
    assert queries[0]["location"] == "Remote"
    assert queries[0]["work_mode"] == "Remote"
    assert len(queries) == 2
    assert queries[1]["job_title"] == "Spring Boot Developer"
    assert queries[1]["location"] == "Remote"


def test_deduplicates_identical_preference_combinations_across_users(db_session, test_user, second_user):
    _pref(db_session, test_user, job_title="SDET Automation Engineer", location="Austin, TX", work_mode="Hybrid")
    _pref(db_session, second_user, job_title="SDET Automation Engineer", location="Austin, TX", work_mode="Hybrid")
    queries = get_active_preference_queries(db_session)
    titles = [query["job_title"] for query in queries]
    assert titles[0] == "SDET Automation Engineer"
    assert titles.count("SDET Automation Engineer") == 1


def test_distinct_preferences_each_produce_their_own_query(db_session, test_user, second_user):
    _pref(db_session, test_user, job_title="Data Engineer", location="Chicago, IL")
    _pref(db_session, second_user, job_title="DevOps Engineer", location="Seattle, WA")
    queries = get_active_preference_queries(db_session)
    titles = {query["job_title"] for query in queries}
    assert {"Data Engineer", "DevOps Engineer"} <= titles
    assert "Java Developer" not in titles


def test_preference_with_blank_job_title_is_skipped(db_session, test_user):
    _pref(db_session, test_user, job_title="   ")
    assert get_active_preference_queries(db_session) == []


def test_no_preferences_at_all_returns_empty_list(db_session):
    assert get_active_preference_queries(db_session) == []


def test_max_queries_bounds_the_result(db_session, test_user, second_user):
    _pref(db_session, test_user, job_title="Role One", location="City One")
    _pref(db_session, second_user, job_title="Role Two", location="City Two")
    queries = get_active_preference_queries(db_session, max_queries=1)
    assert len(queries) == 1
