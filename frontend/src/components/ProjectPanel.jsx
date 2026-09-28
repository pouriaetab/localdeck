import { useEffect, useState } from "react";
import TerminalView from "./TerminalView";
import { appUrl, buildWarnings, envWarnings } from "../lib/appUrl";

function statusClass(status) {
  switch (status) {
    case "Running":
      return "running";
    case "Starting":
      return "starting";
    case "Stopping":
      return "stopping";
    case "Failed":
      return "failed";
    default:
      return "stopped";
  }
}

function canPreview(project) {
  return project.project_type === "web" || project.project_type === "hybrid";
}

/** Returns today's date as "YYYY-MM-DD" in local time. */
export default function ProjectPanel({
  project,
  isExpanded,
  isFocusedView,
  onStart,
  onStop,
  onRestart,
  onClearLogs,
  onHide,
  onToggleCollapsed,
  onToggleExpanded,
  onEdit,
  onOpenStandards,
  onOpenPanelTab,
  onOpenAppTab,
  onAltStart,
  altStatus = null,
  altUrl = null,
}) {
  // Be defensive: never let a project missing a field white-screen the dashboard.
  project = {
    ...project,
    panel: project.panel || {},
    runtime: project.runtime || {
      status: "Stopped",
      pid: null,
      logs: [],
      log_seq: 0,
      last_error: null,
    },
    preview: project.preview || {
      reachable: false,
      last_error: null,
      serving: null,
      html_cacheable: null,
    },
  };

  const [iframeKey, setIframeKey] = useState(0);
  const [iframeLoaded, setIframeLoaded] = useState(false);
  const [iframeWarning, setIframeWarning] = useState(false);

  useEffect(() => {
    setIframeLoaded(false);
    setIframeWarning(false);
    if (!project.web_url || !canPreview(project) || project.panel.collapsed) {
      return undefined;
    }

    const timeoutId = window.setTimeout(() => {
      setIframeWarning((current) => (!iframeLoaded ? true : current));
    }, 8000);

    return () => window.clearTimeout(timeoutId);
  }, [iframeKey, iframeLoaded, project.id, project.panel.collapsed, project.web_url, project.runtime.status]);

  const isRunning =
    project.runtime.status === "Running" || project.runtime.status === "Starting";

  // Only ever populated for an app observed serving a build from disk.
  const warnings = buildWarnings(project);
  // Only ever populated when the project has no Python environment that runs.
  const envProblems = envWarnings(project);

  function handleStart() {
    onStart(project.id, null);
  }

  return (
    <article
      className={`project-panel ${project.panel.collapsed ? "collapsed" : ""} ${
        isExpanded ? "expanded" : ""
      } ${isFocusedView ? "focused" : ""} ${project.hide_terminal ? "preview-only" : ""}`}
    >
      <header className="panel-header">
        <div className="panel-title">
          {isExpanded || isFocusedView ? null : (
            <span className="panel-drag-handle" title="Drag to move panel" aria-hidden="true">
              ⠿
            </span>
          )}
          <div>
            <p className="eyebrow">Project</p>
            <h2>{project.name}</h2>
          </div>
          <span className={`status-pill ${statusClass(project.runtime.status)}`}>
            {project.runtime.status}
          </span>
        </div>

        <div className="panel-actions">
          {isRunning ? (
            <button className="danger-button small" type="button" onClick={() => onStop(project.id)}>
              Stop
            </button>
          ) : (
            <button className="primary-button small" type="button" onClick={handleStart}>
              Start
            </button>
          )}
          {/* Shown in BOTH states on purpose. Tucked into the "stopped" branch
              it was invisible exactly when it was wanted: the project already
              running, the panel showing Stop, and the button nowhere on screen.
              The label stays the same either way: what it does is "start in the
              alternate mode", and whether that needs a restart is its business. */}
          {project.alt_start ? (
            <button
              className="secondary-button small"
              type="button"
              onClick={() => onAltStart && onAltStart(project.id)}
              disabled={Boolean(altStatus)}
              title={project.alt_start.hint || "Start with the alternate mode"}
            >
              {altStatus ? `${project.alt_start.label || "Alt start"}…` : project.alt_start.label || "Alt start"}
            </button>
          ) : null}
          <button className="secondary-button small" type="button" onClick={() => onRestart(project.id)}>
            Restart
          </button>
          <button className="ghost-button small" type="button" onClick={() => onClearLogs(project.id)}>
            Clear logs
          </button>
          <button className="ghost-button small" type="button" onClick={() => onToggleCollapsed(project)}>
            {project.panel.collapsed ? "Expand body" : "Collapse"}
          </button>
          <button
            className={`${isExpanded ? "secondary-button" : "ghost-button"} small`}
            type="button"
            onClick={() => onToggleExpanded(project.id)}
          >
            {isExpanded ? "Restore" : "Maximize"}
          </button>
          <a
            className="ghost-button small"
            style={{ textDecoration: "none" }}
            href={
              project.web_url
                ? `${window.location.origin}/launch/${project.id}`
                : `?project=${project.id}`
            }
            target="_blank"
            rel="noopener"
            title="Open in a new tab — ⌘-click (or middle-click) to keep this page in front"
            onClick={(event) => onOpenPanelTab(project, event)}
          >
            Panel tab
          </a>
          {project.web_url ? (
            <button className="ghost-button small" type="button" onClick={() => onOpenAppTab(appUrl(project))}>
              Open app
            </button>
          ) : null}
          {onOpenStandards ? (
            <button className="ghost-button small" type="button" onClick={() => onOpenStandards(project)}>
              Standards
            </button>
          ) : null}
          <button className="ghost-button small" type="button" onClick={() => onEdit(project)}>
            Edit
          </button>
          <button className="ghost-button small" type="button" onClick={() => onHide(project.id)}>
            Hide
          </button>
        </div>
      </header>

      {altStatus ? (
        <div className="alt-start-status" role="status">
          <span className="alt-start-spinner" aria-hidden="true" />
          {altStatus}
        </div>
      ) : null}

      {/* The address, kept on screen after the run. A tab opened automatically
          a minute after the click is a pop-up as far as the browser is
          concerned and gets dropped without a word; this link cannot be, and it
          is still here when you come back for it. */}
      {altUrl ? (
        <div className="alt-start-status" role="status">
          <a href={altUrl} target="_blank" rel="noopener"
             style={{ overflowWrap: "anywhere" }}>
            {altUrl}
          </a>
          <button
            className="ghost-button small"
            type="button"
            onClick={() => navigator.clipboard?.writeText(altUrl)}
          >
            Copy link
          </button>
        </div>
      ) : null}

      {envProblems.length > 0 ? (
        <div className="build-warning" role="status">
          <strong>Python environment</strong>
          {envProblems.map((problem) => (
            <span key={problem}>{problem}</span>
          ))}
        </div>
      ) : null}

      {warnings.length > 0 ? (
        <div className="build-warning" role="status">
          <strong>Stale build check</strong>
          {warnings.map((warning) => (
            <span key={warning}>{warning}</span>
          ))}
        </div>
      ) : null}

      <div className="panel-meta">
        <span>{project.working_directory}</span>
        <span>PID {project.runtime.pid ?? "—"}</span>
        {project.runtime.last_error ? <span className="error-text">{project.runtime.last_error}</span> : null}
      </div>

      {!project.panel.collapsed && canPreview(project) ? (
        <section className="preview-block">
          <div className="preview-header">
            <div>
              <h3>Web preview</h3>
              <p>
                {project.preview.reachable
                  ? "Endpoint reachable."
                  : project.preview.last_error || "Waiting for the app to come online."}
              </p>
            </div>
            <div className="panel-actions">
              <button className="ghost-button small" type="button" onClick={() => setIframeKey((value) => value + 1)}>
                Refresh preview
              </button>
              {project.web_url ? (
                <button className="secondary-button small" type="button" onClick={() => onOpenAppTab(appUrl(project))}>
                  Open in new tab
                </button>
              ) : null}
            </div>
          </div>

          {project.preview.reachable && project.web_url ? (
            <div className="iframe-shell">
              <iframe
                key={`${project.id}-${iframeKey}`}
                src={appUrl(project, iframeKey ? { deckRefresh: iframeKey } : null)}
                title={`${project.name} preview`}
                onLoad={() => setIframeLoaded(true)}
              />
              {!iframeLoaded && project.runtime.status === "Starting" ? (
                <div className="iframe-overlay">Starting application preview...</div>
              ) : null}
              {iframeWarning && !iframeLoaded ? (
                <div className="iframe-warning">
                  Preview is taking longer than expected. The app may still be booting, or it may block iframe
                  embedding. Use <strong>Open in new tab</strong> if the preview stays blank.
                </div>
              ) : null}
            </div>
          ) : (
            <div className="empty-preview">
              <p>The web preview is not reachable yet.</p>
              {project.web_url ? (
                <button className="secondary-button" type="button" onClick={() => onOpenAppTab(appUrl(project))}>
                  Open app directly
                </button>
              ) : null}
            </div>
          )}
        </section>
      ) : null}

      {!project.panel.collapsed && !project.hide_terminal ? (
        <section className="terminal-block">
          <div className="terminal-header">
            <h3>Terminal output</h3>
            <p>Live stdout and stderr stream directly from the project process.</p>
          </div>
          <TerminalView logs={project.runtime.logs} logSeq={project.runtime.log_seq} />
        </section>
      ) : null}
    </article>
  );
}
