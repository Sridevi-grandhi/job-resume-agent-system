function providerLabel(llmProvider) {
  if (!llmProvider) return null;
  if (llmProvider === "ollama") return "Ollama";
  if (llmProvider === "openai") return "OpenAI";
  return llmProvider;
}

export default function AiImprovePopover({ draft, busy, onAccept, onReject, onRetry }) {
  if (!draft) return null;
  // Only gate on having *some* suggestion text — a rewrite that lands back on
  // the original wording (e.g. the bullet was already clean) is still a
  // valid, acceptable result. Requiring it to differ left Accept silently
  // disabled with no explanation whenever that happened.
  const hasSuggestion = Boolean(draft.suggested);
  const noChangeSuggested = !draft.noChangeRecommended && hasSuggestion && draft.suggested === draft.original;
  const label = providerLabel(draft.llmProvider);
  return (
    <div className="rw-improve-modal" role="dialog" aria-modal="true" aria-label="AI Improve suggestion">
      <div className="rw-improve-card">
        <header>
          <h3>AI Improve</h3>
          <p>Review AI wording changes. Suggestions are constrained by your resume, job description, and verified evidence.</p>
        </header>
        <div className="rw-improve-compare">
          <div>
            <b>Original</b>
            <p>{draft.original}</p>
          </div>
          <div>
            <b>Suggested {draft.usedLlm && label && <span className="rw-improve-badge">Powered by {label}</span>}</b>
            <p>{draft.suggested || "—"}</p>
          </div>
        </div>
        {busy && <p className="rw-improve-status">Generating another suggestion…</p>}
        {!busy && draft.retryFailed && (
          <p className="rw-improve-status error">Could not generate another suggestion. Please try again.</p>
        )}
        {!busy && !draft.retryFailed && draft.noChangeRecommended && (
          <p className="rw-improve-status">No change recommended — this text already reads well.</p>
        )}
        {!busy && !draft.retryFailed && noChangeSuggested && (
          <p className="rw-improve-status">This text is already well-written — no wording change suggested.</p>
        )}
        <div className="rw-improve-actions">
          <button type="button" className="button primary" disabled={busy || !hasSuggestion} onClick={onAccept}>Accept</button>
          <button type="button" className="button secondary" disabled={busy} onClick={onReject}>Reject</button>
          <button type="button" className="button secondary" disabled={busy} onClick={onRetry}>Try Again</button>
        </div>
      </div>
    </div>
  );
}
