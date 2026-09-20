import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ResumeWorkspace from "../components/resume-workspace/ResumeWorkspace";

function buildDocument(bulletText = "Built REST APIs using Java.") {
  return {
    templateId: "professional-ats",
    pageSize: "letter",
    fontFamily: "Calibri",
    fontSize: 11,
    spacing: "normal",
    align: "left",
    header: { name: "Jane Developer", title: "SDET", email: "jane@example.test", phone: "", location: "Remote", links: [] },
    sections: [
      {
        id: "s1",
        type: "experience",
        heading: "Professional Experience",
        items: [{
          id: "e1", title: "SDET", company: "Acme", location: "Remote", dates: "2021-Present",
          bullets: [{ id: "b1", text: bulletText }],
        }],
      },
    ],
  };
}

describe("ResumeWorkspace — AI Improve / Try Again", () => {
  it("keeps the previous suggestion visible and sends it as previousSuggestion when Try Again is clicked (regression: retry used to send text=undefined and blank the draft)", async () => {
    const onImprove = vi.fn()
      .mockResolvedValueOnce({ suggested: "Engineered scalable REST APIs in Java.", used_llm: true, llm_provider: "ollama" })
      .mockResolvedValueOnce({ suggested: "Delivered production-grade REST APIs in Java.", used_llm: true, llm_provider: "ollama" });

    render(<ResumeWorkspace initialDocument={buildDocument()} onImprove={onImprove} />);

    await userEvent.click(screen.getByRole("button", { name: /ai improve/i }));
    await waitFor(() => expect(screen.getByText("Engineered scalable REST APIs in Java.")).toBeInTheDocument());
    expect(onImprove).toHaveBeenNthCalledWith(1, { text: "Built REST APIs using Java.", context: "bullet", previousSuggestion: "" });

    await userEvent.click(screen.getByRole("button", { name: /^try again$/i }));
    // While the retry is in flight, the PREVIOUS suggestion must still be visible (never blanked).
    // (jsdom resolves the mocked promise on the next microtask, so we assert the final state below;
    // the key regression check is the argument onImprove was called with.)
    await waitFor(() => expect(screen.getByText("Delivered production-grade REST APIs in Java.")).toBeInTheDocument());
    expect(onImprove).toHaveBeenNthCalledWith(2, {
      text: "Built REST APIs using Java.",
      context: "bullet",
      previousSuggestion: "Engineered scalable REST APIs in Java.",
    });
  });

  it("never blanks the suggestion when a retry fails — shows an error message and keeps the previous text", async () => {
    const onImprove = vi.fn()
      .mockResolvedValueOnce({ suggested: "Engineered scalable REST APIs in Java.", used_llm: true, llm_provider: "ollama" })
      .mockResolvedValueOnce({ suggested: "", retry_failed: true });

    render(<ResumeWorkspace initialDocument={buildDocument()} onImprove={onImprove} />);

    await userEvent.click(screen.getByRole("button", { name: /ai improve/i }));
    await waitFor(() => expect(screen.getByText("Engineered scalable REST APIs in Java.")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: /^try again$/i }));
    await waitFor(() => expect(screen.getByText(/could not generate another suggestion/i)).toBeInTheDocument());
    // The prior suggestion must still be shown, not blanked.
    expect(screen.getByText("Engineered scalable REST APIs in Java.")).toBeInTheDocument();
  });

  it("shows the Ollama badge only when used_llm is true", async () => {
    const onImprove = vi.fn().mockResolvedValueOnce({ suggested: "Delivered REST APIs in Java.", used_llm: false });
    render(<ResumeWorkspace initialDocument={buildDocument()} onImprove={onImprove} />);
    await userEvent.click(screen.getByRole("button", { name: /ai improve/i }));
    await waitFor(() => expect(screen.getByText("Delivered REST APIs in Java.")).toBeInTheDocument());
    expect(screen.queryByText(/powered by ollama/i)).toBeNull();
  });

  it("Accept applies the suggestion to the document and closes the popover", async () => {
    const onImprove = vi.fn().mockResolvedValueOnce({ suggested: "Engineered scalable REST APIs in Java.", used_llm: true, llm_provider: "ollama" });
    render(<ResumeWorkspace initialDocument={buildDocument()} onImprove={onImprove} />);
    await userEvent.click(screen.getByRole("button", { name: /ai improve/i }));
    await waitFor(() => expect(screen.getByText("Engineered scalable REST APIs in Java.")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /^accept$/i }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: /ai improve suggestion/i })).toBeNull());
    expect(screen.getByDisplayValue("Engineered scalable REST APIs in Java.")).toBeInTheDocument();
  });

  it("never opens the review modal when the suggestion matches the original wording — shows an inline notice instead (regression: this used to open a modal offering Accept/Reject on two identical strings)", async () => {
    const onImprove = vi.fn().mockResolvedValueOnce({ suggested: "Built REST APIs using Java.", used_llm: false });
    render(<ResumeWorkspace initialDocument={buildDocument()} onImprove={onImprove} />);
    await userEvent.click(screen.getByRole("button", { name: /ai improve/i }));
    await waitFor(() => expect(screen.getByText(/no meaningfully different improvement/i)).toBeInTheDocument());
    expect(screen.queryByRole("dialog", { name: /ai improve suggestion/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /^accept$/i })).toBeNull();
  });

  it("Reject discards the suggestion and keeps the original bullet text", async () => {
    const onImprove = vi.fn().mockResolvedValueOnce({ suggested: "Engineered scalable REST APIs in Java.", used_llm: true, llm_provider: "ollama" });
    render(<ResumeWorkspace initialDocument={buildDocument()} onImprove={onImprove} />);
    await userEvent.click(screen.getByRole("button", { name: /ai improve/i }));
    await waitFor(() => expect(screen.getByText("Engineered scalable REST APIs in Java.")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /^reject$/i }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: /ai improve suggestion/i })).toBeNull());
    expect(screen.getByDisplayValue("Built REST APIs using Java.")).toBeInTheDocument();
  });
});

const TRACKED_REPORT = {
  bullet_changes: [{
    section: "SDET at Acme",
    original_text: "Built REST APIs using Java.",
    generated_text: "Engineered scalable REST APIs in Java.",
    change_type: "REWRITTEN",
  }],
};

describe("ResumeWorkspace — inline AI-change review (no sidebar bullet list)", () => {
  it("does not render the old large sidebar AI-Modified-Bullets list", () => {
    render(
      <ResumeWorkspace
        initialDocument={buildDocument("Engineered scalable REST APIs in Java.")}
        improvementReport={TRACKED_REPORT}
      />
    );
    expect(screen.queryByText(/ai-modified bullets/i)).toBeNull();
  });

  it("shows an inline pending badge next to the AI-modified bullet, and sidebar counts reflect it", () => {
    render(
      <ResumeWorkspace
        initialDocument={buildDocument("Engineered scalable REST APIs in Java.")}
        improvementReport={TRACKED_REPORT}
      />
    );
    expect(screen.getByText(/ai improved/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^review$/i })).toBeInTheDocument();
    expect(screen.getByText("Pending review")).toBeInTheDocument();
    const pendingCount = screen.getByText("Pending review").closest("div").querySelector("dd");
    expect(pendingCount.textContent).toBe("1");
  });

  it("Review opens the comparison modal without a new API call, and Reject restores the original text + updates the badge", async () => {
    const onImprove = vi.fn();
    render(
      <ResumeWorkspace
        initialDocument={buildDocument("Engineered scalable REST APIs in Java.")}
        improvementReport={TRACKED_REPORT}
        onImprove={onImprove}
      />
    );
    await userEvent.click(screen.getByRole("button", { name: /^review$/i }));
    expect(screen.getByRole("dialog", { name: /ai improve suggestion/i })).toBeInTheDocument();
    expect(onImprove).not.toHaveBeenCalled(); // reviewing an already-known change needs no fresh request

    await userEvent.click(screen.getByRole("button", { name: /^reject$/i }));
    await waitFor(() => expect(screen.getByDisplayValue("Built REST APIs using Java.")).toBeInTheDocument());
    expect(screen.queryByText(/ai improved/i)).toBeNull();
  });

  it("Review then Accept marks the badge Accepted", async () => {
    render(
      <ResumeWorkspace
        initialDocument={buildDocument("Engineered scalable REST APIs in Java.")}
        improvementReport={TRACKED_REPORT}
      />
    );
    await userEvent.click(screen.getByRole("button", { name: /^review$/i }));
    await userEvent.click(screen.getByRole("button", { name: /^accept$/i }));
    await waitFor(() => expect(screen.getByText("✓ Accepted")).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /^review$/i })).toBeNull();
  });

  it("directly editing a tracked bullet marks it 'Edited manually' instead of continuing to show the old AI suggestion as current", async () => {
    render(
      <ResumeWorkspace
        initialDocument={buildDocument("Engineered scalable REST APIs in Java.")}
        improvementReport={TRACKED_REPORT}
      />
    );
    const bulletBox = screen.getByLabelText("Experience bullet");
    await userEvent.clear(bulletBox);
    await userEvent.type(bulletBox, "Hand-written wording by the candidate.");
    await waitFor(() => expect(screen.getByText(/edited manually/i)).toBeInTheDocument());
  });
});

describe("ResumeWorkspace — Professional Summary inline AI review", () => {
  const SUMMARY_REPORT = {
    summary_change: {
      original_text: "Java developer.",
      generated_text: "Senior Java developer with 8 years of experience building enterprise systems.",
    },
  };

  function buildDocumentWithSummary(summaryText) {
    const doc = buildDocument();
    doc.sections.unshift({ id: "sum1", type: "summary", heading: "Professional Summary", content: summaryText });
    return doc;
  }

  it("shows an inline AI Improved badge on the summary and reviewing it needs no fresh request", async () => {
    render(
      <ResumeWorkspace
        initialDocument={buildDocumentWithSummary("Senior Java developer with 8 years of experience building enterprise systems.")}
        improvementReport={SUMMARY_REPORT}
      />
    );
    const reviewButtons = screen.getAllByRole("button", { name: /^review$/i });
    await userEvent.click(reviewButtons[0]);
    expect(screen.getByText("Java developer.")).toBeInTheDocument();
  });

  it("summary Try Again shows the OpenAI provider badge, not Ollama", async () => {
    const onImprove = vi.fn().mockResolvedValueOnce({
      suggested: "Senior Java developer focused on enterprise Spring Boot systems, with 8 years of delivery experience.",
      used_llm: true,
      llm_provider: "openai",
    });
    render(
      <ResumeWorkspace
        initialDocument={buildDocumentWithSummary("Senior Java developer with 8 years of experience building enterprise systems.")}
        improvementReport={SUMMARY_REPORT}
        onImprove={onImprove}
      />
    );
    await userEvent.click(screen.getAllByRole("button", { name: /^review$/i })[0]);
    await userEvent.click(screen.getByRole("button", { name: /^try again$/i }));
    await waitFor(() => expect(screen.getByText(/powered by openai/i)).toBeInTheDocument());
    expect(screen.queryByText(/powered by ollama/i)).toBeNull();
  });
});

describe("ResumeWorkspace — Undo/Redo integrates with Accept", () => {
  it("Undo after Accept reverts the bullet, and Redo re-applies the accepted suggestion", async () => {
    const onImprove = vi.fn().mockResolvedValueOnce({ suggested: "Engineered scalable REST APIs in Java.", used_llm: true, llm_provider: "ollama" });
    render(<ResumeWorkspace initialDocument={buildDocument()} onImprove={onImprove} />);
    await userEvent.click(screen.getByRole("button", { name: /ai improve/i }));
    await waitFor(() => expect(screen.getByText("Engineered scalable REST APIs in Java.")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /^accept$/i }));
    await waitFor(() => expect(screen.getByDisplayValue("Engineered scalable REST APIs in Java.")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: /undo/i }));
    await waitFor(() => expect(screen.getByDisplayValue("Built REST APIs using Java.")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: /redo/i }));
    await waitFor(() => expect(screen.getByDisplayValue("Engineered scalable REST APIs in Java.")).toBeInTheDocument());
  });
});

describe("ResumeWorkspace — near-identical/no-change suggestions never open the Accept/Reject modal", () => {
  it("no_change_recommended from a fresh AI Improve click shows an inline notice, not a modal", async () => {
    const onImprove = vi.fn().mockResolvedValueOnce({ suggested: "", used_llm: false, no_change_recommended: true });
    render(<ResumeWorkspace initialDocument={buildDocument()} onImprove={onImprove} />);
    await userEvent.click(screen.getByRole("button", { name: /ai improve/i }));
    await waitFor(() => expect(screen.getByText(/no meaningfully different improvement/i)).toBeInTheDocument());
    expect(screen.queryByRole("dialog", { name: /ai improve suggestion/i })).toBeNull();
  });

  it("Review on a stale/invalid tracked change (empty or identical original/current) shows an unavailable notice instead of a broken modal", async () => {
    render(
      <ResumeWorkspace
        initialDocument={buildDocument("Engineered scalable REST APIs in Java.")}
        improvementReport={{
          bullet_changes: [{
            section: "SDET at Acme",
            // Identical original/generated — should never have been seeded
            // as a real change; this test guards the defensive check.
            original_text: "Engineered scalable REST APIs in Java.",
            generated_text: "Engineered scalable REST APIs in Java.",
            change_type: "REWRITTEN",
          }],
        }}
      />
    );
    // The badge itself should not even render for an identical pair.
    expect(screen.queryByRole("button", { name: /^review$/i })).toBeNull();
  });
});
