import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ResumeWorkspace from "../components/resume-workspace/ResumeWorkspace";

function buildDocument(summary = "Senior Java developer building reliable services.") {
  return {
    templateId: "professional-ats",
    pageSize: "letter",
    fontFamily: "Calibri",
    fontSize: 11,
    spacing: "normal",
    align: "left",
    header: { name: "Jane Developer", title: "Engineer", email: "jane@example.test", phone: "", location: "Remote", links: [] },
    sections: [
      { id: "s1", type: "summary", heading: "Professional Summary", content: summary },
    ],
  };
}

describe("ResumeWorkspace — autosave persistence", () => {
  it("autosaves a manual editor change after the debounce", async () => {
    const onSave = vi.fn().mockResolvedValue({ persisted: true, document: buildDocument("Edited summary.") });
    render(<ResumeWorkspace initialDocument={buildDocument()} onSave={onSave} />);
    const summary = screen.getByLabelText("Professional summary");
    await userEvent.clear(summary);
    await userEvent.type(summary, "Edited summary.");
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    await waitFor(() => expect(onSave).toHaveBeenCalled(), { timeout: 2500 });
    const payload = onSave.mock.calls[0][0];
    expect(payload.action).toBe("autosave");
    expect(payload.skipScore).toBe(true);
    expect(payload.plainText).toContain("Edited summary.");
    await waitFor(() => expect(screen.getByText("Saved ✓")).toBeInTheDocument());
  });

  it("does not let a stale generated document overwrite an unsaved manual edit", async () => {
    const onSave = vi.fn().mockResolvedValue({ persisted: true });
    const first = buildDocument("Original generated summary.");
    const { rerender } = render(<ResumeWorkspace initialDocument={first} onSave={onSave} />);
    const summary = screen.getByLabelText("Professional summary");
    await userEvent.clear(summary);
    await userEvent.type(summary, "User typed this before hydration finished.");
    rerender(
      <ResumeWorkspace
        initialDocument={buildDocument("Original generated summary.")}
        onSave={onSave}
      />,
    );
    expect(screen.getByLabelText("Professional summary")).toHaveValue("User typed this before hydration finished.");
  });

  it("does not apply an older generated payload after a newer autosave", async () => {
    const savedDoc = buildDocument("Latest saved editor summary.");
    const onSave = vi.fn().mockResolvedValue({ persisted: true, document: savedDoc });
    const { rerender } = render(
      <ResumeWorkspace initialDocument={buildDocument("Original generated summary.")} onSave={onSave} />,
    );
    const summary = screen.getByLabelText("Professional summary");
    await userEvent.clear(summary);
    await userEvent.type(summary, "Latest saved editor summary.");
    await waitFor(() => expect(onSave).toHaveBeenCalled(), { timeout: 2500 });
    await waitFor(() => expect(screen.getByText("Saved ✓")).toBeInTheDocument());
    rerender(
      <ResumeWorkspace
        initialDocument={buildDocument("Original generated summary.")}
        onSave={onSave}
      />,
    );
    expect(screen.getByLabelText("Professional summary")).toHaveValue("Latest saved editor summary.");
  });
});
