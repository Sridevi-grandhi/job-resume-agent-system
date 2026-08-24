import { describe, expect, it } from "vitest";
import { documentToPlainText, emptyDocument, summarizeWhatChanged } from "../components/resume-workspace/resumeDocumentUtils";

describe("resume document utils", () => {
  it("serializes structured documents to plain text", () => {
    const document = emptyDocument();
    document.header = { name: "Ada", title: "Engineer", email: "ada@example.com", phone: "", location: "Remote", links: [] };
    document.sections = [
      { id: "1", type: "summary", heading: "Professional Summary", content: "Builds reliable systems." },
      { id: "2", type: "skills", heading: "Technical Skills", skills: ["Java", "SQL"] },
      {
        id: "3",
        type: "experience",
        heading: "Professional Experience",
        items: [{ id: "e1", title: "Engineer", company: "Acme", location: "Remote", dates: "2022-Present", bullets: [{ id: "b1", text: "Shipped APIs" }] }],
      },
    ];
    const text = documentToPlainText(document);
    expect(text).toContain("Ada");
    expect(text).toContain("Java, SQL");
    expect(text).toContain("Acme");
    expect(text).toContain("- Shipped APIs");
  });

  it("summarizes what changed without role hardcoding", () => {
    const items = summarizeWhatChanged(
      { sections_improved: ["Summary"], bullets_rewritten: 2, keywords_added: ["SQL"] },
      { confirmed_skills_used: ["SQL"], unsupported_keywords_not_added: ["Terraform"] },
    );
    expect(items.some((item) => /Confirmed skills incorporated/i.test(item))).toBe(true);
    expect(items.some((item) => /Unsupported JD requirements excluded/i.test(item))).toBe(true);
  });
});
