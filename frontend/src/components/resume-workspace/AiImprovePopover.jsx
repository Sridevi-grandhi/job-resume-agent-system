export default function AiImprovePopover({ draft, busy, onAccept, onReject, onRetry }) {
  if (!draft) return null;
  return (
    <div className="rw-improve-modal" role="dialog" aria-modal="true" aria-label="AI Improve suggestion">
      <div className="rw-improve-card">
        <header>
          <h3>AI Improve</h3>
          <p>Wording polish only — constrained by your resume, JD, and verified evidence. No invented claims.</p>
        </header>
        <div className="rw-improve-compare">
          <div>
            <b>Original</b>
            <p>{draft.original}</p>
          </div>
          <div>
            <b>Suggested</b>
            <p>{busy ? "Generating…" : (draft.suggested || "—")}</p>
          </div>
        </div>
        <div className="rw-improve-actions">
          <button type="button" className="button primary" disabled={busy || !draft.suggested || draft.suggested === draft.original} onClick={onAccept}>Accept</button>
          <button type="button" className="button secondary" disabled={busy} onClick={onReject}>Reject</button>
          <button type="button" className="button secondary" disabled={busy} onClick={onRetry}>Try Again</button>
        </div>
      </div>
    </div>
  );
}
