/** Structured resume document helpers for the Step 5 WYSIWYG workspace. */

function nid(prefix = "n") {
  return `${prefix}-${Math.random().toString(36).slice(2, 10)}`;
}

export const TEMPLATES = [
  { id: "professional-ats", label: "Professional ATS" },
  { id: "classic-ats", label: "Classic ATS" },
  { id: "compact-ats", label: "Compact ATS" },
];

export const FONT_FAMILIES = [
  { id: "Calibri", label: "Calibri" },
  { id: "Georgia", label: "Georgia" },
  { id: "Arial", label: "Arial" },
  { id: "Times New Roman", label: "Times New Roman" },
];

export const SPACING = [
  { id: "compact", label: "Compact" },
  { id: "normal", label: "Normal" },
  { id: "relaxed", label: "Relaxed" },
];

export function emptyDocument(seedText = "") {
  return {
    templateId: "professional-ats",
    pageSize: "letter",
    fontFamily: "Calibri",
    fontSize: 11,
    spacing: "normal",
    align: "left",
    header: {
      name: "",
      title: "",
      email: "",
      phone: "",
      location: "",
      links: [],
    },
    sections: seedText
      ? [{ id: nid("s"), type: "custom", heading: "Resume", content: seedText, bullets: [] }]
      : [],
  };
}

export function documentToPlainText(document) {
  if (!document) return "";
  const header = document.header || {};
  const lines = [];
  if (header.name) lines.push(header.name);
  if (header.title) lines.push(header.title);
  const contact = [header.email, header.phone, header.location, ...(header.links || [])].filter(Boolean);
  if (contact.length) lines.push(contact.join(" | "));
  if (lines.length) lines.push("");

  for (const section of document.sections || []) {
    lines.push(section.heading || "Section");
    const type = section.type || "custom";
    if (type === "summary") {
      if (section.content) lines.push(section.content);
    } else if (type === "skills") {
      if ((section.skills || []).length) lines.push(section.skills.join(", "));
      else if (section.content) lines.push(section.content);
    } else if (type === "experience") {
      for (const item of section.items || []) {
        const meta = [item.title, item.company, item.location, item.dates].filter(Boolean).join(" | ");
        if (meta) lines.push(meta);
        for (const bullet of item.bullets || []) {
          const text = typeof bullet === "string" ? bullet : bullet.text;
          if (text) lines.push(`- ${text}`);
        }
        lines.push("");
      }
    } else if (type === "projects") {
      for (const item of section.items || []) {
        const meta = [item.name, item.dates].filter(Boolean).join(" | ");
        if (meta) lines.push(meta);
        if (item.description) lines.push(item.description);
        for (const bullet of item.bullets || []) {
          const text = typeof bullet === "string" ? bullet : bullet.text;
          if (text) lines.push(`- ${text}`);
        }
        lines.push("");
      }
    } else {
      if (section.content) lines.push(section.content);
      for (const bullet of section.bullets || []) {
        const text = typeof bullet === "string" ? bullet : bullet.text;
        if (text) lines.push(`- ${text}`);
      }
    }
    lines.push("");
  }
  return lines.join("\n").replace(/\n{3,}/g, "\n\n").trim();
}

export function moveSection(sections, fromIndex, toIndex) {
  if (fromIndex === toIndex || fromIndex < 0 || toIndex < 0 || fromIndex >= sections.length || toIndex >= sections.length) {
    return sections;
  }
  const next = [...sections];
  const [item] = next.splice(fromIndex, 1);
  next.splice(toIndex, 0, item);
  return next;
}

export function addExperienceItem(section) {
  return {
    ...section,
    items: [
      ...(section.items || []),
      { id: nid("e"), company: "", title: "", location: "", dates: "", bullets: [{ id: nid("b"), text: "" }] },
    ],
  };
}

export function addProjectItem(section) {
  return {
    ...section,
    items: [
      ...(section.items || []),
      { id: nid("p"), name: "", dates: "", description: "", bullets: [{ id: nid("b"), text: "" }] },
    ],
  };
}

export function addBullet(list = []) {
  return [...list, { id: nid("b"), text: "" }];
}

export function cloneDocument(document) {
  return JSON.parse(JSON.stringify(document));
}

export function applyPresentation(document, presentation) {
  return { ...document, ...presentation };
}

export function summarizeWhatChanged(improvementReport = {}, result = {}) {
  const items = [];
  const reportChanges = improvementReport.changes || improvementReport.what_changed || [];
  for (const item of reportChanges) {
    items.push(typeof item === "string" ? item : (item.label || item.description || "Change recorded"));
  }
  if (improvementReport.sections_improved?.length) {
    items.push(`Sections modified: ${improvementReport.sections_improved.join(", ")}`);
  }
  if (improvementReport.bullets_rewritten) {
    items.push(`Bullets rewritten: ${improvementReport.bullets_rewritten}`);
  }
  if (improvementReport.keywords_added?.length) {
    items.push(`Keywords strengthened: ${improvementReport.keywords_added.join(", ")}`);
  }
  if (improvementReport.new_skills_added?.length) {
    items.push(`Skills reorganized / added from evidence: ${improvementReport.new_skills_added.join(", ")}`);
  }
  if (result.confirmed_skills_used?.length) {
    items.push(`Confirmed skills incorporated: ${result.confirmed_skills_used.join(", ")}`);
  }
  if (result.unsupported_keywords_not_added?.length) {
    items.push(`Unsupported JD requirements excluded: ${result.unsupported_keywords_not_added.join(", ")}`);
  }
  if (!items.length) {
    items.push(result.content_changed ? "Summary and wording refined within evidence constraints." : "No content changes recorded.");
  }
  return items;
}
