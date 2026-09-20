import { describe, expect, it } from "vitest";
import { documentToPlainText, emptyDocument, findBulletPath, summarizeWhatChanged } from "../components/resume-workspace/resumeDocumentUtils";

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

describe("findBulletPath", () => {
  const document = {
    sections: [
      { id: "1", type: "summary", heading: "Professional Summary", content: "Builds reliable systems." },
      {
        id: "2",
        type: "experience",
        heading: "Professional Experience",
        items: [{
          id: "e1", title: "Engineer", company: "Acme", location: "Remote", dates: "2022-Present",
          bullets: [{ id: "b1", text: "Built REST APIs." }, { id: "b2", text: "Led migrations." }],
        }],
      },
      {
        id: "3",
        type: "projects",
        heading: "Projects",
        items: [{ id: "p1", name: "Pipeline", dates: "2023", bullets: [{ id: "b3", text: "Wrote ETL jobs." }] }],
      },
    ],
  };

  it("locates an experience bullet by its exact current text", () => {
    expect(findBulletPath(document, "Led migrations.")).toEqual(["sections", 1, "items", 0, "bullets", 1, "text"]);
  });

  it("locates a project bullet by its exact current text", () => {
    expect(findBulletPath(document, "Wrote ETL jobs.")).toEqual(["sections", 2, "items", 0, "bullets", 0, "text"]);
  });

  it("returns null when no bullet currently holds that text (already edited further)", () => {
    expect(findBulletPath(document, "This text does not exist anywhere.")).toBeNull();
  });

  it("returns null for empty/blank input instead of matching an empty bullet", () => {
    expect(findBulletPath(document, "")).toBeNull();
    expect(findBulletPath(document, "   ")).toBeNull();
  });
});
