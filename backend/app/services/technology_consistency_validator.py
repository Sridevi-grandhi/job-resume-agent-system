"""Technology conflict selection for ATS Analyzer."""

from __future__ import annotations

from app.services.keyword_utils import contains_keyword, normalize_keyword
from app.services.technology_catalog import CONFLICT_GROUPS


def validate_technology_consistency(
    jd_keywords: list[str],
    resume_text: str,
    supported_keywords: list[str],
) -> dict:
    selected = []
    rejected = []
    warnings = []

    # Start from supported JD keywords
    for keyword in supported_keywords:
        canonical = normalize_keyword(keyword)
        if canonical not in selected:
            selected.append(canonical)

    # Resolve conflicts: prefer technologies already present in resume
    for group_name, members in CONFLICT_GROUPS.items():
        present_in_jd = [m for m in members if any(normalize_keyword(k) == m for k in jd_keywords)]
        if len(present_in_jd) <= 1:
            continue

        resume_supported = [m for m in present_in_jd if contains_keyword(resume_text, m)]
        if not resume_supported:
            # Keep first supported if any were somehow selected, else reject extras
            keep = [m for m in present_in_jd if m in selected][:1]
        else:
            keep = resume_supported

        for tech in present_in_jd:
            if tech in keep:
                if tech not in selected and contains_keyword(resume_text, tech):
                    selected.append(tech)
                continue
            if tech in selected:
                selected = [s for s in selected if s != tech]
            if tech not in rejected:
                rejected.append(tech)
            warnings.append(
                f"{group_name}: selected {', '.join(keep) or 'none'}; rejected {tech} to avoid conflict."
            )

    # Multi-cloud: only keep multiple clouds if resume already shows multi-cloud
    clouds = [c for c in ("AWS", "Azure", "GCP") if c in selected]
    if len(clouds) > 1:
        resume_clouds = [c for c in clouds if contains_keyword(resume_text, c)]
        if len(resume_clouds) <= 1:
            keep_cloud = resume_clouds[0] if resume_clouds else clouds[0]
            for cloud in clouds:
                if cloud != keep_cloud:
                    selected = [s for s in selected if s != cloud]
                    if cloud not in rejected:
                        rejected.append(cloud)
                    warnings.append(
                        f"Cloud Providers: kept {keep_cloud}; rejected {cloud} (multi-cloud not evidenced)."
                    )

    # Next.js requires explicit resume support (do not infer from React alone)
    if "Next.js" in selected and not contains_keyword(resume_text, "Next.js"):
        selected = [s for s in selected if s != "Next.js"]
        if "Next.js" not in rejected:
            rejected.append("Next.js")
        warnings.append("Rejected Next.js because React experience alone does not imply Next.js.")

    return {
        "selectedTechnologies": selected,
        "rejectedTechnologies": rejected,
        "conflictWarnings": warnings,
    }
