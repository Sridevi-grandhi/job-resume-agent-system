import { useEffect, useRef, useState } from "react";
import AiImprovePopover from "./AiImprovePopover";
import ResumeAtsPanel from "./ResumeAtsPanel";
import ResumeDocumentCanvas from "./ResumeDocumentCanvas";
import ResumeEditorToolbar from "./ResumeEditorToolbar";
import {
  applyPresentation,
  cloneDocument,
  documentToPlainText,
  emptyDocument,
  findBulletIdByText,
  findBulletPathById,
  findSummaryPath,
  isEffectivelyIdentical,
  SUMMARY_CHANGE_KEY,
  summarizeWhatChanged,
} from "./resumeDocumentUtils";

function setByPath(root, path, value) {
  const next = cloneDocument(root);
  let cursor = next;
  for (let i = 0; i < path.length - 1; i += 1) {
    cursor = cursor[path[i]];
  }
  cursor[path[path.length - 1]] = value;
  return next;
}

/** Single source of truth for "what did AI change and what's its review
 * status" — seeded once from the backend's bullet_changes/summary_change
 * diff (joined to the live document's stable bullet ids by text, since the
 * backend diff is computed from plain text and doesn't know frontend ids),
 * then updated in place as the user accepts/rejects/edits. Both the inline
 * document badges and the sidebar summary counts read from this one map. */
function buildInitialChangeMap(document, improvementReport) {
  const map = new Map();
  if (!document) return map;
  for (const change of improvementReport?.bullet_changes || []) {
    if (!change.original_text?.trim() || !change.generated_text?.trim()) continue;
    // Defensive, in addition to the backend's own filtering: an
    // identical/near-identical pair is never a real "AI Improved" change —
    // it must not even appear as a pending review, per the "never ask the
    // user to Accept/Reject two identical strings" rule.
    if (isEffectivelyIdentical(change.original_text, change.generated_text)) continue;
    const bulletId = findBulletIdByText(document, change.generated_text);
    if (!bulletId) continue;
    map.set(bulletId, {
      status: "pending",
      originalText: change.original_text,
      aiText: change.generated_text,
      section: change.section || "",
      context: "bullet",
      changeType: change.change_type || "REWRITTEN",
    });
  }
  const summaryChange = improvementReport?.summary_change;
  if (
    summaryChange?.original_text?.trim()
    && summaryChange?.generated_text?.trim()
    && !isEffectivelyIdentical(summaryChange.original_text, summaryChange.generated_text)
  ) {
    map.set(SUMMARY_CHANGE_KEY, {
      status: "pending",
      originalText: summaryChange.original_text,
      aiText: summaryChange.generated_text,
      section: "Professional Summary",
      context: "summary",
      changeType: "REWRITTEN",
    });
  }
  return map;
}

function pathForKey(document, key) {
  return key === SUMMARY_CHANGE_KEY ? findSummaryPath(document) : findBulletPathById(document, key);
}

function getAtPath(root, path) {
  let cursor = root;
  for (const segment of path || []) cursor = cursor?.[segment];
  return cursor;
}

export default function ResumeWorkspace({
  initialDocument,
  initialText,
  originalScore,
  initialCurrentScore,
  initialScore,
  improvementReport,
  resultMeta,
  gapsUnsupported = [],
  gapsPreferred = [],
  confirmedSkillsIncorporated = [],
  generationLabel = null,
  fallbackReason = null,
  downloadDocxUrl,
  downloadPdfUrl,
  downloadTxtUrl,
  onSave,
  onScore,
  onImprove,
  onRegenerate,
  generating = false,
}) {
  const [document, setDocument] = useState(() => initialDocument || emptyDocument(initialText || ""));
  const [past, setPast] = useState([]);
  const [future, setFuture] = useState([]);
  const [saveStatus, setSaveStatus] = useState("idle");
  const [scoring, setScoring] = useState(false);
  const [scoreNote, setScoreNote] = useState("");
  const [scoreStale, setScoreStale] = useState(false);
  const [currentScore, setCurrentScore] = useState(initialCurrentScore);
  const [score, setScore] = useState(initialScore || null);
  const [fitMode, setFitMode] = useState("page");
  const [panelCollapsed, setPanelCollapsed] = useState(false);
  const [mobileTab, setMobileTab] = useState("editor");
  const [improveDraft, setImproveDraft] = useState(null);
  const [improvingKey, setImprovingKey] = useState("");
  const [improveBusy, setImproveBusy] = useState(false);
  // A fresh AI Improve request (or opening Review) that turns out to have
  // nothing meaningfully different to offer never opens the Accept/Reject
  // modal — this transient, non-blocking notice is shown inline instead.
  const [noImprovementNotice, setNoImprovementNotice] = useState(null);
  const [changeMap, setChangeMap] = useState(() => buildInitialChangeMap(initialDocument, improvementReport));
  const autosaveTimer = useRef(null);
  const latestDoc = useRef(document);
  latestDoc.current = document;
  // Tracks the in-flight persist() promise (if any) so a download triggered
  // while a debounced autosave is still mid-flight can await the SAME
  // request instead of racing ahead of it — see handleDownload.
  const pendingSaveRef = useRef(null);

  useEffect(() => {
    if (initialDocument) {
      setDocument(initialDocument);
      latestDoc.current = initialDocument;
      setChangeMap(buildInitialChangeMap(initialDocument, improvementReport));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialDocument, improvementReport]);

  useEffect(() => {
    if (initialCurrentScore != null) setCurrentScore(initialCurrentScore);
  }, [initialCurrentScore]);

  useEffect(() => {
    if (initialScore) {
      setScore(initialScore);
      if (initialScore.jd_match != null && initialCurrentScore == null) {
        setCurrentScore(initialScore.jd_match);
      }
    }
  }, [initialScore, initialCurrentScore]);

  const pushHistory = (next) => {
    // Snapshot BEFORE latestDoc.current is reassigned below — setPast's
    // updater callback isn't guaranteed to run before that reassignment, so
    // reading latestDoc.current lazily inside it could capture the NEW
    // document instead of the one being replaced.
    const snapshot = cloneDocument(latestDoc.current);
    setPast((history) => [...history.slice(-39), snapshot]);
    setFuture([]);
    setDocument(next);
    latestDoc.current = next;
    setSaveStatus("unsaved");
    setScoreStale(true);
    if (autosaveTimer.current) clearTimeout(autosaveTimer.current);
    autosaveTimer.current = setTimeout(() => {
      persist(next, { silent: true, skipScore: true, action: "autosave" });
    }, 1400);
  };

  const persist = async (doc = latestDoc.current, { silent = false, action, skipScore = false } = {}) => {
    if (!onSave) return null;
    if (!silent) setSaveStatus("saving");
    else setSaveStatus((status) => (status === "unsaved" ? "saving" : status));
    const run = (async () => {
      try {
        const saved = await onSave({
          document: doc,
          plainText: documentToPlainText(doc),
          action,
          skipScore,
        });
        setSaveStatus("saved");
        if (saved?.edited_score != null && !skipScore) {
          setCurrentScore(saved.edited_score);
          setScoreStale(false);
        }
        if (saved?.score) setScore(saved.score);
        return saved;
      } catch {
        setSaveStatus("failed");
        return null;
      }
    })();
    pendingSaveRef.current = run;
    const result = await run;
    if (pendingSaveRef.current === run) pendingSaveRef.current = null;
    return result;
  };

  const [downloadingFormat, setDownloadingFormat] = useState(null);
  const [downloadError, setDownloadError] = useState("");

  // Export action only — never touches ATS scores, evidence, workflow step,
  // or the selected job. Flushes any pending autosave first (reusing the
  // existing persist()/onSave, never a second save system) so the download
  // always reflects the CURRENT editor content, not a stale debounced edit.
  const handleDownload = async (url, format, extension) => {
    if (!url || downloadingFormat) return;
    setDownloadError("");
    setDownloadingFormat(format);
    try {
      if (autosaveTimer.current) {
        clearTimeout(autosaveTimer.current);
        autosaveTimer.current = null;
      }
      // Wait out any debounced autosave that's already mid-flight (e.g. the
      // user clicked Download right after typing, before the 1.4s debounce
      // fired) before layering a fresh save on top — racing ahead of an
      // in-flight PATCH could download using validation_status/final_file_path
      // from an even older save.
      if (pendingSaveRef.current) {
        await pendingSaveRef.current;
      }
      await persist(latestDoc.current, { silent: true, skipScore: true, action: "pre-download-save" });
      const response = await fetch(url);
      if (!response.ok) throw new Error(`download_failed_${response.status}`);
      const blob = await response.blob();
      const disposition = response.headers.get("Content-Disposition") || "";
      const match = /filename\*?=(?:UTF-8'')?"?([^";\n]+)"?/i.exec(disposition);
      const filename = match ? decodeURIComponent(match[1]) : `resume.${extension}`;
      const objectUrl = URL.createObjectURL(blob);
      const link = window.document.createElement("a");
      link.href = objectUrl;
      link.download = filename;
      window.document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(objectUrl);
    } catch {
      setDownloadError("Unable to prepare the resume download. Please try again.");
    } finally {
      setDownloadingFormat(null);
    }
  };

  const handlePresentationChange = (presentation) => {
    pushHistory(applyPresentation(document, presentation));
  };

  const handleUndo = () => {
    if (!past.length) return;
    const previous = past[past.length - 1];
    setPast((history) => history.slice(0, -1));
    setFuture((history) => [cloneDocument(document), ...history]);
    setDocument(previous);
    latestDoc.current = previous;
    setSaveStatus("unsaved");
    setScoreStale(true);
  };

  const handleRedo = () => {
    if (!future.length) return;
    const next = future[0];
    setFuture((history) => history.slice(1));
    setPast((history) => [...history, cloneDocument(document)]);
    setDocument(next);
    latestDoc.current = next;
    setSaveStatus("unsaved");
    setScoreStale(true);
  };

  const recalculate = async () => {
    if (!onScore) return;
    setScoring(true);
    setScoreNote("");
    try {
      // Preferred: auto-save current editor content, then score with the same ATS engine.
      const scored = await onScore({
        document: latestDoc.current,
        plainText: documentToPlainText(latestDoc.current),
      });
      if (scored?.edited_score != null) setCurrentScore(scored.edited_score);
      if (scored?.score) {
        setScore(scored.score);
        if (scored.edited_score == null && scored.score.jd_match != null) {
          setCurrentScore(scored.score.jd_match);
        }
      }
      if (scored?.persisted) setSaveStatus("saved");
      setScoreStale(false);
      if (scored?.score_unchanged && scored?.score_unchanged_reason) {
        setScoreNote(scored.score_unchanged_reason);
      } else if (scored?.edited_score != null) {
        setScoreNote(`Score updated to ${Math.round(Number(scored.edited_score))}%.`);
      }
    } finally {
      setScoring(false);
    }
  };

  // Opens the AI Improve modal for a FRESH request — the bullet/summary is
  // not (yet) a tracked AI change; a new suggestion is fetched immediately.
  // Per the "never ask the user to Accept/Reject two identical strings"
  // rule, a result that's effectively the same as the original (whether the
  // backend explicitly says so, or a redundant client-side normalize check
  // catches it) never opens the comparison modal at all.
  const requestImprove = async ({ key, text, context }) => {
    setNoImprovementNotice(null);
    setImprovingKey(key);
    setImproveBusy(true);
    setImproveDraft({ key, original: text || "", suggested: "", context, usedLlm: false, llmProvider: null, retryFailed: false, noChangeRecommended: false });
    try {
      const response = await onImprove?.({ text, context, previousSuggestion: "" });
      const suggested = response?.suggested || "";
      if (response?.no_change_recommended || isEffectivelyIdentical(suggested, text)) {
        setImproveDraft(null);
        setNoImprovementNotice({
          key,
          message: `No meaningfully different improvement was found for this ${context === "summary" ? "summary" : "bullet"}.`,
        });
        return;
      }
      setImproveDraft({
        key,
        original: text || "",
        suggested,
        context,
        usedLlm: Boolean(response?.used_llm),
        llmProvider: response?.llm_provider || null,
        retryFailed: false,
        noChangeRecommended: false,
      });
    } catch {
      // A technical failure is different from "no improvement found" — still
      // never opens the modal on an identical pair, but frames it as an error.
      setImproveDraft(null);
      setNoImprovementNotice({ key, message: "Could not generate a suggestion. Please try again." });
    } finally {
      setImproveBusy(false);
      setImprovingKey("");
    }
  };

  // Opens the modal for an ALREADY-tracked AI change (inline "AI Improved ·
  // Review" badge) — no API call yet; Original/Suggested are the known
  // pre-AI text and the AI text already showing in the document. Defensive:
  // the source diff already excludes identical/near-identical pairs, but if
  // a stale or malformed entry ever reaches here, never show a broken modal.
  const reviewChange = (key) => {
    setNoImprovementNotice(null);
    const entry = changeMap.get(key);
    if (!entry || !entry.originalText?.trim() || !entry.aiText?.trim() || isEffectivelyIdentical(entry.originalText, entry.aiText)) {
      setNoImprovementNotice({ key, message: "AI change details are unavailable for this item." });
      return;
    }
    setImproveDraft({
      key,
      original: entry.originalText,
      suggested: entry.aiText,
      context: entry.context,
      // Provider isn't tracked in the persisted diff — only a freshly
      // fetched suggestion (below) carries a real llm_provider to attribute.
      usedLlm: false,
      llmProvider: null,
      retryFailed: false,
      noChangeRecommended: false,
    });
  };

  // "Try Again": re-request a suggestion for the SAME bullet/summary. Keeps
  // the current suggestion visible throughout (never blanks it) and only
  // replaces it once a valid, non-empty, meaningfully-different result
  // arrives.
  const retryImprove = async () => {
    if (!improveDraft) return;
    const { key, original, suggested, context } = improveDraft;
    setImproveBusy(true);
    setImproveDraft((current) => (current ? { ...current, retryFailed: false, noChangeRecommended: false } : current));
    try {
      const response = await onImprove?.({ text: original, context, previousSuggestion: suggested });
      if (response?.no_change_recommended) {
        setImproveDraft((current) => (current ? { ...current, noChangeRecommended: true } : current));
        return;
      }
      const nextSuggested = String(response?.suggested || "").trim();
      if (response?.retry_failed || !nextSuggested) {
        setImproveDraft((current) => (current ? { ...current, retryFailed: true } : current));
        return;
      }
      setImproveDraft({
        key,
        original,
        suggested: nextSuggested,
        context,
        usedLlm: Boolean(response?.used_llm),
        llmProvider: response?.llm_provider || null,
        retryFailed: false,
        noChangeRecommended: false,
      });
    } catch {
      setImproveDraft((current) => (current ? { ...current, retryFailed: true } : current));
    } finally {
      setImproveBusy(false);
    }
  };

  const upsertChange = (key, patch) => {
    setChangeMap((current) => {
      const next = new Map(current);
      const existing = next.get(key) || { originalText: improveDraft?.original || "", section: "", context: improveDraft?.context || "bullet", changeType: "REWRITTEN" };
      next.set(key, { ...existing, ...patch });
      return next;
    });
  };

  const acceptImprove = () => {
    if (!improveDraft?.key) return;
    const { key, context, suggested } = improveDraft;
    const path = pathForKey(latestDoc.current, key);
    console.info("AI_CHANGE_ACCEPT_STARTED", { key, context });
    if (path && getAtPath(latestDoc.current, path) !== suggested) {
      pushHistory(setByPath(latestDoc.current, path, suggested));
    }
    upsertChange(key, { status: "accepted", aiText: suggested });
    console.info("AI_CHANGE_ACCEPT_SUCCESS", { key, context });
    setImproveDraft(null);
  };

  const rejectImprove = () => {
    if (improveDraft?.key) {
      const { key, context, original } = improveDraft;
      console.info("AI_CHANGE_REJECTED", { key, context });
      // Only touch the document (and history/save status) if the AI text was
      // actually applied — a Reject on a fresh, not-yet-accepted suggestion
      // has nothing to revert.
      const path = pathForKey(latestDoc.current, key);
      if (path && getAtPath(latestDoc.current, path) !== original) {
        pushHistory(setByPath(latestDoc.current, path, original));
      }
      if (changeMap.has(key)) {
        upsertChange(key, { status: "rejected" });
      }
    }
    setImproveDraft(null);
  };

  // Fired when the user directly edits a bullet/summary that has a tracked
  // AI change, bypassing the review modal entirely — the badge must reflect
  // that this is now the candidate's own wording, not silently keep
  // presenting the old AI suggestion as "current".
  const onManualEdit = (key, newText) => {
    setChangeMap((current) => {
      const entry = current.get(key);
      if (!entry || entry.status === "manual_edit") return current;
      if ((newText || "").trim() === (entry.aiText || "").trim()) return current;
      const next = new Map(current);
      next.set(key, { ...entry, status: "manual_edit" });
      return next;
    });
  };

  const improvement = (currentScore != null && originalScore != null)
    ? Number(currentScore) - Number(originalScore)
    : null;

  const whatChanged = summarizeWhatChanged(improvementReport, resultMeta || {});
  const changeCounts = {
    modified: changeMap.size,
    accepted: 0,
    rejected: 0,
    pending: 0,
    manual: 0,
  };
  for (const entry of changeMap.values()) {
    if (entry.status === "accepted") changeCounts.accepted += 1;
    else if (entry.status === "rejected") changeCounts.rejected += 1;
    else if (entry.status === "manual_edit") changeCounts.manual += 1;
    else changeCounts.pending += 1;
  }

  return (
    <div className="rw-workspace">
      <ResumeEditorToolbar
        document={document}
        saveStatus={saveStatus}
        canUndo={past.length > 0}
        canRedo={future.length > 0}
        onPresentationChange={handlePresentationChange}
        onUndo={handleUndo}
        onRedo={handleRedo}
        fitMode={fitMode}
        onFit={() => setFitMode((mode) => (mode === "page" ? "actual" : "page"))}
      />

      <div className="rw-mobile-tabs">
        <button type="button" className={mobileTab === "editor" ? "active" : ""} onClick={() => setMobileTab("editor")}>Resume</button>
        <button type="button" className={mobileTab === "ats" ? "active" : ""} onClick={() => setMobileTab("ats")}>ATS Analysis</button>
      </div>

      <div className={`rw-body ${mobileTab === "ats" ? "show-ats" : "show-editor"}`}>
        <div className="rw-main">
          <ResumeDocumentCanvas
            document={document}
            fitMode={fitMode}
            onChange={pushHistory}
            onImprove={requestImprove}
            improvingKey={improvingKey}
            changeMap={changeMap}
            onReviewChange={reviewChange}
            onManualEdit={onManualEdit}
            noImprovementNotice={noImprovementNotice}
          />
        </div>
        <ResumeAtsPanel
          originalScore={originalScore}
          currentScore={currentScore}
          scoreStale={scoreStale}
          improvement={improvement}
          score={score}
          scoring={scoring}
          gapsUnsupported={gapsUnsupported}
          gapsPreferred={gapsPreferred}
          confirmedSkillsIncorporated={confirmedSkillsIncorporated}
          generationLabel={generationLabel}
          fallbackReason={fallbackReason}
          scoreNote={scoreNote}
          whatChanged={whatChanged}
          sectionsModifiedCount={(improvementReport?.sections_improved || []).length}
          changeCounts={changeCounts}
          onRecalculate={recalculate}
          collapsed={panelCollapsed}
          onToggle={() => setPanelCollapsed((value) => !value)}
        />
      </div>

      <div className="rw-action-bar">
        <button type="button" className="button secondary" disabled={generating} onClick={onRegenerate}>
          {generating ? "Generating…" : "Regenerate"}
        </button>
        <button type="button" className="button secondary" onClick={() => persist(document)} disabled={saveStatus === "saving"}>
          Save
        </button>
        <button type="button" className="button secondary" onClick={recalculate} disabled={scoring}>
          {scoring ? "Recalculating…" : "Recalculate Score"}
        </button>
        <div className="rw-download-group">
          {downloadDocxUrl && (
            <button
              type="button"
              className="button primary"
              disabled={Boolean(downloadingFormat)}
              onClick={() => handleDownload(downloadDocxUrl, "docx", "docx")}
            >
              {downloadingFormat === "docx" ? "Preparing download…" : "Download DOCX"}
            </button>
          )}
          {downloadPdfUrl && (
            <button
              type="button"
              className="button secondary"
              disabled={Boolean(downloadingFormat)}
              onClick={() => handleDownload(downloadPdfUrl, "pdf", "pdf")}
            >
              {downloadingFormat === "pdf" ? "Preparing download…" : "Download PDF"}
            </button>
          )}
          {downloadTxtUrl && (
            <button
              type="button"
              className="button secondary"
              disabled={Boolean(downloadingFormat)}
              onClick={() => handleDownload(downloadTxtUrl, "txt", "txt")}
            >
              {downloadingFormat === "txt" ? "Preparing download…" : "Download TXT"}
            </button>
          )}
        </div>
        {downloadError && <p className="rw-download-error">{downloadError}</p>}
      </div>

      <AiImprovePopover
        draft={improveDraft}
        busy={improveBusy}
        onAccept={acceptImprove}
        onReject={rejectImprove}
        onRetry={retryImprove}
      />
    </div>
  );
}
