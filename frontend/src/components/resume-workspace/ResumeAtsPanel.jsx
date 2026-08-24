export default function ResumeAtsPanel({
  originalScore,
  currentScore,
  improvement,
  score,
  scoring,
  gapsUnsupported = [],
  gapsPreferred = [],
  confirmedSkillsIncorporated = [],
  generationLabel = null,
  fallbackReason = null,
  whatChanged = [],
  onRecalculate,
  collapsed,
  onToggle,
}) {
  const mandatory = score?.mandatory_requirements || {};
  const preferred = score?.preferred_requirements || {};
  const coverage = score?.keyword_coverage || {};
  const components = score?.component_scores || score?.component_details || {};
  const componentRows = [
    ["Role alignment", "role_alignment", 20],
    ["Mandatory requirements", "mandatory_skills", 35],
    ["Experience evidence", "experience_evidence", 20],
    ["Preferred requirements", "preferred_skills", 8],
    ["Responsibilities / domain", "responsibilities_domain", 7],
    ["Education / certifications", "education_certifications", 5],
    ["Location / employment", "location_employment", 5],
  ];
  const formatComponent = (key, fallbackMax) => {
    const entry = components[key];
    if (entry && typeof entry === "object" && entry.earned != null && entry.max != null) {
      return `${Math.round(Number(entry.earned))}/${entry.max}`;
    }
    if (entry != null && typeof entry !== "object") {
      return `${Math.round(Number(entry))}/${fallbackMax}`;
    }
    return "—";
  };
  // Prefer live score gaps; fall back to workflow Remaining Gaps (same backend scorer).
  const missingMandatory = (
    (score?.remaining_gaps?.unsupported?.length ? score.remaining_gaps.unsupported : null)
    || (mandatory.missing?.length ? mandatory.missing : null)
    || gapsUnsupported
    || []
  );
  const missingPreferred = (
    (score?.remaining_gaps?.preferred_gaps?.length ? score.remaining_gaps.preferred_gaps : null)
    || (preferred.missing?.length ? preferred.missing : null)
    || gapsPreferred
    || []
  );
  const confirmedUsed = (
    confirmedSkillsIncorporated?.length
      ? confirmedSkillsIncorporated
      : (score?.confirmed_skills_used || score?.confirmed_skills_incorporated || [])
  );

  return (
    <aside className={`rw-ats-panel ${collapsed ? "collapsed" : ""}`}>
      <header className="rw-ats-panel-head">
        <div>
          <h3>ATS / AI Analysis</h3>
          <p>Same scoring engine as your original resume.</p>
        </div>
        <button type="button" className="rw-icon-btn" onClick={onToggle} aria-label="Toggle analysis panel">
          {collapsed ? "Open" : "Hide"}
        </button>
      </header>

      {!collapsed && (
        <>
          <div className="rw-score-trio">
            <div>
              <span>Original</span>
              <b>{originalScore != null ? `${Math.round(Number(originalScore))}%` : "—"}</b>
            </div>
            <div className="current">
              <span>Current</span>
              <b>{scoring ? "…" : (currentScore != null ? `${Math.round(Number(currentScore))}%` : "—")}</b>
            </div>
            <div className="delta">
              <span>Improvement</span>
              <b>
                {Number.isFinite(Number(improvement))
                  ? `${Number(improvement) >= 0 ? "+" : ""}${Math.round(Number(improvement))}`
                  : "—"}
              </b>
            </div>
          </div>

          {generationLabel && (
            <p className="rw-note rw-generation-meta">
              {generationLabel}
              {fallbackReason ? ` · Reason: ${fallbackReason}` : ""}
            </p>
          )}

          <button type="button" className="button primary rw-full" disabled={scoring} onClick={onRecalculate}>
            {scoring ? "Recalculating…" : "Recalculate Score"}
          </button>

          <dl className="rw-metric-list">
            <div><dt>JD Match</dt><dd>{score?.jd_match != null ? `${Math.round(Number(score.jd_match))}%` : (currentScore != null ? `${Math.round(Number(currentScore))}%` : "—")}</dd></div>
            <div><dt>Keyword Coverage</dt><dd>{coverage.matched != null ? `${coverage.matched}/${coverage.required || "—"}` : "—"}</dd></div>
            <div><dt>Mandatory Requirements</dt><dd>{mandatory.total != null ? `${mandatory.covered}/${mandatory.total}` : "—"}</dd></div>
            <div><dt>Preferred Requirements</dt><dd>{preferred.total != null ? `${preferred.covered}/${preferred.total}` : "—"}</dd></div>
            <div><dt>Resume Quality</dt><dd>{score?.resume_quality != null ? Math.round(Number(score.resume_quality)) : formatComponent("experience_evidence", 20).split("/")[0]}</dd></div>
            <div><dt>ATS Formatting</dt><dd>{score?.ats_formatting != null ? Math.round(Number(score.ats_formatting)) : "—"}</dd></div>
          </dl>

          <details className="rw-panel-block rw-why-score">
            <summary>Why this score?</summary>
            <dl className="rw-metric-list">
              {componentRows.map(([label, key, max]) => (
                <div key={key}>
                  <dt>{label}</dt>
                  <dd>{formatComponent(key, max)}</dd>
                </div>
              ))}
              <div>
                <dt>Total (display)</dt>
                <dd>{score?.jd_match != null ? `${Math.round(Number(score.jd_match))}%` : (currentScore != null ? `${Math.round(Number(currentScore))}%` : "—")}</dd>
              </div>
              {score?.raw_score_unrounded != null && (
                <div>
                  <dt>Internal total</dt>
                  <dd>{Number(score.raw_score_unrounded).toFixed(2)} / 100</dd>
                </div>
              )}
            </dl>
            {score?.why_not_95 && <p className="rw-note">{score.why_not_95}</p>}
          </details>

          <section className="rw-panel-block">
            <h4>Remaining Gaps</h4>
            <p className="rw-note">Shown for awareness — nothing unsupported is auto-added.</p>
            {missingMandatory.length > 0 && (
              <div className="rw-gap-group">
                <b>Unsupported / missing mandatory</b>
                <ul>{missingMandatory.map((item) => <li key={`m-${item}`}>{item}</li>)}</ul>
              </div>
            )}
            {missingPreferred.length > 0 && (
              <div className="rw-gap-group preferred">
                <b>Preferred gaps</b>
                <ul>{missingPreferred.map((item) => <li key={`p-${item}`}>{item}</li>)}</ul>
              </div>
            )}
            {!missingMandatory.length && !missingPreferred.length && (
              <p className="rw-note">No remaining scored gaps.</p>
            )}
          </section>

          {confirmedUsed.length > 0 && (
            <section className="rw-panel-block">
              <h4>Confirmed evidence incorporated</h4>
              <ul className="rw-gap-group">
                {confirmedUsed.map((item) => <li key={`c-${item}`}>{item}</li>)}
              </ul>
            </section>
          )}

          <details className="rw-what-changed" open>
            <summary>What Changed</summary>
            <ul>
              {whatChanged.map((item, index) => <li key={`wc-${index}`}>{item}</li>)}
            </ul>
          </details>
        </>
      )}
    </aside>
  );
}
