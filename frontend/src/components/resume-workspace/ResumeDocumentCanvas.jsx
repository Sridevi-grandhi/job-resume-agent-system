import { useState } from "react";
import { addBullet, addExperienceItem, addProjectItem, SUMMARY_CHANGE_KEY } from "./resumeDocumentUtils";

function Inline({ value, onChange, className = "", placeholder = "", multiline = false, "aria-label": ariaLabel }) {
  if (multiline) {
    return (
      <textarea
        className={`rw-inline rw-inline-area ${className}`}
        value={value || ""}
        placeholder={placeholder}
        aria-label={ariaLabel}
        rows={Math.max(2, String(value || "").split("\n").length)}
        onChange={(event) => onChange(event.target.value)}
      />
    );
  }
  return (
    <input
      className={`rw-inline ${className}`}
      value={value || ""}
      placeholder={placeholder}
      aria-label={ariaLabel}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

function SectionChrome({ index, total, onMove, onAddItem, onRemove, onImprove, improveLabel, children }) {
  return (
    <section className="rw-section">
      <div className="rw-section-chrome">
        <button type="button" disabled={index <= 0} onClick={() => onMove(index, index - 1)} aria-label="Move section up">↑</button>
        <button type="button" disabled={index >= total - 1} onClick={() => onMove(index, index + 1)} aria-label="Move section down">↓</button>
        {onAddItem && <button type="button" onClick={onAddItem}>Add item</button>}
        {onImprove && <button type="button" onClick={onImprove}>{improveLabel || "AI Improve"}</button>}
        {onRemove && <button type="button" className="danger" onClick={onRemove}>Remove</button>}
      </div>
      {children}
    </section>
  );
}

/** Subtle inline indicator shown beside a bullet/summary that carries a
 * tracked AI change — this replaces the old large sidebar bullet-review
 * list; the review happens right where the text lives. */
function ChangeBadge({ entry, onReview }) {
  if (!entry) return null;
  if (entry.status === "accepted") {
    return <span className="rw-ai-badge rw-ai-badge-accepted">✓ Accepted</span>;
  }
  if (entry.status === "manual_edit") {
    return <span className="rw-ai-badge rw-ai-badge-manual">Edited manually</span>;
  }
  if (entry.status === "rejected") {
    return null;
  }
  // "AI Improved" = OpenAI reworded existing candidate content during
  // generation. "AI Generated" = new content created strictly from valid
  // confirmed professional evidence that wasn't on the original resume.
  const label = entry.changeType === "ADDED_FROM_CONFIRMED_EVIDENCE" ? "✨ AI Generated" : "✨ AI Improved";
  return (
    <span className="rw-ai-badge rw-ai-badge-pending">
      {label}
      <button type="button" className="rw-ai-badge-review" onClick={onReview}>Review</button>
    </span>
  );
}

/** Transient, non-blocking note for when a fresh AI Improve request (or
 * opening Review) had nothing meaningfully different to offer — shown
 * inline instead of ever opening an Accept/Reject modal on identical text. */
function NoImprovementNotice({ notice, itemKey }) {
  if (!notice || notice.key !== itemKey) return null;
  return <p className="rw-no-improvement-notice">{notice.message}</p>;
}

export default function ResumeDocumentCanvas({
  document,
  fitMode,
  onChange,
  onImprove,
  improvingKey,
  changeMap = new Map(),
  onReviewChange,
  onManualEdit,
  noImprovementNotice = null,
}) {
  const [dragIndex, setDragIndex] = useState(null);
  if (!document) return null;

  const pageClass = document.pageSize === "a4" ? "a4" : "letter";
  const templateClass = `tpl-${document.templateId || "professional-ats"}`;
  const spacingClass = `spacing-${document.spacing || "normal"}`;
  const style = {
    fontFamily: document.fontFamily || "Calibri, Segoe UI, sans-serif",
    fontSize: `${document.fontSize || 11}pt`,
    textAlign: document.align || "left",
  };

  const updateHeader = (patch) => onChange({ ...document, header: { ...document.header, ...patch } });
  const updateSections = (sections) => onChange({ ...document, sections });
  const updateSection = (index, patch) => {
    const sections = document.sections.map((section, i) => (i === index ? { ...section, ...patch } : section));
    updateSections(sections);
  };
  const moveSection = (from, to) => {
    const sections = [...document.sections];
    const [item] = sections.splice(from, 1);
    sections.splice(to, 0, item);
    updateSections(sections);
  };

  return (
    <div className={`rw-canvas-scroll ${fitMode === "page" ? "fit" : ""}`}>
      <article className={`rw-page ${pageClass} ${templateClass} ${spacingClass}`} style={style}>
        <header className="rw-doc-header">
          <Inline className="rw-name" value={document.header?.name} onChange={(name) => updateHeader({ name })} placeholder="Full name" aria-label="Name" />
          <Inline className="rw-title" value={document.header?.title} onChange={(title) => updateHeader({ title })} placeholder="Professional title" aria-label="Professional title" />
          <div className="rw-contact-row">
            <Inline value={document.header?.email} onChange={(email) => updateHeader({ email })} placeholder="Email" aria-label="Email" />
            <Inline value={document.header?.phone} onChange={(phone) => updateHeader({ phone })} placeholder="Phone" aria-label="Phone" />
            <Inline value={document.header?.location} onChange={(location) => updateHeader({ location })} placeholder="Location" aria-label="Location" />
          </div>
          <Inline
            value={(document.header?.links || []).join(" · ")}
            onChange={(value) => updateHeader({ links: value.split(/[·|]/).map((part) => part.trim()).filter(Boolean) })}
            placeholder="LinkedIn / GitHub / Portfolio"
            aria-label="Links"
          />
        </header>

        {(document.sections || []).map((section, index) => (
          <div
            key={section.id || index}
            draggable
            onDragStart={() => setDragIndex(index)}
            onDragOver={(event) => event.preventDefault()}
            onDrop={() => {
              if (dragIndex == null || dragIndex === index) return;
              moveSection(dragIndex, index);
              setDragIndex(null);
            }}
          >
            <SectionChrome
              index={index}
              total={document.sections.length}
              onMove={moveSection}
              onAddItem={
                section.type === "experience"
                  ? () => updateSection(index, addExperienceItem(section))
                  : section.type === "projects"
                    ? () => updateSection(index, addProjectItem(section))
                    : section.type === "skills"
                      ? () => updateSection(index, { skills: [...(section.skills || []), ""] })
                      : null
              }
              onRemove={() => updateSections(document.sections.filter((_, i) => i !== index))}
              onImprove={section.type === "summary" ? () => onImprove({ key: SUMMARY_CHANGE_KEY, text: section.content || "", context: "summary" }) : null}
              improveLabel={improvingKey === SUMMARY_CHANGE_KEY ? "Improving…" : "AI Improve"}
            >
              <Inline
                className="rw-heading"
                value={section.heading}
                onChange={(heading) => updateSection(index, { heading })}
                placeholder="Section heading"
                aria-label="Section heading"
              />

              {section.type === "summary" && (
                <>
                  <ChangeBadge entry={changeMap.get(SUMMARY_CHANGE_KEY)} onReview={() => onReviewChange?.(SUMMARY_CHANGE_KEY)} />
                  <NoImprovementNotice notice={noImprovementNotice} itemKey={SUMMARY_CHANGE_KEY} />
                  <Inline
                    multiline
                    className="rw-summary"
                    value={section.content}
                    onChange={(content) => {
                      updateSection(index, { content });
                      onManualEdit?.(SUMMARY_CHANGE_KEY, content);
                    }}
                    placeholder="Professional summary"
                    aria-label="Professional summary"
                  />
                </>
              )}

              {section.type === "skills" && (
                <div className="rw-skills">
                  {(section.skills || []).map((skill, skillIndex) => (
                    <span key={`${section.id}-sk-${skillIndex}`} className="rw-skill-chip">
                      <Inline
                        value={skill}
                        onChange={(value) => {
                          const skills = [...(section.skills || [])];
                          skills[skillIndex] = value;
                          updateSection(index, { skills });
                        }}
                        placeholder="Skill"
                        aria-label={`Skill ${skillIndex + 1}`}
                      />
                      <button
                        type="button"
                        className="rw-chip-remove"
                        onClick={() => updateSection(index, { skills: (section.skills || []).filter((_, i) => i !== skillIndex) })}
                        aria-label="Remove skill"
                      >
                        ×
                      </button>
                    </span>
                  ))}
                </div>
              )}

              {section.type === "experience" && (section.items || []).map((item, itemIndex) => (
                <div key={item.id || itemIndex} className="rw-item">
                  <div className="rw-item-meta">
                    <Inline value={item.title} onChange={(title) => {
                      const items = section.items.map((row, i) => (i === itemIndex ? { ...row, title } : row));
                      updateSection(index, { items });
                    }} placeholder="Job title" aria-label="Job title" />
                    <Inline value={item.company} onChange={(company) => {
                      const items = section.items.map((row, i) => (i === itemIndex ? { ...row, company } : row));
                      updateSection(index, { items });
                    }} placeholder="Company" aria-label="Company" />
                    <Inline value={item.location} onChange={(location) => {
                      const items = section.items.map((row, i) => (i === itemIndex ? { ...row, location } : row));
                      updateSection(index, { items });
                    }} placeholder="Location" aria-label="Location" />
                    <Inline value={item.dates} onChange={(dates) => {
                      const items = section.items.map((row, i) => (i === itemIndex ? { ...row, dates } : row));
                      updateSection(index, { items });
                    }} placeholder="Dates" aria-label="Dates" />
                  </div>
                  <ul className="rw-bullets">
                    {(item.bullets || []).map((bullet, bulletIndex) => {
                      const bulletId = typeof bullet === "object" ? bullet.id : null;
                      const bulletText = typeof bullet === "string" ? bullet : bullet.text;
                      return (
                        <li key={bullet.id || bulletIndex}>
                          <ChangeBadge entry={changeMap.get(bulletId)} onReview={() => onReviewChange?.(bulletId)} />
                          <NoImprovementNotice notice={noImprovementNotice} itemKey={bulletId} />
                          <Inline
                            multiline
                            value={bulletText}
                            onChange={(text) => {
                              const items = section.items.map((row, i) => {
                                if (i !== itemIndex) return row;
                                const bullets = row.bullets.map((entry, bi) => (
                                  bi === bulletIndex ? { ...(typeof entry === "object" ? entry : { id: `b-${bi}` }), text } : entry
                                ));
                                return { ...row, bullets };
                              });
                              updateSection(index, { items });
                              if (bulletId) onManualEdit?.(bulletId, text);
                            }}
                            placeholder="Achievement bullet"
                            aria-label="Experience bullet"
                          />
                          <div className="rw-bullet-actions">
                            <button type="button" onClick={() => onImprove({
                              key: bulletId,
                              text: bulletText,
                              context: "bullet",
                            })}>
                              {improvingKey === bulletId ? "Improving…" : "AI Improve"}
                            </button>
                            <button type="button" onClick={() => {
                              const items = section.items.map((row, i) => (
                                i === itemIndex ? { ...row, bullets: row.bullets.filter((_, bi) => bi !== bulletIndex) } : row
                              ));
                              updateSection(index, { items });
                            }}>Delete</button>
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                  <div className="rw-item-actions">
                    <button type="button" onClick={() => {
                      const items = section.items.map((row, i) => (
                        i === itemIndex ? { ...row, bullets: addBullet(row.bullets) } : row
                      ));
                      updateSection(index, { items });
                    }}>Add bullet</button>
                    <button type="button" className="danger" onClick={() => updateSection(index, { items: section.items.filter((_, i) => i !== itemIndex) })}>Remove role</button>
                  </div>
                </div>
              ))}

              {section.type === "projects" && (section.items || []).map((item, itemIndex) => (
                <div key={item.id || itemIndex} className="rw-item">
                  <div className="rw-item-meta">
                    <Inline value={item.name} onChange={(name) => {
                      const items = section.items.map((row, i) => (i === itemIndex ? { ...row, name } : row));
                      updateSection(index, { items });
                    }} placeholder="Project name" aria-label="Project name" />
                    <Inline value={item.dates} onChange={(dates) => {
                      const items = section.items.map((row, i) => (i === itemIndex ? { ...row, dates } : row));
                      updateSection(index, { items });
                    }} placeholder="Dates" aria-label="Project dates" />
                  </div>
                  <Inline
                    multiline
                    value={item.description}
                    onChange={(description) => {
                      const items = section.items.map((row, i) => (i === itemIndex ? { ...row, description } : row));
                      updateSection(index, { items });
                    }}
                    placeholder="Project description"
                    aria-label="Project description"
                  />
                  <ul className="rw-bullets">
                    {(item.bullets || []).map((bullet, bulletIndex) => {
                      const bulletId = typeof bullet === "object" ? bullet.id : null;
                      const bulletText = typeof bullet === "string" ? bullet : bullet.text;
                      return (
                        <li key={bullet.id || bulletIndex}>
                          <ChangeBadge entry={changeMap.get(bulletId)} onReview={() => onReviewChange?.(bulletId)} />
                          <NoImprovementNotice notice={noImprovementNotice} itemKey={bulletId} />
                          <Inline
                            multiline
                            value={bulletText}
                            onChange={(text) => {
                              const items = section.items.map((row, i) => {
                                if (i !== itemIndex) return row;
                                const bullets = row.bullets.map((entry, bi) => (
                                  bi === bulletIndex ? { ...(typeof entry === "object" ? entry : { id: `b-${bi}` }), text } : entry
                                ));
                                return { ...row, bullets };
                              });
                              updateSection(index, { items });
                              if (bulletId) onManualEdit?.(bulletId, text);
                            }}
                            placeholder="Project bullet"
                            aria-label="Project bullet"
                          />
                          <div className="rw-bullet-actions">
                            <button type="button" onClick={() => onImprove({
                              key: bulletId,
                              text: bulletText,
                              context: "bullet",
                            })}>
                              {improvingKey === bulletId ? "Improving…" : "AI Improve"}
                            </button>
                            <button type="button" onClick={() => {
                              const items = section.items.map((row, i) => (
                                i === itemIndex ? { ...row, bullets: row.bullets.filter((_, bi) => bi !== bulletIndex) } : row
                              ));
                              updateSection(index, { items });
                            }}>Delete</button>
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                  <div className="rw-item-actions">
                    <button type="button" onClick={() => {
                      const items = section.items.map((row, i) => (
                        i === itemIndex ? { ...row, bullets: addBullet(row.bullets) } : row
                      ));
                      updateSection(index, { items });
                    }}>Add bullet</button>
                    <button type="button" className="danger" onClick={() => updateSection(index, { items: section.items.filter((_, i) => i !== itemIndex) })}>Remove project</button>
                  </div>
                </div>
              ))}

              {!["summary", "skills", "experience", "projects"].includes(section.type) && (
                <>
                  <Inline
                    multiline
                    value={section.content}
                    onChange={(content) => updateSection(index, { content })}
                    placeholder="Section content"
                    aria-label="Section content"
                  />
                  <ul className="rw-bullets">
                    {(section.bullets || []).map((bullet, bulletIndex) => (
                      <li key={bullet.id || bulletIndex}>
                        <Inline
                          multiline
                          value={typeof bullet === "string" ? bullet : bullet.text}
                          onChange={(text) => {
                            const bullets = (section.bullets || []).map((entry, bi) => (
                              bi === bulletIndex ? { ...(typeof entry === "object" ? entry : { id: `b-${bi}` }), text } : entry
                            ));
                            updateSection(index, { bullets });
                          }}
                          placeholder="Bullet"
                          aria-label="Section bullet"
                        />
                      </li>
                    ))}
                  </ul>
                  <button type="button" onClick={() => updateSection(index, { bullets: addBullet(section.bullets) })}>Add bullet</button>
                </>
              )}
            </SectionChrome>
          </div>
        ))}
      </article>
    </div>
  );
}
