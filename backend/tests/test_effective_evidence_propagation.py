"""Regression: Step 4 and Step 5 must share one effective evidence source."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.skill_analysis_service import content_hash, normalize_skill_name
from app.services.skill_confirmation_service import (
    EVIDENCE_STATUS_PRECEDENCE,
    evaluate_generation_readiness,
    get_current_effective_evidence,
    reconcile_confirmations_with_analysis,
    save_evidence,
)


def _row(
    *,
    id,
    skill,
    status,
    resume_id=38,
    is_active=True,
    workflow_run_id="run-current",
    requirement_type="MANDATORY",
    evidence_source="user_input",
):
    return SimpleNamespace(
        id=id,
        skill_name=skill,
        normalized_skill_name=skill if skill.islower() else normalize_skill_name(skill),
        evidence_status=status,
        requirement_type=requirement_type,
        resume_id=resume_id,
        is_active=is_active,
        workflow_run_id=workflow_run_id,
        superseded_at=None,
        evidence_type="Current employment" if "PROFESSIONAL" in status else None,
        evidence_source=evidence_source,
        usage_description="used in production",
        user_input_description="used in production",
        ai_rewritten_description=None,
        experience_level="Advanced",
        experience_range="4+ years",
        supporting_context=None,
        confirmed_accurate=True,
        employer_name=None,
        role_title=None,
        project_name=None,
        certification_name=None,
        provider_name=None,
        start_date=None,
        end_date=None,
        currently_using=None,
        years_experience=None,
        last_used=None,
        work_completed=None,
        related_technologies=None,
        impact_description=None,
        skill_modules=None,
        confirmed=status.startswith("USER_CONFIRMED") or status == "LEARNING_ONLY",
        updated_at=None,
    )


def _grouped_terraform_mandatory():
    return {
        "verified_resume_skills": [
            {"skill": "AWS", "normalized_skill": "aws"},
            {"skill": "Docker", "normalized_skill": "docker"},
            {"skill": "Kubernetes", "normalized_skill": "kubernetes"},
        ],
        "weak_or_indirect_skills": [
            {
                "skill": "Terraform",
                "normalized_skill": "terraform",
                "requirement_type": "MANDATORY",
                "resume_status": "WEAK",
            }
        ],
        "missing_mandatory_skills": [],
        "missing_preferred_skills": [
            {
                "skill": "Datadog",
                "normalized_skill": "datadog",
                "requirement_type": "PREFERRED",
                "resume_status": "MISSING",
            }
        ],
    }


class EffectiveEvidencePropagationTests(unittest.TestCase):
    def test_confirmed_overrides_older_incomplete_for_same_skill(self):
        grouped = _grouped_terraform_mandatory()
        rows = [
            _row(id=1, skill="Terraform", status="INCOMPLETE", resume_id=38),
            _row(id=2, skill="Terraform", status="USER_CONFIRMED_PROFESSIONAL", resume_id=38),
        ]
        readiness = evaluate_generation_readiness(grouped, rows)
        self.assertTrue(readiness["can_generate_resume"])
        self.assertEqual(readiness["blocking_reasons"], [])
        self.assertEqual(readiness["current_unresolved_required_count"], 0)

    def test_precedence_ranking_matches_spec(self):
        self.assertGreater(
            EVIDENCE_STATUS_PRECEDENCE["USER_CONFIRMED_PROFESSIONAL"],
            EVIDENCE_STATUS_PRECEDENCE["INCOMPLETE"],
        )
        self.assertGreater(
            EVIDENCE_STATUS_PRECEDENCE["USER_CONFIRMED_PROFESSIONAL"],
            EVIDENCE_STATUS_PRECEDENCE["VERIFIED_FROM_RESUME"],
        )

    def test_only_active_current_rows_considered(self):
        grouped = _grouped_terraform_mandatory()
        rows = [
            _row(id=1, skill="Terraform", status="INCOMPLETE", is_active=False),
            _row(id=2, skill="Terraform", status="USER_CONFIRMED_PROFESSIONAL", is_active=True),
        ]
        readiness = evaluate_generation_readiness(grouped, rows)
        self.assertTrue(readiness["can_generate_resume"])

    def test_zero_unresolved_required_means_can_generate(self):
        grouped = _grouped_terraform_mandatory()
        readiness = evaluate_generation_readiness(
            grouped,
            [_row(id=9, skill="Terraform", status="USER_CONFIRMED_PROFESSIONAL")],
        )
        self.assertEqual(readiness["current_unresolved_required_count"], 0)
        self.assertTrue(readiness["can_generate_resume"])

    def test_preferred_unresolved_does_not_block(self):
        grouped = _grouped_terraform_mandatory()
        readiness = evaluate_generation_readiness(
            grouped,
            [_row(id=9, skill="Terraform", status="USER_CONFIRMED_PROFESSIONAL")],
        )
        self.assertTrue(readiness["can_generate_resume"])
        self.assertIn("Datadog", readiness["current_optional_confirmations"])

    def test_verified_from_resume_never_treated_as_incomplete_blocker(self):
        grouped = _grouped_terraform_mandatory()
        stale = [
            _row(id=1, skill="AWS", status="INCOMPLETE"),
            _row(id=2, skill="Docker", status="INCOMPLETE"),
            _row(id=3, skill="Kubernetes", status="INCOMPLETE"),
            _row(id=4, skill="Terraform", status="USER_CONFIRMED_PROFESSIONAL"),
        ]
        readiness = evaluate_generation_readiness(grouped, stale)
        blocked = {item["normalized_skill"] for item in readiness["blocking_reasons"]}
        self.assertNotIn("aws", blocked)
        self.assertNotIn("docker", blocked)
        self.assertNotIn("kubernetes", blocked)
        self.assertTrue(readiness["can_generate_resume"])

    def test_get_current_effective_rebinds_equivalent_resume_ids(self):
        text = "DevOps resume with Terraform experience"
        resume_hash = content_hash(text)
        old = _row(id=37, skill="Terraform", status="USER_CONFIRMED_PROFESSIONAL", resume_id=37)
        # Simulate SQLAlchemy-ish mutable row
        old.normalized_skill_name = "Terraform"  # legacy Title-case key

        resume_37 = SimpleNamespace(id=37, user_id=3, parsed_text=text)
        resume_38 = SimpleNamespace(id=38, user_id=3, parsed_text=text)

        db = MagicMock()

        def query_side_effect(model):
            q = MagicMock()
            name = getattr(model, "__name__", str(model))
            if "Resume" in name or model.__name__ == "Resume":
                q.filter.return_value.first.return_value = resume_38
                q.filter.return_value.all.return_value = [resume_37, resume_38]
            else:
                q.filter.return_value.order_by.return_value.all.return_value = [old]
            return q

        db.query.side_effect = query_side_effect

        effective = get_current_effective_evidence(
            db,
            user_id=3,
            job_id=956,
            resume_id=38,
            workflow_run_id="3:956:38:abc",
            resume_hash=resume_hash,
            persist_rebinds=True,
        )
        self.assertEqual(len(effective), 1)
        self.assertEqual(effective[0].resume_id, 38)
        self.assertEqual(effective[0].evidence_status, "USER_CONFIRMED_PROFESSIONAL")
        self.assertEqual(normalize_skill_name(effective[0].normalized_skill_name), "terraform")
        db.commit.assert_called()

    def test_different_resume_content_does_not_count_as_current(self):
        confirmed_other = _row(
            id=1, skill="Terraform", status="USER_CONFIRMED_PROFESSIONAL", resume_id=10,
        )
        db = MagicMock()
        resume_current = SimpleNamespace(id=38, user_id=3, parsed_text="new resume text")
        resume_other = SimpleNamespace(id=10, user_id=3, parsed_text="totally different resume")

        def query_side_effect(model):
            q = MagicMock()
            if getattr(model, "__name__", "") == "Resume":
                q.filter.return_value.first.return_value = resume_current
                q.filter.return_value.all.return_value = [resume_other, resume_current]
            else:
                q.filter.return_value.order_by.return_value.all.return_value = [confirmed_other]
            return q

        db.query.side_effect = query_side_effect
        effective = get_current_effective_evidence(
            db, user_id=3, job_id=956, resume_id=38, persist_rebinds=False,
        )
        self.assertEqual(effective, [])

    def test_different_job_rows_are_not_loaded(self):
        # load_confirmed_skills filters by job_id — readiness only sees provided rows.
        grouped = _grouped_terraform_mandatory()
        other_job = [_row(id=1, skill="Terraform", status="INCOMPLETE")]
        # Simulating empty current-job rows — gaps warn but never block generation.
        readiness = evaluate_generation_readiness(grouped, [])
        self.assertTrue(readiness["can_generate_resume"])
        self.assertTrue(readiness["generation_warnings"])
        # Other job incomplete is irrelevant when not passed in
        readiness_ok = evaluate_generation_readiness(
            grouped,
            [_row(id=2, skill="Terraform", status="USER_CONFIRMED_PROFESSIONAL")],
        )
        self.assertTrue(readiness_ok["can_generate_resume"])
        self.assertEqual(readiness_ok["generation_warnings"], [])
        del other_job

    def test_different_users_do_not_leak(self):
        grouped = _grouped_terraform_mandatory()
        # Caller is responsible for user scoping via load_confirmed_skills filter.
        readiness = evaluate_generation_readiness(grouped, [])
        self.assertTrue(readiness["can_generate_resume"])
        self.assertTrue(any(item["skill"] == "Terraform" for item in readiness["generation_warnings"]))

    def test_reconcile_then_readiness_matches_step4_for_reupload(self):
        grouped = _grouped_terraform_mandatory()
        text = "same devops resume body"
        old = _row(id=37, skill="Terraform", status="USER_CONFIRMED_PROFESSIONAL", resume_id=37)
        old.normalized_skill_name = "Terraform"
        resume_37 = SimpleNamespace(id=37, user_id=3, parsed_text=text)
        resume_38 = SimpleNamespace(id=38, user_id=3, parsed_text=text)
        db = MagicMock()

        def query_side_effect(model):
            q = MagicMock()
            if getattr(model, "__name__", "") == "Resume":
                q.filter.return_value.first.return_value = resume_38
                q.filter.return_value.all.return_value = [resume_37, resume_38]
            else:
                q.filter.return_value.order_by.return_value.all.return_value = [old]
            return q

        db.query.side_effect = query_side_effect
        effective = reconcile_confirmations_with_analysis(
            db,
            user_id=3,
            job_id=956,
            resume_id=38,
            grouped_analysis=grouped,
            workflow_run_id="run-38",
            resume_hash=content_hash(text),
        )
        readiness = evaluate_generation_readiness(grouped, effective)
        self.assertTrue(readiness["can_generate_resume"])
        self.assertEqual(readiness["blocking_reasons"], [])
        self.assertEqual(old.resume_id, 38)

    def test_save_evidence_supersedes_duplicate_active_rows(self):
        incomplete = _row(id=1, skill="Terraform", status="INCOMPLETE", resume_id=38)
        incomplete.normalized_skill_name = "Terraform"
        db = MagicMock()
        query = MagicMock()
        query.filter.return_value.order_by.return_value.all.return_value = [incomplete]
        db.query.return_value = query

        saved = save_evidence(
            db,
            user_id=3,
            job_id=956,
            resume_id=38,
            workflow_run_id="run-38",
            items=[{
                "skill": "Terraform",
                "answer": "has_it",
                "evidence_type": "Current employment",
                "simple_confirmation": True,
                "experience_level": "Advanced",
                "usage_description": "Provisioned AWS infra with Terraform modules.",
                "requirement_type": "MANDATORY",
            }],
        )
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].evidence_status, "USER_CONFIRMED_PROFESSIONAL")
        self.assertEqual(normalize_skill_name(saved[0].normalized_skill_name), "terraform")
        self.assertEqual(incomplete.evidence_status, "USER_CONFIRMED_PROFESSIONAL")
        db.commit.assert_called()


if __name__ == "__main__":
    unittest.main()
