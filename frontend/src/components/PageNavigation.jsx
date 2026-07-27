import { useNavigate } from "react-router-dom";

export default function PageNavigation({
  nextPath,
  canContinue,
  disabledMessage,
  showNext = true,
}) {
  const navigate = useNavigate();

  if (!showNext) return null;

  return (
    <div className="page-navigation">
      {!canContinue && disabledMessage && (
        <p className="page-navigation-message">{disabledMessage}</p>
      )}
      <button
        type="button"
        className="button primary"
        disabled={!canContinue}
        onClick={() => navigate(nextPath)}
      >
        Next
      </button>
    </div>
  );
}
