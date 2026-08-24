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
  const [currentScore, setCurrentScore] = useState(initialCurrentScore);
  const [score, setScore] = useState(initialScore || null);
  const [fitMode, setFitMode] = useState("page");
  const [panelCollapsed, setPanelCollapsed] = useState(false);
  const [mobileTab, setMobileTab] = useState("editor");
  const [improveDraft, setImproveDraft] = useState(null);
  const [improvingKey, setImprovingKey] = useState("");
  const [improveBusy, setImproveBusy] = useState(false);
  const autosaveTimer = useRef(null);
  const latestDoc = useRef(document);
  latestDoc.current = document;

  useEffect(() => {
    if (initialDocument) {
      setDocument(initialDocument);
      latestDoc.current = initialDocument;
    }
  }, [initialDocument]);

  useEffect(() => {
    if (initialCurrentScore != null) setCurrentScore(initialCurrentScore);
  }, [initialCurrentScore]);

  useEffect(() => {
    if (initialScore) setScore(initialScore);
  }, [initialScore]);

  const pushHistory = (next) => {
    setPast((history) => [...history.slice(-39), cloneDocument(latestDoc.current)]);
    setFuture([]);
    setDocument(next);
    latestDoc.current = next;
    setSaveStatus("unsaved");
    if (autosaveTimer.current) clearTimeout(autosaveTimer.current);
    autosaveTimer.current = setTimeout(() => {
      persist(next, { silent: true, skipScore: true, action: "autosave" });
    }, 1400);
  };

  const persist = async (doc = latestDoc.current, { silent = false, action, skipScore = false } = {}) => {
    if (!onSave) return null;
    if (!silent) setSaveStatus("saving");
    else setSaveStatus((status) => (status === "unsaved" ? "saving" : status));
    try {
      const saved = await onSave({
        document: doc,
        plainText: documentToPlainText(doc),
        action,
        skipScore,
      });
      setSaveStatus("saved");
      if (saved?.edited_score != null && !skipScore) setCurrentScore(saved.edited_score);
      if (saved?.score) setScore(saved.score);
      return saved;
    } catch {
      setSaveStatus("failed");
      return null;
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
  };

  const handleRedo = () => {
    if (!future.length) return;
    const next = future[0];
    setFuture((history) => history.slice(1));
    setPast((history) => [...history, cloneDocument(document)]);
    setDocument(next);
    latestDoc.current = next;
    setSaveStatus("unsaved");
  };

  const recalculate = async () => {
    if (!onScore) return;
    setScoring(true);
    try {
      const scored = await onScore({
        document: latestDoc.current,
        plainText: documentToPlainText(latestDoc.current),
      });
      if (scored?.edited_score != null) setCurrentScore(scored.edited_score);
      if (scored?.score) setScore(scored.score);
    } finally {
      setScoring(false);
    }
  };

  const requestImprove = async ({ path, text, context }) => {
    const key = path.join(".");
    setImprovingKey(key);
    setImproveBusy(true);
    setImproveDraft({ path, original: text || "", suggested: "", context });
    try {
      const response = await onImprove?.({ text, context });
      setImproveDraft({
        path,
        original: text || "",
        suggested: response?.suggested || text || "",
        context,
      });
    } finally {
      setImproveBusy(false);
      setImprovingKey("");
    }
  };

  const acceptImprove = () => {
    if (!improveDraft?.path) return;
    pushHistory(setByPath(document, improveDraft.path, improveDraft.suggested));
    setImproveDraft(null);
  };

  const improvement = (currentScore != null && originalScore != null)
    ? Number(currentScore) - Number(originalScore)
    : null;

  const whatChanged = summarizeWhatChanged(improvementReport, resultMeta || {});

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
          />
        </div>
        <ResumeAtsPanel
          originalScore={originalScore}
          currentScore={currentScore}
          improvement={improvement}
          score={score}
          scoring={scoring}
          gapsUnsupported={gapsUnsupported}
          gapsPreferred={gapsPreferred}
          confirmedSkillsIncorporated={confirmedSkillsIncorporated}
          generationLabel={generationLabel}
          fallbackReason={fallbackReason}
          whatChanged={whatChanged}
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
            <a className="button primary" href={downloadDocxUrl}>Download DOCX</a>
          )}
          {downloadPdfUrl && (
            <a className="button secondary" href={downloadPdfUrl}>Download PDF</a>
          )}
        </div>
      </div>

      <AiImprovePopover
        draft={improveDraft}
        busy={improveBusy}
        onAccept={acceptImprove}
        onReject={() => setImproveDraft(null)}
        onRetry={() => improveDraft && requestImprove(improveDraft)}
      />
    </div>
  );
}
