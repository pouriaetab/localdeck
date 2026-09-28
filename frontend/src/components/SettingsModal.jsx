function buildPortIndex(projects, reservedPort) {
  const index = new Map();
  if (reservedPort) {
    index.set(reservedPort, ["localdeck"]);
  }
  projects.forEach((project) => {
    (project.ports ?? []).forEach((port) => {
      if (!port.value) return;
      const owners = index.get(port.value) ?? [];
      owners.push(project.name);
      index.set(port.value, owners);
    });
  });
  return index;
}

export default function SettingsModal({
  isOpen,
  projects,
  reservedPort,
  isResolving,
  onResolvePorts,
  onClose,
}) {
  if (!isOpen) {
    return null;
  }

  const portIndex = buildPortIndex(projects, reservedPort);
  const isConflicted = (value) => (portIndex.get(value)?.length ?? 0) > 1;
  const conflictCount = Array.from(portIndex.values()).filter((owners) => owners.length > 1).length;
  const projectsWithPorts = projects.filter((p) => (p.ports ?? []).length > 0);

  return (
    <div className="modal-backdrop standards-backdrop" role="presentation" onClick={onClose}>
      <div className="modal-card" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div>
            <p className="eyebrow">Settings</p>
            <h2>Ports &amp; conflicts</h2>
          </div>
          <button className="ghost-button" type="button" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="settings-body">
          <p className="settings-note">
            Each project is assigned ports so several can run at once. localdeck itself uses
            <code> {reservedPort || "8900"}</code>. Conflicts are highlighted in red.
          </p>

          <div className={`settings-status status-pill ${conflictCount > 0 ? "fail" : "pass"}`}>
            {conflictCount > 0
              ? `${conflictCount} port conflict${conflictCount > 1 ? "s" : ""} found`
              : "No port conflicts — all projects can run together"}
          </div>

          {projectsWithPorts.length === 0 ? (
            <p className="settings-note">No projects declare ports yet.</p>
          ) : (
            <ul className="settings-list">
              {projectsWithPorts.map((project) => (
                <li key={project.id} className="settings-item">
                  <div className="settings-item-name">
                    <strong>{project.name}</strong>
                    {project.category ? <span className="category-chip">{project.category}</span> : null}
                  </div>
                  <div className="settings-ports">
                    {(project.ports ?? []).map((port, idx) => (
                      <span
                        key={`${port.env}-${idx}`}
                        className={`port-chip ${isConflicted(port.value) ? "conflict" : ""}`}
                        title={port.env ? `env: ${port.env}` : undefined}
                      >
                        {port.name}: <strong>{port.value}</strong>
                        {isConflicted(port.value) ? " ⚠" : ""}
                      </span>
                    ))}
                  </div>
                </li>
              ))}
            </ul>
          )}

          <div className="modal-actions">
            <button
              className="primary-button"
              type="button"
              disabled={isResolving || conflictCount === 0}
              onClick={onResolvePorts}
            >
              {conflictCount === 0
                ? "All ports unique"
                : isResolving
                  ? "Assigning…"
                  : "Auto-assign unique ports"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
