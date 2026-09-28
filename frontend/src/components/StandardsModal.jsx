function statusLabel(status) {
  if (status === "pass") return "Pass";
  if (status === "warn") return "Warn";
  if (status === "fail") return "Fail";
  return status;
}

export default function StandardsModal({
  isOpen,
  project,
  scorecard,
  isLoading,
  applyResult,
  isApplying,
  onApply,
  onRefresh,
  onClose,
}) {
  if (!isOpen || !project) {
    return null;
  }

  const overall = scorecard?.overall;
  const actionable =
    scorecard?.checks?.filter((check) => check.status === "warn" || check.status === "fail") ?? [];

  function handleApply() {
    const confirmed = window.confirm(
      `Write a standards fix-request (CONTROL_DECK_STANDARDS.md) into ${project.name}'s folder?\n\n` +
        "This adds a document describing the required changes. It does not modify the project's existing code."
    );
    if (confirmed) {
      onApply();
    }
  }

  return (
    <div className="modal-backdrop standards-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal-card"
        role="dialog"
        aria-modal="true"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-header">
          <div>
            <p className="eyebrow">Standards check</p>
            <h2>{project.name}</h2>
          </div>
          <button className="ghost-button" type="button" onClick={onClose}>
            Close
          </button>
        </div>

        {isLoading ? (
          <p className="standards-loading">Scanning the project folder…</p>
        ) : !scorecard ? (
          <p className="standards-loading">No results yet.</p>
        ) : (
          <div className="standards-body">
            <div className={`standards-overall status-pill ${overall}`}>
              Overall: {statusLabel(overall)}
              <span className="standards-counts">
                {scorecard.counts.pass} pass · {scorecard.counts.warn} warn · {scorecard.counts.fail} fail
              </span>
            </div>

            <ul className="standards-list">
              {scorecard.checks.map((check) => (
                <li key={check.id} className={`standards-item ${check.status}`}>
                  <div className="standards-item-top">
                    <span className={`status-dot ${check.status}`} aria-hidden="true" />
                    <strong>{check.title}</strong>
                    <span className={`standards-tag ${check.status}`}>{statusLabel(check.status)}</span>
                  </div>
                  <p className="standards-detail">{check.detail}</p>
                  {check.suggestion ? (
                    <p className="standards-suggestion">→ {check.suggestion}</p>
                  ) : null}
                </li>
              ))}
            </ul>

            {applyResult ? (
              <div className="standards-applied">
                Fix request written to <code>{applyResult.written}</code>. Open that project to apply the
                changes, then re-check.
              </div>
            ) : null}

            <div className="modal-actions">
              <button className="ghost-button" type="button" onClick={onRefresh}>
                Re-check
              </button>
              <button
                className="primary-button"
                type="button"
                disabled={isApplying || actionable.length === 0}
                onClick={handleApply}
              >
                {actionable.length === 0
                  ? "All standards met"
                  : isApplying
                    ? "Sending…"
                    : `Approve & send fix plan (${actionable.length})`}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
