import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderRoute } from "./render.jsx";
import { makeApp } from "./mockApp";
import AtsAnalyzerPage from "../pages/AtsAnalyzerPage";

let app;
vi.mock("../context/AppContext", () => ({ useApp: () => app }));

const job = { id: 42, title: "Java Engineer", company: "Acme", location: "Remote", source: "User Selected Job", description: "A detailed Java backend position with enough content to be analyzed safely.", job_url: "https://jobs.example.test/42" };
const groups = {
  verified_resume_skills: [{ skill: "Maven", category: "Build Tool", match_type: "EXACT", evidence_strength: "strong", resume_status: "VERIFIED", evidence_snippets: ["Used Maven builds"] }],
  weak_or_indirect_skills: [{ skill: "AWS", category: "Cloud", match_type: "INDIRECT", evidence_strength: "weak", requirement_type: "PREFERRED", interview: { modules: ["Lambda", "EC2"] } }],
  missing_mandatory_skills: [{ skill: "Kafka", category: "Messaging", importance: "MANDATORY", interview: { modules: ["Producers"] } }],
  missing_preferred_skills: [{ skill: "Docker", category: "DevOps", importance: "PREFERRED" }],
  resume_employments: [{ index: 0, company: "Acme", title: "Engineer", dates: "2021-Present" }],
  can_generate_resume: true,
  blocking_reasons: [],
  generation_warnings: [{
    skill: "Kafka",
    reason: "Kafka is not supported by your current resume or confirmed evidence. It will be excluded from the optimized resume and may limit your maximum match score.",
    evidence_status: "MISSING",
    severity: "warning",
    code: "UNSUPPORTED_REQUIREMENT",
  }],
  current_required_confirmations: ["Kafka"],
  current_optional_confirmations: ["AWS", "Docker"],
  generation_blocked_message: "",
  generation_warning_message: "Kafka is not supported and will remain a gap. Generation can continue; unsupported requirements will be excluded.",
  active_confirmation_counts: { verified_from_resume: 1, user_confirmed_with_evidence: 0, learning_only: 0, not_possessed: 0, incomplete: 0 },
  evidence: [],
  evidence_history: [],
  evidence_summary: {
    evidence: [],
    incomplete: [],
    user_confirmed: [],
    evidence_history: [],
    counts: { verified_from_resume: 1, user_confirmed_with_evidence: 0, learning_only: 0, not_possessed: 0, incomplete: 0 },
  },
};

beforeEach(() => {
  app = makeApp({ jobs: [job], latestResume: { id: 4, file_name: "resume.txt" }, atsSelectedJobId: "42", missingSkillsGroups: groups });
  app.loadMissingSkills.mockResolvedValue(groups);
  app.loadAtsResultForJob.mockResolvedValue(null);
  app.loadSkillConfirmations.mockResolvedValue({ evidence: [] });
  globalThis.fetch = vi.fn(() => Promise.resolve(new Response(JSON.stringify(job), { status: 200 })));
});

describe("ATS optimizer", () => {
  it("shows a threshold warning, not a role mismatch, for exact alignment below 65", async () => {
    globalThis.fetch = vi.fn((url) => Promise.resolve(new Response(JSON.stringify(
      String(url).includes("/eligibility/")
        ? { optimization_allowed: false, original_score: 58, role_alignment: "EXACT", resume_role_family: "DevOps", job_role_family: "DevOps", missing_mandatory_skills: ["Terraform"], component_scores: { role_alignment: { earned: 20, max: 20 }, mandatory_skills: { earned: 21, max: 35 } } }
        : job
    ), { status: 200 })));
    renderRoute(<AtsAnalyzerPage />, "/optimizer/42", "/optimizer/:jobId");
    await userEvent.click(screen.getByRole("button", { name: /^continue$/i }));
    await userEvent.click(screen.getByRole("button", { name: /analyze job description/i }));
    expect(screen.getByText(/Role aligned, but ATS score is below 65%/i)).toBeInTheDocument();
    expect(screen.queryByText(/Major role mismatch detected/i)).not.toBeInTheDocument();
    expect(screen.getByText("20/20")).toBeInTheDocument();
    expect(screen.getByText("21/35")).toBeInTheDocument();
  });

  it("loads the exact route job and groups each requirement correctly", async () => {
    renderRoute(<AtsAnalyzerPage />, "/optimizer/42", "/optimizer/:jobId");
    await waitFor(() => expect(app.setAtsSelectedJobId).toHaveBeenCalledWith("42"));
    expect(screen.getByText("Java Engineer")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /view original job/i })).toHaveAttribute("href", job.job_url);
    await userEvent.click(screen.getByRole("button", { name: /^continue$/i }));
    await userEvent.click(screen.getByRole("button", { name: /analyze job description/i }));
    expect(app.loadMissingSkills).toHaveBeenCalledWith(42);
    expect(screen.getByText("Maven")).toBeInTheDocument();
    expect(screen.getByText(/verified from your resume \(1\)/i)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /possible evidence found/i })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /not found in resume/i })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /skills not found in your resume/i })).not.toBeInTheDocument();
    expect(screen.getAllByText(/Kafka/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Docker/).length).toBeGreaterThan(0);
    // AWS is weak/indirect evidence (found), so it must never be presented as "not found".
    const notFoundSection = screen.getByRole("heading", { name: /not found in resume/i }).closest(".ats-skill-category-group");
    expect(within(notFoundSection).queryByText("AWS")).not.toBeInTheDocument();
  });

  it("renders separate scores and exact improvement", async () => {
    app.atsResult = {
      workflow_id: 8, workflow_status: "COMPLETED", job, optimized_resume_id: 91,
      original_match_percentage: 57, optimized_match_percentage: 82, score_improvement: 25,
      safe_to_apply: true, why_not_95: "Kafka remains unsupported.",
      final_validation: { status: "passed", warnings: [] }, generated_resume: { plain_text: "Safe resume" },
      validation_passed: true,
    };
    globalThis.fetch = vi.fn((url) => {
      if (String(url).includes("/api/optimizer/resume/")) {
        return Promise.resolve(new Response(JSON.stringify({
          optimized_resume_id: 91,
          generated_text: "Safe resume",
          final_text: "Safe resume",
          document: {
            templateId: "professional-ats", pageSize: "letter", fontFamily: "Calibri", fontSize: 11, spacing: "normal", align: "left",
            header: { name: "Alex", title: "Engineer", email: "a@example.com", phone: "", location: "", links: [] },
            sections: [{ id: "s1", type: "summary", heading: "Professional Summary", content: "Safe resume" }],
          },
          generated_score: 82, edited_score: 82, validation_status: "passed",
          score: { jd_match: 82, keyword_coverage: { matched: 4, required: 6 }, mandatory_requirements: { covered: 3, total: 4, missing: ["Kafka"] }, preferred_requirements: { covered: 1, total: 2, missing: [] }, ats_formatting: 100 },
        }), { status: 200 }));
      }
      return Promise.resolve(new Response(JSON.stringify(job), { status: 200 }));
    });
    renderRoute(<AtsAnalyzerPage />, "/optimizer/42", "/optimizer/:jobId");
    fireEvent.click(await screen.findByText(/Generate, Review/i));
    expect(await screen.findByText("57%")).toBeInTheDocument();
    expect(screen.getAllByText("82%").length).toBeGreaterThan(0);
    expect(screen.getByText("+25")).toBeInTheDocument();
    expect(screen.getByText(/Kafka remains unsupported/i)).toBeInTheDocument();
    expect(screen.getByRole("toolbar", { name: /resume editor toolbar/i })).toBeInTheDocument();
  });

  it("shows Ollama only when backend explicitly identifies it", async () => {
    app.atsResult = {
      workflow_status: "COMPLETED", job,
      validation_passed: true,
      final_validation: { status: "passed", warnings: [] },
      generation_metadata: {
        generation_method: "llm", llm_used: true, fallback_used: false,
        llm_provider: "openai_compatible", llm_model: "llama3.1:8b", llm_service: "ollama",
      },
    };
    renderRoute(<AtsAnalyzerPage />, "/optimizer/42", "/optimizer/:jobId");
    fireEvent.click(screen.getByText(/Generate, Review/i));
    expect(screen.getAllByText("AI Optimized · llama3.1:8b").length).toBeGreaterThan(0);
  });

  it("does not show AI Optimized when workflow validation failed", async () => {
    app.atsResult = {
      workflow_status: "VALIDATION_FAILED", job,
      validation_passed: false,
      final_validation: { status: "failed", errors: ["Protected field changed or missing: name"] },
      generation_metadata: {
        generation_method: "llm", llm_used: true, fallback_used: false,
        llm_provider: "openai", llm_model: "gpt-5-mini",
      },
    };
    renderRoute(<AtsAnalyzerPage />, "/optimizer/42", "/optimizer/:jobId");
    fireEvent.click(screen.getByText(/Generate, Review/i));
    expect(screen.getByText(/AI generation completed, but final resume validation failed/i)).toBeInTheDocument();
    expect(screen.queryByText(/AI Optimized · gpt-5-mini/)).not.toBeInTheDocument();
  });

  it("shows deterministic fallback without mislabeling provider as Ollama", async () => {
    app.atsResult = {
      workflow_status: "COMPLETED", job,
      generation_metadata: {
        generation_method: "deterministic_fallback", llm_used: false,
        fallback_used: true, fallback_reason: "connection_failed",
        llm_provider: "openai_compatible", llm_model: "llama3.1:8b", llm_service: null,
      },
    };
    renderRoute(<AtsAnalyzerPage />, "/optimizer/42", "/optimizer/:jobId");
    fireEvent.click(screen.getByText(/Generate, Review/i));
    expect(screen.getByText(/Optimized locally · AI response could not be safely applied/)).toBeInTheDocument();
    expect(screen.queryByText(/AI Optimized/)).not.toBeInTheDocument();
  });

  it("exposes only five wizard steps and a Step 5 workspace for completed results", async () => {
    app.atsResult = {
      workflow_id: 8, workflow_status: "COMPLETED", job, optimized_resume_id: 88,
      original_match_percentage: 78, optimized_match_percentage: 79, score_improvement: 1,
      safe_to_apply: true, final_validation: { status: "passed", warnings: ["Review formatting"] },
      generated_resume: { plain_text: "Generated safe resume", formatted_preview: "Generated safe resume" },
      original_analysis: { matched_keywords: ["Java"], missing_keywords: ["Kafka"] },
      optimized_analysis: { matched_keywords: ["Java", "Kafka"], missing_keywords: [] },
      content_changed: true, confirmed_skills_used: ["Kafka"], why_not_95: "One preferred skill remains.",
      score_status: "STRONG_MATCH", role_alignment: "EXACT", resume_role_families: ["Java Backend"], target_job_role: "Java Backend",
      matched_core_skills: ["Java"], matched_transferable_skills: ["AWS"], critical_missing_skills: ["Docker"],
      newly_matched_keywords: ["Kafka"], unsupported_keywords_not_added: ["Kubernetes"],
      final_suggestions: ["Review the final resume"], final_recommendation: "Apply after review",
      original_resume: "Original resume text",
      validation_passed: true,
      improvement_report: {
        meters: { resume_quality: { before: 70, after: 80 }, ats_match: { before: 78, after: 79 } },
        new_skills_added: ["Kafka"], keywords_added: ["Kafka"], sections_improved: ["Summary"], bullets_rewritten: 1,
        missing_skills_remaining: ["Docker"], explanation: ["Added verified evidence"], ats_recommendations: ["Review wording"],
      },
    };
    globalThis.fetch = vi.fn((url) => {
      if (String(url).includes("/api/optimizer/resume/") && String(url).includes("/88")) {
        return Promise.resolve(new Response(JSON.stringify({
          optimized_resume_id: 88,
          generated_text: "Generated safe resume",
          final_text: "Generated safe resume",
          document: {
            templateId: "professional-ats", pageSize: "letter", fontFamily: "Calibri", fontSize: 11, spacing: "normal", align: "left",
            header: { name: "Pat", title: "Engineer", email: "p@example.com", phone: "", location: "", links: [] },
            sections: [{ id: "s1", type: "summary", heading: "Professional Summary", content: "Generated safe resume" }],
          },
          generated_score: 79, edited_score: 79, validation_status: "passed",
          safe_to_download: true,
          score: {
            jd_match: 79,
            keyword_coverage: { matched: 5, required: 6 },
            mandatory_requirements: { covered: 4, total: 5, missing: [] },
            preferred_requirements: { covered: 1, total: 2, missing: ["Docker"] },
            ats_formatting: 100,
            remaining_gaps: { unsupported: ["Kubernetes"], preferred_gaps: ["Docker"] },
          },
        }), { status: 200 }));
      }
      return Promise.resolve(new Response(JSON.stringify(job), { status: 200 }));
    });
    renderRoute(<AtsAnalyzerPage />, "/optimizer/42", "/optimizer/:jobId");
    expect(screen.queryByText("Validate Resume")).not.toBeInTheDocument();
    expect(screen.queryByText("Compare Scores")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText(/Generate, Review/i));
    expect(screen.getByRole("heading", { name: /Step 5/i })).toBeInTheDocument();
    expect(await screen.findByText("+1")).toBeInTheDocument();
    expect(screen.getByRole("toolbar", { name: /resume editor toolbar/i })).toBeInTheDocument();
    expect(screen.getByText(/Confirmed skills incorporated/i)).toBeInTheDocument();
    expect(screen.getByText(/Unsupported JD requirements excluded/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^regenerate$/i })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /recalculate score/i }).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /download docx/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /download pdf/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /download txt/i })).toBeInTheDocument();
  });

  it("recalculates score by saving current editor content without calling OpenAI", async () => {
    app.atsResult = {
      workflow_id: 9, workflow_status: "COMPLETED", job, optimized_resume_id: 91,
      original_match_percentage: 70, optimized_match_percentage: 80, score_improvement: 10,
      safe_to_apply: true, final_validation: { status: "passed", warnings: [] },
      generated_resume: { plain_text: "Optimized resume body" },
      validation_passed: true,
    };
    const patchCalls = [];
    globalThis.fetch = vi.fn((url, options = {}) => {
      const href = String(url);
      if (href.includes("/api/optimizer/resume/") && options.method === "PATCH") {
        patchCalls.push(JSON.parse(options.body || "{}"));
        return Promise.resolve(new Response(JSON.stringify({
          persisted: true,
          edited_score: 80,
          previous_edited_score: 80,
          score_unchanged: true,
          score_unchanged_reason: "Score remains 80%. Your edits did not change any scored ATS requirements.",
          safe_to_download: true,
          validation_passed: true,
          validation: { status: "passed" },
          score: {
            jd_match: 80,
            component_scores: { mandatory_skills: { earned: 30, max: 35 } },
            keyword_coverage: { matched: 4, required: 5 },
            mandatory_requirements: { covered: 3, total: 4, missing: [] },
            preferred_requirements: { covered: 1, total: 2, missing: [] },
          },
          document: {
            templateId: "professional-ats", pageSize: "letter", fontFamily: "Calibri", fontSize: 11, spacing: "normal", align: "left",
            header: { name: "Pat", title: "", email: "p@example.com", phone: "", location: "", links: [] },
            sections: [{ id: "s1", type: "summary", heading: "Professional Summary", content: "Optimized resume body" }],
          },
        }), { status: 200 }));
      }
      if (href.includes("/api/optimizer/resume/") && href.includes("/91")) {
        return Promise.resolve(new Response(JSON.stringify({
          optimized_resume_id: 91,
          final_text: "Optimized resume body",
          validation_status: "passed",
          safe_to_download: true,
          edited_score: 80,
          document: {
            templateId: "professional-ats", pageSize: "letter", fontFamily: "Calibri", fontSize: 11, spacing: "normal", align: "left",
            header: { name: "Pat", title: "", email: "p@example.com", phone: "", location: "", links: [] },
            sections: [{ id: "s1", type: "summary", heading: "Professional Summary", content: "Optimized resume body" }],
          },
          score: { jd_match: 80, keyword_coverage: { matched: 4, required: 5 }, mandatory_requirements: { covered: 3, total: 4, missing: [] }, preferred_requirements: { covered: 1, total: 2, missing: [] } },
        }), { status: 200 }));
      }
      return Promise.resolve(new Response(JSON.stringify(job), { status: 200 }));
    });
    renderRoute(<AtsAnalyzerPage />, "/optimizer/42", "/optimizer/:jobId");
    fireEvent.click(screen.getByText(/Generate, Review/i));
    expect(await screen.findByRole("button", { name: /download docx/i })).toBeInTheDocument();
    const recalculateButtons = screen.getAllByRole("button", { name: /recalculate score/i });
    await userEvent.click(recalculateButtons[0]);
    expect(await screen.findByText(/Score remains 80%/i)).toBeInTheDocument();
    expect(patchCalls.length).toBeGreaterThan(0);
    expect(patchCalls[0].persist).not.toBe(false);
    expect(patchCalls[0].score_only).not.toBe(true);
    expect(patchCalls[0].action).toBe("recalculate");
  });

  it("renders failure and retry without separate validation workflow steps", async () => {
    app.messages = { ats: "Unsupported methodology introduced: Scrum" };
    app.atsResult = { workflow_id: 10, workflow_status: "VALIDATION_FAILED", job, score_status: "NOT_RECOMMENDED", role_alignment: "MISMATCH", safe_to_apply: false, error_message: "Unsupported methodology introduced: Scrum", final_validation: { status: "failed", warnings: ["Unsupported claim"] } };
    renderRoute(<AtsAnalyzerPage />, "/optimizer/42", "/optimizer/:jobId");
    await userEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(app.retryAtsWorkflow).toHaveBeenCalledWith(10);
    fireEvent.click(screen.getByText(/Generate, Review/i));
    expect(screen.getByRole("heading", { name: /Step 5/i })).toBeInTheDocument();
    expect(screen.getAllByText("Unsupported methodology introduced: Scrum")).toHaveLength(1);
    expect(screen.getByText("Calculating Optimized Match").closest("li")).not.toHaveClass("done");
    expect(screen.getByText("Completed").closest("li")).not.toHaveClass("done");
  });

  it("shows mismatch warning and optimized preview in Step 5", async () => {
    app.atsResult = {
      workflow_id: 11, workflow_status: "COMPLETED", job, optimized_resume_id: 77,
      original_match_percentage: 25, optimized_match_percentage: 24, score_improvement: -1,
      score_status: "NOT_RECOMMENDED", role_alignment: "MISMATCH", safe_to_apply: false,
      generated_resume: { plain_text: "Truth-preserving resume" }, final_validation: { status: "passed", warnings: [] },
      validation_passed: true,
    };
    globalThis.fetch = vi.fn((url) => {
      if (String(url).includes("/api/optimizer/resume/")) {
        return Promise.resolve(new Response(JSON.stringify({
          optimized_resume_id: 77,
          generated_text: "Truth-preserving resume",
          final_text: "Truth-preserving resume",
          document: {
            templateId: "professional-ats", pageSize: "letter", fontFamily: "Calibri", fontSize: 11, spacing: "normal", align: "left",
            header: { name: "Sam", title: "", email: "", phone: "", location: "", links: [] },
            sections: [{ id: "s1", type: "summary", heading: "Professional Summary", content: "Truth-preserving resume" }],
          },
          generated_score: 24, edited_score: 24, validation_status: "passed",
          score: { jd_match: 24, keyword_coverage: { matched: 1, required: 5 }, mandatory_requirements: { covered: 0, total: 3, missing: ["X"] }, preferred_requirements: { covered: 0, total: 1, missing: [] }, ats_formatting: 100 },
        }), { status: 200 }));
      }
      return Promise.resolve(new Response(JSON.stringify(job), { status: 200 }));
    });
    renderRoute(<AtsAnalyzerPage />, "/optimizer/42", "/optimizer/:jobId");
    fireEvent.click(screen.getByText(/Generate, Review/i));
    expect(await screen.findByText("-1")).toBeInTheDocument();
    expect(screen.getByText(/Major role mismatch detected/i)).toBeInTheDocument();
    expect(screen.getByDisplayValue("Truth-preserving resume")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /download docx/i })).toBeInTheDocument();
  });

  it("allows optimization continue with unsupported required skills as warnings", async () => {
    renderRoute(<AtsAnalyzerPage />, "/optimizer/42", "/optimizer/:jobId");
    await userEvent.click(screen.getByRole("button", { name: /^continue$/i }));
    await userEvent.click(screen.getByRole("button", { name: /analyze job description/i }));
    expect(screen.getByText(/3 skills need review/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /continue to resume generation/i })).toBeEnabled();
    expect(screen.getByText(/required skill/i)).toBeInTheDocument();
    expect(screen.getByText(/^Kafka$/i)).toBeInTheDocument();
  });

  it("keeps Continue enabled when mandatory unresolved evidence remains", async () => {
    app.missingSkillsGroups = {
      ...groups,
      can_generate_resume: true,
      blocking_reasons: [],
      generation_warnings: [{
        skill: "Terraform",
        reason: "Terraform evidence is incomplete. It will be excluded from the optimized resume and may limit your maximum match score.",
        evidence_status: "INCOMPLETE",
        severity: "warning",
        code: "INCOMPLETE_EVIDENCE",
      }],
      generation_warning_message: "Terraform is not supported and will remain a gap. Generation can continue; unsupported requirements will be excluded.",
      evidence: [{ id: 9, skill: "Terraform", evidence_status: "INCOMPLETE", requirement_type: "MANDATORY", is_active: true }],
      evidence_summary: {
        evidence: [{ id: 9, skill: "Terraform", evidence_status: "INCOMPLETE", requirement_type: "MANDATORY", is_active: true }],
        incomplete: [{ id: 9, skill: "Terraform", evidence_status: "INCOMPLETE", requirement_type: "MANDATORY", is_active: true }],
        user_confirmed: [],
        counts: { verified_from_resume: 1, user_confirmed_with_evidence: 0, learning_only: 0, not_possessed: 0, incomplete: 1 },
      },
    };
    renderRoute(<AtsAnalyzerPage />, "/optimizer/42", "/optimizer/:jobId");
    fireEvent.click(screen.getByText("Review Evidence"));
    expect(screen.getByRole("button", { name: /continue to optimization/i })).toBeEnabled();
    const unsupportedHeading = screen.getByRole("heading", { name: /unsupported requirements/i });
    expect(unsupportedHeading.nextElementSibling.textContent).toMatch(/Terraform/i);
  });

  it("shows Healthcare gap warning on Step 5 without Failed status and keeps Generate enabled", async () => {
    app.missingSkillsGroups = {
      ...groups,
      can_generate_resume: true,
      blocking_reasons: [],
      generation_warnings: [{
        skill: "Healthcare",
        reason: "Healthcare experience is not supported by your current resume or confirmed evidence. It will be excluded from the optimized resume and may limit your maximum match score.",
        evidence_status: "INCOMPLETE",
        severity: "warning",
        code: "INCOMPLETE_EVIDENCE",
      }],
      generation_warning_message: "Healthcare is not supported and will remain a gap. Generation can continue; unsupported requirements will be excluded.",
      missing_mandatory_skills: [{ skill: "Healthcare", importance: "MANDATORY" }],
      evidence: [{ id: 11, skill: "Healthcare", evidence_status: "INCOMPLETE", requirement_type: "MANDATORY", is_active: true }],
      evidence_summary: {
        evidence: [{ id: 11, skill: "Healthcare", evidence_status: "INCOMPLETE", requirement_type: "MANDATORY", is_active: true }],
        incomplete: [{ id: 11, skill: "Healthcare", evidence_status: "INCOMPLETE", requirement_type: "MANDATORY", is_active: true }],
        user_confirmed: [],
        counts: { verified_from_resume: 1, user_confirmed_with_evidence: 0, learning_only: 0, not_possessed: 0, incomplete: 1 },
      },
    };
    app.messages = { ats: "" };
    app.atsResult = null;
    app.loadSkillConfirmations.mockResolvedValue({
      can_generate_resume: true,
      blocking_reasons: [],
      generation_warnings: app.missingSkillsGroups.generation_warnings,
      evidence: app.missingSkillsGroups.evidence,
    });
    renderRoute(<AtsAnalyzerPage />, "/optimizer/42", "/optimizer/:jobId");
    fireEvent.click(screen.getByText(/Generate, Review/i));
    expect(await screen.findByRole("heading", { name: /step 5/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /generate optimized resume/i })).toBeEnabled();
    expect(screen.getByText(/Remaining Gaps \(1\)/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Healthcare/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Mandatory requirement — no supported evidence/i)).toBeInTheDocument();
    expect(screen.queryAllByText(/Healthcare is not supported and will remain a gap/i)).toHaveLength(0);
    expect(screen.queryByText(/^Failed: Required evidence incomplete: Healthcare$/i)).not.toBeInTheDocument();
    expect(screen.getByText(/Ready to generate optimized resume/i)).toBeInTheDocument();
  });

  it("collects compact evidence only for selected skills and saves several together", async () => {
    renderRoute(<AtsAnalyzerPage />, "/optimizer/42", "/optimizer/:jobId");
    await userEvent.click(screen.getByRole("button", { name: /^continue$/i }));
    await userEvent.click(screen.getByRole("button", { name: /analyze job description/i }));

    await userEvent.click(screen.getByRole("checkbox", { name: /KafkaRequired/i }));
    await userEvent.click(screen.getByRole("checkbox", { name: /AWSPreferred/i }));
    await userEvent.click(screen.getByRole("button", { name: /select skills i have/i }));
    expect(screen.getByText(/tell us about the skills you selected/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/Briefly describe how you used Docker/i)).not.toBeInTheDocument();

    const kafka = screen.getByText("Kafka").closest(".ats-compact-evidence-row");
    expect(within(kafka).getByLabelText("Experience level")).toHaveValue("");
    expect(within(kafka).getByLabelText(/Where have you used it/i)).toHaveValue("");
    expect(within(kafka).getByLabelText(/Briefly describe how you used Kafka/i)).toHaveValue("");
    expect(within(kafka).getByText(/Selected — evidence needed/i)).toBeInTheDocument();
    await userEvent.selectOptions(within(kafka).getByLabelText("Experience level"), "Advanced");
    await userEvent.selectOptions(within(kafka).getByLabelText(/Where have you used it/i), "Current employment");
    await userEvent.type(within(kafka).getByLabelText(/Briefly describe how you used Kafka/i), "Implemented event delivery");
    const aws = screen.getByText("AWS").closest(".ats-compact-evidence-row");
    await userEvent.selectOptions(within(aws).getByLabelText("Experience level"), "Intermediate");
    await userEvent.selectOptions(within(aws).getByLabelText(/Where have you used it/i), "Personal project");
    await userEvent.type(within(aws).getByLabelText(/Briefly describe how you used AWS/i), "Deployed my personal application.");
    await userEvent.click(screen.getByRole("button", { name: /save evidence/i }));
    // Drafts stay frontend-only until Save Evidence — no answer:null INCOMPLETE stubs.
    expect(app.saveConfirmedSkills).toHaveBeenCalledTimes(1);
    expect(app.saveConfirmedSkills).toHaveBeenCalledWith(42, expect.arrayContaining([
      expect.objectContaining({ skill: "Kafka", answer: "has_it", simple_confirmation: true }),
      expect.objectContaining({ skill: "AWS", answer: "has_it", simple_confirmation: true }),
    ]));
  });

  it("seeds saved evidence and renders review and generation summaries", async () => {
    const active = [
      { id: 1, skill: "AWS", confirmed: true, evidence_status: "USER_CONFIRMED_PROJECT", requirement_type: "PREFERRED", is_active: true, evidence_type: "Personal project", project_name: "Cloud app", experience_level: "Intermediate", experience_range: "1-2 years", usage_description: "Deployed app", confirmed_accurate: true },
      { id: 2, skill: "Kafka", evidence_status: "LEARNING_ONLY", requirement_type: "MANDATORY", is_active: true, evidence_type: "Learning only" },
      { id: 3, skill: "Docker", evidence_status: "REJECTED", requirement_type: "PREFERRED", is_active: true },
    ];
    app.confirmedSkills = active;
    app.missingSkillsGroups = {
      ...groups,
      can_generate_resume: true,
      blocking_reasons: [],
      generation_warnings: [],
      generation_warning_message: "",
      generation_blocked_message: "",
      current_required_confirmations: [],
      active_confirmation_counts: { verified_from_resume: 1, user_confirmed_with_evidence: 1, learning_only: 1, not_possessed: 1, incomplete: 0 },
      evidence: active,
      evidence_history: [{ id: 99, skill: "Kubernetes", evidence_status: "INCOMPLETE", is_active: false }],
      evidence_summary: {
        evidence: active,
        user_confirmed: [active[0]],
        incomplete: [],
        optional_preferred: [active[0], active[2]],
        evidence_history: [{ id: 99, skill: "Kubernetes", evidence_status: "INCOMPLETE", is_active: false }],
        counts: { verified_from_resume: 1, user_confirmed_with_evidence: 1, learning_only: 1, not_possessed: 1, incomplete: 0 },
      },
    };
    app.loadSkillConfirmations.mockResolvedValue({ evidence: active, can_generate_resume: true });
    app.analyzeAndOptimize.mockResolvedValue({ workflow_status: "COMPLETED", optimized_resume_id: 55 });
    app.atsResult = { workflow_id: 8, workflow_status: "COMPLETED", optimized_resume_id: 55, job, original_match_percentage: 57, optimized_match_percentage: 82, safe_to_apply: true, generated_resume: { plain_text: "Generated" }, final_validation: { status: "passed", warnings: [] }, validation_passed: true };
    globalThis.fetch = vi.fn((url) => {
      if (String(url).includes("/api/optimizer/resume/")) {
        return Promise.resolve(new Response(JSON.stringify({
          optimized_resume_id: 55,
          generated_text: "Generated",
          final_text: "Generated",
          document: {
            templateId: "professional-ats", pageSize: "letter", fontFamily: "Calibri", fontSize: 11, spacing: "normal", align: "left",
            header: { name: "Pat", title: "", email: "", phone: "", location: "", links: [] },
            sections: [{ id: "s1", type: "summary", heading: "Professional Summary", content: "Generated" }],
          },
          generated_score: 82, edited_score: 82, validation_status: "passed",
          score: { jd_match: 82, keyword_coverage: { matched: 2, required: 3 }, mandatory_requirements: { covered: 1, total: 1, missing: [] }, preferred_requirements: { covered: 0, total: 0, missing: [] }, ats_formatting: 100 },
        }), { status: 200 }));
      }
      return Promise.resolve(new Response(JSON.stringify(job), { status: 200 }));
    });
    renderRoute(<AtsAnalyzerPage />, "/optimizer/42", "/optimizer/:jobId");
    fireEvent.click(screen.getByText("Review Evidence"));
    expect(screen.getByText(/User-confirmed with evidence:/i)).toBeInTheDocument();
    const userConfirmedHeading = screen.getByRole("heading", { name: /current user-confirmed evidence/i });
    const userConfirmedList = userConfirmedHeading.nextElementSibling;
    expect(within(userConfirmedList).getByText(/AWS/i)).toBeInTheDocument();
    expect(within(userConfirmedList).getByText(/Project experience confirmed/i)).toBeInTheDocument();
    expect(within(userConfirmedList).queryByText(/USER_CONFIRMED_PROJECT/i)).not.toBeInTheDocument();
    const unresolvedHeading = screen.getByRole("heading", { name: /unsupported requirements/i });
    expect(within(unresolvedHeading.nextElementSibling).queryByText(/Kubernetes/i)).not.toBeInTheDocument();
    expect(within(unresolvedHeading.nextElementSibling).getByText(/None/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /continue to optimization/i })).toBeEnabled();
    await userEvent.click(screen.getAllByRole("button", { name: /remove evidence/i })[0]);
    const confirmGroup = screen.getByRole("group", { name: /confirm removal of aws/i });
    expect(confirmGroup).toBeInTheDocument();
    await userEvent.click(within(confirmGroup).getByRole("button", { name: /^cancel$/i }));
    expect(app.deleteConfirmedSkill).not.toHaveBeenCalled();
    await userEvent.click(screen.getAllByRole("button", { name: /remove evidence/i })[0]);
    const confirmGroupAgain = screen.getByRole("group", { name: /confirm removal of aws/i });
    await userEvent.click(within(confirmGroupAgain).getByRole("button", { name: /^remove$/i }));
    expect(app.deleteConfirmedSkill).toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /continue to optimization/i }));
    expect(screen.getByRole("heading", { name: /step 5/i })).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: /^regenerate$/i }));
    expect(app.analyzeAndOptimize).toHaveBeenCalledWith(42, true);
  });

  it("enables Generate Resume only when can_generate_resume is true and forces a new run", async () => {
    app.missingSkillsGroups = {
      ...groups,
      can_generate_resume: true,
      blocking_reasons: [],
      generation_blocked_message: "",
      evidence: [],
      evidence_summary: { evidence: [], incomplete: [], user_confirmed: [], counts: groups.active_confirmation_counts },
    };
    app.loadSkillConfirmations.mockResolvedValue({ evidence: [], can_generate_resume: true, blocking_reasons: [] });
    app.analyzeAndOptimize.mockResolvedValue({ workflow_status: "COMPLETED", optimized_resume_id: 77 });
    renderRoute(<AtsAnalyzerPage />, "/optimizer/42", "/optimizer/:jobId");
    fireEvent.click(screen.getByText(/Generate, Review/i));
    const generateBtn = screen.getByRole("button", { name: /generate optimized resume/i });
    expect(generateBtn).toBeEnabled();
    await userEvent.click(generateBtn);
    expect(app.loadSkillConfirmations).toHaveBeenCalledWith(42);
    expect(app.analyzeAndOptimize).toHaveBeenCalledWith(42, true);
  });

  it("hides stale evidence-incomplete banner when can_generate_resume is true", async () => {
    app.missingSkillsGroups = {
      ...groups,
      can_generate_resume: true,
      blocking_reasons: [],
      generation_blocked_message: "",
      evidence: [{ id: 2, skill: "Terraform", evidence_status: "USER_CONFIRMED_PROFESSIONAL", is_active: true }],
      evidence_summary: {
        evidence: [{ id: 2, skill: "Terraform", evidence_status: "USER_CONFIRMED_PROFESSIONAL", is_active: true }],
        incomplete: [],
        user_confirmed: [{ id: 2, skill: "Terraform", evidence_status: "USER_CONFIRMED_PROFESSIONAL" }],
        counts: { verified_from_resume: 12, user_confirmed_with_evidence: 1, learning_only: 0, not_possessed: 0, incomplete: 0 },
      },
    };
    app.messages = { ats: "Failed: Required evidence incomplete: Terraform" };
    app.atsResult = {
      workflow_id: 62,
      workflow_status: "FAILED",
      error_message: "Required evidence incomplete: Terraform",
    };
    app.loadSkillConfirmations.mockResolvedValue({
      evidence: [{ id: 2, skill: "Terraform", evidence_status: "USER_CONFIRMED_PROFESSIONAL", is_active: true }],
      can_generate_resume: true,
      blocking_reasons: [],
      analysis_id: "3:956:38:current",
    });
    renderRoute(<AtsAnalyzerPage />, "/optimizer/42", "/optimizer/:jobId");
    fireEvent.click(screen.getByText("Review Evidence"));
    await userEvent.click(screen.getByRole("button", { name: /continue to optimization/i }));
    expect(screen.getByRole("heading", { name: /step 5/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /generate optimized resume/i })).toBeEnabled();
  });

  it("does not render inactive historical incomplete rows in Step 4", async () => {
    app.confirmedSkills = [
      { id: 1, skill: "Docker", evidence_status: "INCOMPLETE", is_active: false },
      { id: 2, skill: "Terraform", evidence_status: "USER_CONFIRMED_PROFESSIONAL", requirement_type: "MANDATORY", is_active: true },
    ];
    app.missingSkillsGroups = {
      ...groups,
      can_generate_resume: true,
      blocking_reasons: [],
      generation_warnings: [],
      generation_warning_message: "",
      generation_blocked_message: "",
      current_required_confirmations: [],
      verified_resume_skills: [{ skill: "Maven", resume_status: "VERIFIED" }],
      active_confirmation_counts: { verified_from_resume: 1, user_confirmed_with_evidence: 1, learning_only: 0, not_possessed: 0, incomplete: 0 },
      evidence: [{ id: 2, skill: "Terraform", evidence_status: "USER_CONFIRMED_PROFESSIONAL", requirement_type: "MANDATORY", is_active: true }],
      evidence_history: [{ id: 1, skill: "Docker", evidence_status: "INCOMPLETE", is_active: false }],
      evidence_summary: {
        evidence: [{ id: 2, skill: "Terraform", evidence_status: "USER_CONFIRMED_PROFESSIONAL", requirement_type: "MANDATORY", is_active: true }],
        user_confirmed: [{ id: 2, skill: "Terraform", evidence_status: "USER_CONFIRMED_PROFESSIONAL", requirement_type: "MANDATORY", is_active: true }],
        incomplete: [],
        evidence_history: [{ id: 1, skill: "Docker", evidence_status: "INCOMPLETE", is_active: false }],
        counts: { verified_from_resume: 1, user_confirmed_with_evidence: 1, learning_only: 0, not_possessed: 0, incomplete: 0 },
      },
    };
    renderRoute(<AtsAnalyzerPage />, "/optimizer/42", "/optimizer/:jobId");
    fireEvent.click(screen.getByText("Review Evidence"));
    expect(screen.getByText(/Incomplete evidence:/i).textContent).toMatch(/0/);
    const unresolvedHeading = screen.getByRole("heading", { name: /unsupported requirements/i });
    expect(within(unresolvedHeading.nextElementSibling).queryByText(/Docker/i)).not.toBeInTheDocument();
    const userConfirmedHeading = screen.getByRole("heading", { name: /current user-confirmed evidence/i });
    expect(within(userConfirmedHeading.nextElementSibling).getByText(/Terraform/i)).toBeInTheDocument();
    expect(screen.getByText(/Previous evidence history/i)).toBeInTheDocument();
  });

  it("opens Step 5 immediately when unresolved required evidence is None", async () => {
    app.missingSkillsGroups = {
      ...groups,
      can_generate_resume: true,
      blocking_reasons: [],
      generation_warnings: [],
      generation_warning_message: "",
      generation_blocked_message: "",
      current_required_confirmations: [],
      evidence: [{ id: 2, skill: "Kafka", evidence_status: "USER_CONFIRMED_PROFESSIONAL", requirement_type: "MANDATORY", is_active: true }],
      evidence_summary: {
        evidence: [{ id: 2, skill: "Kafka", evidence_status: "USER_CONFIRMED_PROFESSIONAL", requirement_type: "MANDATORY", is_active: true }],
        incomplete: [],
        user_confirmed: [{ id: 2, skill: "Kafka", evidence_status: "USER_CONFIRMED_PROFESSIONAL", requirement_type: "MANDATORY", is_active: true }],
        optional_preferred: [],
        counts: { verified_from_resume: 1, user_confirmed_with_evidence: 1, learning_only: 0, not_possessed: 0, incomplete: 0 },
      },
    };
    // Continue must not depend on evidence refresh — hang would previously block Step 5.
    app.loadSkillConfirmations.mockImplementation(() => new Promise(() => {}));
    const confirmCallsBefore = app.loadSkillConfirmations.mock.calls.length;
    renderRoute(<AtsAnalyzerPage />, "/optimizer/42", "/optimizer/:jobId");
    fireEvent.click(screen.getByText("Review Evidence"));
    const continueBtn = screen.getByRole("button", { name: /continue to optimization/i });
    expect(continueBtn).toBeEnabled();
    await userEvent.click(continueBtn);
    expect(screen.getByRole("heading", { name: /step 5/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /generate optimized resume/i })).toBeInTheDocument();
    // No additional evidence API call from Continue itself (mount effect may have called once).
    expect(app.loadSkillConfirmations.mock.calls.length).toBeLessThanOrEqual(confirmCallsBefore + 1);
  });

  it("does not bounce back to Step 4 after Continue", async () => {
    app.missingSkillsGroups = {
      ...groups,
      can_generate_resume: true,
      blocking_reasons: [],
      evidence: [],
      evidence_summary: {
        evidence: [],
        incomplete: [],
        user_confirmed: [],
        counts: { verified_from_resume: 1, user_confirmed_with_evidence: 0, learning_only: 0, not_possessed: 0, incomplete: 0 },
      },
    };
    app.loadSkillConfirmations.mockResolvedValue({ can_generate_resume: true, blocking_reasons: [], evidence: [] });
    renderRoute(<AtsAnalyzerPage />, "/optimizer/42", "/optimizer/:jobId");
    fireEvent.click(screen.getByText("Review Evidence"));
    await userEvent.click(screen.getByRole("button", { name: /continue to optimization/i }));
    expect(screen.getByRole("heading", { name: /step 5/i })).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /step 5/i })).toBeInTheDocument();
    });
    expect(screen.queryByRole("heading", { name: /review evidence and gaps/i })).not.toBeInTheDocument();
  });

  it("allows Continue when only preferred evidence is unresolved", async () => {
    app.missingSkillsGroups = {
      ...groups,
      can_generate_resume: true,
      blocking_reasons: [],
      generation_blocked_message: "",
      missing_mandatory_skills: [],
      evidence: [{ id: 3, skill: "Docker", evidence_status: "INCOMPLETE", requirement_type: "PREFERRED", is_active: true }],
      evidence_summary: {
        evidence: [{ id: 3, skill: "Docker", evidence_status: "INCOMPLETE", requirement_type: "PREFERRED", is_active: true }],
        incomplete: [{ id: 3, skill: "Docker", evidence_status: "INCOMPLETE", requirement_type: "PREFERRED", is_active: true }],
        optional_preferred: [{ id: 3, skill: "Docker", evidence_status: "INCOMPLETE", requirement_type: "PREFERRED", is_active: true }],
        user_confirmed: [],
        counts: { verified_from_resume: 1, user_confirmed_with_evidence: 0, learning_only: 0, not_possessed: 0, incomplete: 1 },
      },
    };
    app.loadSkillConfirmations.mockResolvedValue({ evidence: [], can_generate_resume: true, blocking_reasons: [] });
    renderRoute(<AtsAnalyzerPage />, "/optimizer/42", "/optimizer/:jobId");
    fireEvent.click(screen.getByText("Review Evidence"));
    const continueBtn = screen.getByRole("button", { name: /continue to optimization/i });
    expect(continueBtn).toBeEnabled();
    await userEvent.click(continueBtn);
    expect(screen.getByRole("heading", { name: /step 5/i })).toBeInTheDocument();
  });

  it("treats USER_CONFIRMED_PROFESSIONAL as resolved and ignores stale incomplete history", async () => {
    app.missingSkillsGroups = {
      ...groups,
      can_generate_resume: true,
      blocking_reasons: [],
      evidence: [{ id: 2, skill: "Terraform", evidence_status: "USER_CONFIRMED_PROFESSIONAL", requirement_type: "MANDATORY", is_active: true }],
      evidence_history: [{ id: 1, skill: "Terraform", evidence_status: "INCOMPLETE", is_active: false }],
      evidence_summary: {
        evidence: [{ id: 2, skill: "Terraform", evidence_status: "USER_CONFIRMED_PROFESSIONAL", requirement_type: "MANDATORY", is_active: true }],
        incomplete: [],
        user_confirmed: [{ id: 2, skill: "Terraform", evidence_status: "USER_CONFIRMED_PROFESSIONAL", requirement_type: "MANDATORY", is_active: true }],
        evidence_history: [{ id: 1, skill: "Terraform", evidence_status: "INCOMPLETE", is_active: false }],
        counts: { verified_from_resume: 1, user_confirmed_with_evidence: 1, learning_only: 0, not_possessed: 0, incomplete: 0 },
      },
    };
    app.loadSkillConfirmations.mockResolvedValue({ can_generate_resume: true, blocking_reasons: [], evidence: [] });
    renderRoute(<AtsAnalyzerPage />, "/optimizer/42", "/optimizer/:jobId");
    fireEvent.click(screen.getByText("Review Evidence"));
    expect(screen.getByRole("button", { name: /continue to optimization/i })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: /continue to optimization/i }));
    expect(screen.getByRole("heading", { name: /step 5/i })).toBeInTheDocument();
  });

  it("hides stale Failed banner after successful requirement analysis", async () => {
    app.messages = { ats: "Failed" };
    app.atsResult = {
      workflow_id: 99,
      workflow_status: "FAILED",
      error_message: "Required evidence incomplete: Terraform",
    };
    app.missingSkillsGroups = {
      ...groups,
      analysis_status: "SUCCESS",
      score_explanation: {
        match_percentage: 81,
        role_alignment: "EXACT",
        component_scores: {
          role_alignment: { earned: 20, max: 20 },
          mandatory_skills: { earned: 28, max: 35 },
        },
      },
    };
    app.messages = {
      ats: "Failed",
      "ats-requirements": "Job requirements analyzed successfully.",
    };
    renderRoute(<AtsAnalyzerPage />, "/optimizer/42", "/optimizer/:jobId");
    await userEvent.click(screen.getByRole("button", { name: /^continue$/i }));
    expect(screen.queryByText(/^Failed$/i)).not.toBeInTheDocument();
    expect(screen.getByText(/Job requirements analyzed successfully/i)).toBeInTheDocument();
    expect(screen.getByText(/ATS Match:/i)).toBeInTheDocument();
    expect(screen.getByText(/81%/)).toBeInTheDocument();
  });

  it("keeps Failed banner cleared on Confirm Skills after successful analysis", () => {
    app.atsResult = { workflow_id: 99, workflow_status: "FAILED", error_message: "Failed" };
    app.messages = {
      ats: "Failed",
      "ats-requirements": "Job requirements analyzed successfully.",
    };
    app.missingSkillsGroups = {
      ...groups,
      analysis_status: "SUCCESS",
      score_explanation: { match_percentage: 81, role_alignment: "EXACT" },
    };
    renderRoute(<AtsAnalyzerPage />, "/optimizer/42", "/optimizer/:jobId");
    fireEvent.click(screen.getByText("Confirm Skills"));
    expect(screen.queryByText(/^Failed$/i)).not.toBeInTheDocument();
    expect(
      screen.getByText(/Confirm skills that need evidence|Job requirements analyzed successfully/i),
    ).toBeInTheDocument();
  });

  it("styles Remove Evidence as a danger button with friendly evidence labels", async () => {
    app.missingSkillsGroups = {
      ...groups,
      can_generate_resume: true,
      blocking_reasons: [],
      evidence: [{ id: 7, skill: "Cucumber", evidence_status: "USER_CONFIRMED_PROFESSIONAL", is_active: true }],
      evidence_summary: {
        evidence: [{ id: 7, skill: "Cucumber", evidence_status: "USER_CONFIRMED_PROFESSIONAL", is_active: true }],
        user_confirmed: [{ id: 7, skill: "Cucumber", evidence_status: "USER_CONFIRMED_PROFESSIONAL", is_active: true }],
        incomplete: [],
        counts: { verified_from_resume: 1, user_confirmed_with_evidence: 1, learning_only: 0, not_possessed: 0, incomplete: 0 },
      },
    };
    renderRoute(<AtsAnalyzerPage />, "/optimizer/42", "/optimizer/:jobId");
    fireEvent.click(screen.getByText("Review Evidence"));
    expect(screen.getByText("Cucumber")).toBeInTheDocument();
    expect(screen.getByText(/Professional experience confirmed/i)).toBeInTheDocument();
    expect(screen.queryByText(/USER_CONFIRMED_PROFESSIONAL/i)).not.toBeInTheDocument();
    const removeBtn = screen.getByRole("button", { name: /remove evidence for cucumber/i });
    expect(removeBtn).toHaveClass("button", "danger");
    expect(removeBtn).toHaveTextContent("Remove Evidence");
  });
});
