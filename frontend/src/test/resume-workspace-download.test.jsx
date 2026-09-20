import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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

function mockFetchOk({ body = "fake-file-bytes", filename = "Jane_Developer_Optimized_Resume.docx" } = {}) {
  return vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    headers: { get: (name) => (name === "Content-Disposition" ? `attachment; filename="${filename}"` : null) },
    blob: () => Promise.resolve(new Blob([body])),
  });
}

let appendSpy;
let clickSpy;

beforeEach(() => {
  appendSpy = vi.spyOn(window.document.body, "appendChild");
  clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
});

afterEach(() => {
  appendSpy.mockRestore();
  clickSpy.mockRestore();
  vi.unstubAllGlobals();
});

describe("ResumeWorkspace — Download", () => {
  // Item 1/3/4/5
  it("shows DOCX, PDF, and TXT download controls when a generated resume exists", () => {
    render(
      <ResumeWorkspace
        initialDocument={buildDocument()}
        downloadDocxUrl="http://api.test/download?format=docx"
        downloadPdfUrl="http://api.test/download?format=pdf"
        downloadTxtUrl="http://api.test/download?format=txt"
      />,
    );
    expect(screen.getByRole("button", { name: /^download docx$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^download pdf$/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^download txt$/i })).toBeInTheDocument();
  });

  // Item 2
  it("hides a format's control entirely when no generated resume/URL is available for it", () => {
    render(<ResumeWorkspace initialDocument={buildDocument()} downloadDocxUrl={null} downloadPdfUrl={null} downloadTxtUrl={null} />);
    expect(screen.queryByRole("button", { name: /download docx/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /download pdf/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /download txt/i })).toBeNull();
  });

  // Item 6
  it("clicking Download DOCX fetches the exact URL provided (the existing download endpoint)", async () => {
    const fetchMock = mockFetchOk();
    vi.stubGlobal("fetch", fetchMock);
    render(<ResumeWorkspace initialDocument={buildDocument()} downloadDocxUrl="http://api.test/optimizer/resume/7/42/91/download?format=docx" onSave={vi.fn().mockResolvedValue({})} />);
    await userEvent.click(screen.getByRole("button", { name: /^download docx$/i }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("http://api.test/optimizer/resume/7/42/91/download?format=docx"));
  });

  it("triggers a real file save using the server-provided filename", async () => {
    const fetchMock = mockFetchOk({ filename: "Jane_Developer_Optimized_Resume.docx" });
    vi.stubGlobal("fetch", fetchMock);
    render(<ResumeWorkspace initialDocument={buildDocument()} downloadDocxUrl="http://api.test/download" onSave={vi.fn().mockResolvedValue({})} />);
    await userEvent.click(screen.getByRole("button", { name: /^download docx$/i }));
    await waitFor(() => expect(clickSpy).toHaveBeenCalled());
    const anchor = appendSpy.mock.calls.map(([node]) => node).find((node) => node.tagName === "A");
    expect(anchor.download).toBe("Jane_Developer_Optimized_Resume.docx");
  });

  // Item 8 — manual edits included
  it("flushes a pending manual edit (autosave) before the download request fires", async () => {
    const fetchMock = mockFetchOk();
    vi.stubGlobal("fetch", fetchMock);
    const onSave = vi.fn().mockResolvedValue({ edited_score: 80 });
    render(<ResumeWorkspace initialDocument={buildDocument()} downloadDocxUrl="http://api.test/download" onSave={onSave} />);

    await userEvent.click(screen.getByRole("button", { name: /^download docx$/i }));

    await waitFor(() => expect(onSave).toHaveBeenCalled());
    const saveCallOrder = onSave.mock.invocationCallOrder[0];
    const fetchCallOrder = fetchMock.mock.invocationCallOrder[0];
    expect(saveCallOrder).toBeLessThan(fetchCallOrder); // save happens BEFORE the download request
  });

  // Race condition: a debounced autosave already mid-flight when Download is
  // clicked must be awaited (not raced past) before the download request fires,
  // and a fresh save of the truly-latest document must follow it — otherwise
  // the GET can land while the PATCH that would have unblocked validation is
  // still in flight, downloading stale validation_status/content.
  it("waits for an in-flight autosave before downloading, instead of racing past it", async () => {
    const fetchMock = mockFetchOk();
    vi.stubGlobal("fetch", fetchMock);
    let resolveSave;
    const pendingSave = new Promise((resolve) => { resolveSave = resolve; });
    const onSave = vi.fn()
      .mockReturnValueOnce(pendingSave) // the debounced autosave — left in flight
      .mockResolvedValue({}); // the fresh pre-download save
    render(<ResumeWorkspace initialDocument={buildDocument()} downloadDocxUrl="http://api.test/download" onSave={onSave} />);

    const bulletField = screen.getByDisplayValue("Built REST APIs using Java.");
    await userEvent.type(bulletField, " Extra.");
    // Let the real 1.4s autosave debounce fire so its persist() call is
    // genuinely in flight (onSave called, promise not yet resolved) before
    // Download is clicked.
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1), { timeout: 2000, interval: 50 });

    const downloadClick = userEvent.click(screen.getByRole("button", { name: /^download docx$/i }));
    // While the in-flight autosave is still unresolved, the download must
    // not have fetched yet — it should be awaiting pendingSaveRef first.
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(fetchMock).not.toHaveBeenCalled();

    resolveSave({});
    await downloadClick;
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    // Awaited the in-flight autosave AND issued a fresh save afterward.
    expect(onSave.mock.calls.length).toBeGreaterThanOrEqual(2);
  }, 10000);

  // Item 9/10 — accepted/rejected AI change content flows through the same
  // persisted document, never a stale pre-review snapshot.
  it("persists the CURRENT document (after a manual bullet edit) before downloading, not the original", async () => {
    const fetchMock = mockFetchOk();
    vi.stubGlobal("fetch", fetchMock);
    const onSave = vi.fn().mockResolvedValue({});
    render(
      <ResumeWorkspace
        initialDocument={buildDocument("Built REST APIs using Java.")}
        downloadDocxUrl="http://api.test/download"
        onSave={onSave}
        onManualEdit={vi.fn()}
      />,
    );
    const bulletField = screen.getByDisplayValue("Built REST APIs using Java.");
    await userEvent.clear(bulletField);
    await userEvent.type(bulletField, "Engineered scalable REST APIs in Java.");

    await userEvent.click(screen.getByRole("button", { name: /^download docx$/i }));

    await waitFor(() => expect(onSave).toHaveBeenCalled());
    const lastCall = onSave.mock.calls[onSave.mock.calls.length - 1][0];
    expect(lastCall.plainText).toContain("Engineered scalable REST APIs in Java.");
    expect(lastCall.plainText).not.toContain("Built REST APIs using Java.");
  });

  // Item 9 — an ACCEPTED AI change is included in what gets downloaded
  it("persists an accepted AI-improved bullet before downloading", async () => {
    const fetchMock = mockFetchOk();
    vi.stubGlobal("fetch", fetchMock);
    const onSave = vi.fn().mockResolvedValue({});
    const onImprove = vi.fn().mockResolvedValue({ suggested: "Engineered scalable REST APIs in Java.", used_llm: true });
    render(
      <ResumeWorkspace
        initialDocument={buildDocument("Built REST APIs using Java.")}
        downloadDocxUrl="http://api.test/download"
        onSave={onSave}
        onImprove={onImprove}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /ai improve/i }));
    await waitFor(() => expect(screen.getByText("Engineered scalable REST APIs in Java.")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /^accept$/i }));

    await userEvent.click(screen.getByRole("button", { name: /^download docx$/i }));

    await waitFor(() => expect(onSave).toHaveBeenCalled());
    const lastCall = onSave.mock.calls[onSave.mock.calls.length - 1][0];
    expect(lastCall.plainText).toContain("Engineered scalable REST APIs in Java.");
  });

  // Item 10 — a REJECTED AI change restores the original in what gets downloaded
  it("persists the original wording (not the AI suggestion) after Reject, before downloading", async () => {
    const fetchMock = mockFetchOk();
    vi.stubGlobal("fetch", fetchMock);
    const onSave = vi.fn().mockResolvedValue({});
    const onImprove = vi.fn().mockResolvedValue({ suggested: "Engineered scalable REST APIs in Java.", used_llm: true });
    render(
      <ResumeWorkspace
        initialDocument={buildDocument("Built REST APIs using Java.")}
        downloadDocxUrl="http://api.test/download"
        onSave={onSave}
        onImprove={onImprove}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /ai improve/i }));
    await waitFor(() => expect(screen.getByText("Engineered scalable REST APIs in Java.")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /^reject$/i }));

    await userEvent.click(screen.getByRole("button", { name: /^download docx$/i }));

    await waitFor(() => expect(onSave).toHaveBeenCalled());
    const lastCall = onSave.mock.calls[onSave.mock.calls.length - 1][0];
    expect(lastCall.plainText).toContain("Built REST APIs using Java.");
    expect(lastCall.plainText).not.toContain("Engineered scalable REST APIs in Java.");
  });

  // Item 12
  it("shows a safe generic error message when the download request fails, never a stack trace", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 400 }));
    render(<ResumeWorkspace initialDocument={buildDocument()} downloadDocxUrl="http://api.test/download" onSave={vi.fn().mockResolvedValue({})} />);
    await userEvent.click(screen.getByRole("button", { name: /^download docx$/i }));
    expect(await screen.findByText(/unable to prepare the resume download/i)).toBeInTheDocument();
    expect(screen.queryByText(/traceback|stack|error:/i)).toBeNull();
  });

  it("shows a safe generic error message when the network request itself throws", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network down")));
    render(<ResumeWorkspace initialDocument={buildDocument()} downloadDocxUrl="http://api.test/download" onSave={vi.fn().mockResolvedValue({})} />);
    await userEvent.click(screen.getByRole("button", { name: /^download docx$/i }));
    expect(await screen.findByText(/unable to prepare the resume download/i)).toBeInTheDocument();
  });

  // Loading state + duplicate-click prevention
  it("shows 'Preparing download…' and disables the other download buttons while in flight", async () => {
    let resolveFetch;
    const pending = new Promise((resolve) => { resolveFetch = resolve; });
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(pending));
    render(
      <ResumeWorkspace
        initialDocument={buildDocument()}
        downloadDocxUrl="http://api.test/download?format=docx"
        downloadPdfUrl="http://api.test/download?format=pdf"
        onSave={vi.fn().mockResolvedValue({})}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /^download docx$/i }));
    expect(await screen.findByRole("button", { name: /preparing download/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /^download pdf$/i })).toBeDisabled();

    resolveFetch({
      ok: true, status: 200,
      headers: { get: () => null },
      blob: () => Promise.resolve(new Blob(["x"])),
    });
    await waitFor(() => expect(screen.getByRole("button", { name: /^download docx$/i })).not.toBeDisabled());
  });

  // Item 13 — score state untouched
  it("does not change the current ATS score shown in the editor", async () => {
    const fetchMock = mockFetchOk();
    vi.stubGlobal("fetch", fetchMock);
    render(
      <ResumeWorkspace
        initialDocument={buildDocument()}
        initialCurrentScore={82}
        downloadDocxUrl="http://api.test/download"
        onSave={vi.fn().mockResolvedValue({ edited_score: 999 })}
      />,
    );
    expect(screen.getAllByText("82%").length).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("button", { name: /^download docx$/i }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    // The pre-download persist() call passes skipScore: true — the score shown must not jump to 999.
    expect(screen.getAllByText("82%").length).toBeGreaterThan(0);
    expect(screen.queryByText("999%")).toBeNull();
  });

  // Item 14 — never calls onScore/onRegenerate; export only
  it("never triggers scoring or regeneration as a side effect of downloading", async () => {
    const fetchMock = mockFetchOk();
    vi.stubGlobal("fetch", fetchMock);
    const onScore = vi.fn();
    const onRegenerate = vi.fn();
    render(
      <ResumeWorkspace
        initialDocument={buildDocument()}
        downloadDocxUrl="http://api.test/download"
        onSave={vi.fn().mockResolvedValue({})}
        onScore={onScore}
        onRegenerate={onRegenerate}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /^download docx$/i }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(onScore).not.toHaveBeenCalled();
    expect(onRegenerate).not.toHaveBeenCalled();
  });
});
