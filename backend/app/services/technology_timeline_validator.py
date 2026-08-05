"""Technology timeline validation against employment/project years."""

from __future__ import annotations

from app.services.technology_catalog import EARLIEST_USE_YEAR


def technology_allowed_for_years(tech: str, start_year: int | None, end_year: int | None) -> bool:
    earliest = EARLIEST_USE_YEAR.get(tech)
    if earliest is None:
        return True
    if end_year is None and start_year is None:
        return True
    # Technology must have existed by the end of the employment period.
    reference = end_year or start_year
    if reference is None:
        return True
    return reference >= earliest


def validate_technology_timeline(
    selected_technologies: list[str],
    employments: list[dict],
    projects: list[dict] | None = None,
) -> dict:
    warnings = []
    not_added = []

    periods = []
    for emp in employments or []:
        periods.append((emp.get("start_year"), emp.get("end_year"), emp.get("company") or "role"))
    for project in projects or []:
        periods.append((project.get("start_year"), project.get("end_year"), project.get("name") or "project"))

    if not periods:
        return {
            "timelineWarnings": [],
            "technologiesNotAddedDueToTimeline": [],
            "technologyTimelineValidationStatus": "passed",
        }

    # Global earliest employment start used when injecting into older roles
    oldest_start = min((p[0] for p in periods if p[0]), default=None)

    for tech in selected_technologies:
        earliest = EARLIEST_USE_YEAR.get(tech)
        if earliest is None:
            continue
        # If all periods end before earliest release, reject
        if all((p[1] or p[0] or 9999) < earliest for p in periods):
            not_added.append(tech)
            warnings.append(
                f"{tech} was not added because available experience periods end before {earliest}."
            )
            continue
        # Warn when oldest role predates technology (mainstream-period caution)
        if oldest_start and oldest_start < earliest:
            warnings.append(
                f"Timeline caution: {tech} (earliest {earliest}) should not be attributed to roles starting {oldest_start}."
            )

    status = "passed"
    if not_added:
        status = "passed_with_warnings"
    elif warnings:
        status = "passed_with_warnings"

    return {
        "timelineWarnings": warnings,
        "technologiesNotAddedDueToTimeline": not_added,
        "technologyTimelineValidationStatus": status,
    }


def filter_technologies_for_period(
    technologies: list[str],
    start_year: int | None,
    end_year: int | None,
) -> tuple[list[str], list[str]]:
    allowed = []
    blocked = []
    for tech in technologies:
        if technology_allowed_for_years(tech, start_year, end_year):
            allowed.append(tech)
        else:
            blocked.append(tech)
    return allowed, blocked
