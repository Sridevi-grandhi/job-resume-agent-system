import { FONT_FAMILIES, SPACING, TEMPLATES } from "./resumeDocumentUtils";

export default function ResumeEditorToolbar({
  document,
  saveStatus,
  canUndo,
  canRedo,
  onPresentationChange,
  onUndo,
  onRedo,
  onFit,
  fitMode,
}) {
  if (!document) return null;
  return (
    <div className="rw-toolbar" role="toolbar" aria-label="Resume editor toolbar">
      <label className="rw-tool">
        <span>Template</span>
        <select
          value={document.templateId || "professional-ats"}
          onChange={(event) => onPresentationChange({ templateId: event.target.value })}
        >
          {TEMPLATES.map((item) => (
            <option key={item.id} value={item.id}>{item.label}</option>
          ))}
        </select>
      </label>

      <label className="rw-tool">
        <span>Font</span>
        <select
          value={document.fontFamily || "Calibri"}
          onChange={(event) => onPresentationChange({ fontFamily: event.target.value })}
        >
          {FONT_FAMILIES.map((item) => (
            <option key={item.id} value={item.id}>{item.label}</option>
          ))}
        </select>
      </label>

      <div className="rw-tool rw-tool-inline">
        <span>Size</span>
        <button type="button" className="rw-icon-btn" onClick={() => onPresentationChange({ fontSize: Math.max(9, (document.fontSize || 11) - 1) })} aria-label="Decrease font size">−</button>
        <b>{document.fontSize || 11}</b>
        <button type="button" className="rw-icon-btn" onClick={() => onPresentationChange({ fontSize: Math.min(14, (document.fontSize || 11) + 1) })} aria-label="Increase font size">+</button>
      </div>

      <label className="rw-tool">
        <span>Spacing</span>
        <select
          value={document.spacing || "normal"}
          onChange={(event) => onPresentationChange({ spacing: event.target.value })}
        >
          {SPACING.map((item) => (
            <option key={item.id} value={item.id}>{item.label}</option>
          ))}
        </select>
      </label>

      <label className="rw-tool">
        <span>Align</span>
        <select
          value={document.align || "left"}
          onChange={(event) => onPresentationChange({ align: event.target.value })}
        >
          <option value="left">Left</option>
          <option value="center">Center</option>
        </select>
      </label>

      <label className="rw-tool">
        <span>Page</span>
        <select
          value={document.pageSize || "letter"}
          onChange={(event) => onPresentationChange({ pageSize: event.target.value })}
        >
          <option value="letter">US Letter</option>
          <option value="a4">A4</option>
        </select>
      </label>

      <div className="rw-tool-group">
        <button type="button" className="rw-icon-btn" disabled={!canUndo} onClick={onUndo} aria-label="Undo">Undo</button>
        <button type="button" className="rw-icon-btn" disabled={!canRedo} onClick={onRedo} aria-label="Redo">Redo</button>
        <button type="button" className="rw-icon-btn" onClick={onFit} aria-label="Fit to page">
          {fitMode === "page" ? "100%" : "Fit"}
        </button>
      </div>

      <div className={`rw-save-status ${saveStatus}`} aria-live="polite">
        {saveStatus === "saving" && "Saving…"}
        {saveStatus === "saved" && "Saved ✓"}
        {saveStatus === "unsaved" && "Unsaved changes"}
        {saveStatus === "failed" && "Save failed"}
        {saveStatus === "idle" && "Ready"}
      </div>
    </div>
  );
}
