import { useState } from "react";

function readArchivedOpen() {
  try {
    return window.localStorage.getItem("localdeck.archivedOpen") === "1";
  } catch (error) {
    return false;
  }
}

/**
 * What a project's last run left behind, as one short line.
 *
 * A script that does its work and exits (a nightly sync, say)
 * goes Stopped → Starting → Running → Stopped in a couple of seconds. Press
 * Start, look up, and it says "Stopped" again: it ran, it may well have worked,
 * and the dashboard showed you nothing. Worse for a project whose panel is
 * hidden, because then there is no terminal to look at either. So say when it
 * last ran and how it ended.
 */
function lastRunSummary(runtime) {
  if (!runtime || runtime.status === "Running" || runtime.status === "Starting") {
    return null;
  }
  const finished = runtime.stopped_at;
  if (!finished) {
    return null;
  }
  const when = new Date(finished);
  if (Number.isNaN(when.getTime())) {
    return null;
  }
  const clock = when.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  const code = runtime.exit_code;
  if (code === 0 || code === null || code === undefined) {
    return `ran ${clock}`;
  }
  return `ran ${clock} · exit ${code}`;
}

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

export default function Sidebar({
  projects,
  query,
  onQueryChange,
  onAddProject,
  onEditProject,
  onShowProject,
  onHideProject,
  onArchiveProject,
  onUnarchiveProject,
  onOpenProjectPanel,
  onStartProject,
  onStopProject,
  onAltStart,
  altStatus = {},
  onStartAll,
  onStopAll,
  onResetLayout,
  onCollapseSidebar,
  onOpenSettings,
  onOpenSchedules,
  onQuit,
  onRestartDeck,
  categories = [],
  activeCategory = "All",
  onCategoryChange,
  onReorder,
  onAddCategory,
  onRemoveCategory,
  onSetCategory,
}) {
  const tabs = ["All", ...categories];
  const filteredProjects = projects.filter((project) => {
    const matchesQuery = project.name.toLowerCase().includes(query.toLowerCase());
    const projectCategory = project.category || "General";
    const matchesCategory = activeCategory === "All" || projectCategory === activeCategory;
    return matchesQuery && matchesCategory;
  });
  // Archived projects drop out of the main list entirely and collapse into a
  // tiny strip at the bottom, so old/unused projects stop taking sidebar space.
  const activeProjects = filteredProjects.filter((project) => !project.archived);
  const archivedProjects = filteredProjects.filter((project) => project.archived);

  const [dragId, setDragId] = useState(null);
  const [overId, setOverId] = useState(null);
  const [dropTab, setDropTab] = useState(null);
  const [archivedOpen, setArchivedOpen] = useState(readArchivedOpen);

  function toggleArchivedOpen() {
    setArchivedOpen((current) => {
      const next = !current;
      try {
        window.localStorage.setItem("localdeck.archivedOpen", next ? "1" : "0");
      } catch (error) {
        /* ignore storage failures */
      }
      return next;
    });
  }

  function addCategory() {
    const name = window.prompt("New folder name:");
    if (name && name.trim() && onAddCategory) onAddCategory(name.trim());
  }

  function removeCategory(name) {
    if (window.confirm(`Remove the "${name}" folder? Projects in it move back to All.`) && onRemoveCategory) {
      onRemoveCategory(name);
    }
  }

  function handleDrop(targetId) {
    // Archived projects live in their own collapsed strip and aren't draggable,
    // so reordering only ever operates over the active (non-archived) subset.
    const filteredIds = activeProjects.map((p) => p.id);
    const from = filteredIds.indexOf(dragId);
    const to = filteredIds.indexOf(targetId);
    setDragId(null);
    setOverId(null);
    if (from === -1 || to === -1 || from === to || !onReorder) {
      return;
    }
    const reordered = [...filteredIds];
    reordered.splice(to, 0, reordered.splice(from, 1)[0]);
    // Rebuild the full order: keep non-filtered projects in place, apply the new
    // order to the filtered subset (so reordering inside a category tab is safe).
    const inView = new Set(filteredIds);
    let k = 0;
    const globalIds = projects.map((p) => (inView.has(p.id) ? reordered[k++] : p.id));
    onReorder(globalIds);
  }

  return (
    <aside className="sidebar">
      <div className="sidebar-top">
        <div>
          <h1>localdeck</h1>
        </div>
        <div className="sidebar-top-buttons">
          {onCollapseSidebar ? (
            <button
              className="ghost-button small"
              type="button"
              onClick={onCollapseSidebar}
              title="Hide sidebar"
              aria-label="Hide sidebar"
            >
              ⟨ Hide
            </button>
          ) : null}
          <button className="primary-button" type="button" onClick={onAddProject}>
            Add project
          </button>
        </div>
      </div>

      <div className="sidebar-actions">
        <button className="secondary-button" type="button" onClick={onStartAll}>
          Start all
        </button>
        <button className="secondary-button" type="button" onClick={onStopAll}>
          Stop all
        </button>
        <button className="ghost-button" type="button" onClick={onResetLayout}>
          Reset layout
        </button>
        {onOpenSettings ? (
          <button className="ghost-button" type="button" onClick={onOpenSettings}>
            ⚙ Settings
          </button>
        ) : null}
        {onOpenSchedules ? (
          <button className="ghost-button" type="button" onClick={onOpenSchedules}>
            ⏰ Schedules
          </button>
        ) : null}
        {onRestartDeck ? (
          <button
            className="ghost-button"
            type="button"
            onClick={onRestartDeck}
            title="Stop every project and restart localdeck itself, in place — no Terminal, same window, this page reconnects on its own"
          >
            ⟳ Restart deck
          </button>
        ) : null}
        {onQuit ? (
          <button className="danger-button" type="button" onClick={onQuit} title="Stop all projects and shut down localdeck">
            ⏻ Quit all
          </button>
        ) : null}
      </div>

      <label className="search-box">
        <span>Search projects</span>
        <input
          placeholder="Name or category"
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
        />
      </label>

      <div className="category-tabs" role="tablist">
        {tabs.map((tab) => (
          <div
            key={tab}
            role="tab"
            aria-selected={activeCategory === tab}
            className={`category-tab ${activeCategory === tab ? "active" : ""} ${
              dropTab === tab ? "droptab" : ""
            }`}
            onClick={() => onCategoryChange && onCategoryChange(tab)}
            onDragOver={
              tab === "All"
                ? undefined
                : (e) => {
                    e.preventDefault();
                    if (dropTab !== tab) setDropTab(tab);
                  }
            }
            onDragLeave={() => setDropTab(null)}
            onDrop={
              tab === "All"
                ? undefined
                : (e) => {
                    e.preventDefault();
                    if (dragId && onSetCategory) onSetCategory(dragId, tab);
                    setDropTab(null);
                    setDragId(null);
                  }
            }
            title={tab === "All" ? undefined : "Drop a project here to move it into this folder"}
          >
            <span>{tab}</span>
            {tab !== "All" && onRemoveCategory ? (
              <span
                className="cat-x"
                title="Remove folder"
                onClick={(e) => {
                  e.stopPropagation();
                  removeCategory(tab);
                }}
              >
                ×
              </span>
            ) : null}
          </div>
        ))}
        {onAddCategory ? (
          <button type="button" className="category-tab add" onClick={addCategory} title="Create a folder">
            + Folder
          </button>
        ) : null}
      </div>

      <div className="sidebar-list">
        {activeProjects.length === 0 && archivedProjects.length === 0 ? (
          <p className="sidebar-empty">
            No projects in {activeCategory === "All" ? "this view" : `"${activeCategory}"`} yet.
          </p>
        ) : null}
        {activeProjects.map((project) => (
          <div
            className={`sidebar-project ${dragId === project.id ? "dragging" : ""} ${
              overId === project.id && dragId !== project.id ? "dragover" : ""
            }`}
            key={project.id}
            draggable
            onDragStart={(e) => {
              setDragId(project.id);
              e.dataTransfer.effectAllowed = "move";
              // Required (esp. Safari/Firefox) or the drag never starts.
              e.dataTransfer.setData("text/plain", project.id);
            }}
            onDragOver={(e) => {
              e.preventDefault();
              if (overId !== project.id) setOverId(project.id);
            }}
            onDrop={(e) => {
              e.preventDefault();
              handleDrop(project.id);
            }}
            onDragEnd={() => {
              setDragId(null);
              setOverId(null);
              setDropTab(null);
            }}
          >
            <div className="sidebar-project-main">
              <div className="sidebar-project-title">
                <span className="sidebar-drag-grip" title="Drag to reorder" aria-hidden="true">⠿</span>
                <div>
                  <strong>{project.name}</strong>
                  <p>
                    {project.panel?.visible ? "Visible" : "Hidden"} panel
                    {project.category ? <span className="category-chip">{project.category}</span> : null}
                  </p>
                </div>
              </div>
              <span className="sidebar-project-status">
                <span className={`status-pill ${statusClass(project.runtime?.status)}`}>
                  {project.runtime?.status}
                </span>
                {lastRunSummary(project.runtime) ? (
                  <span className="last-run">{lastRunSummary(project.runtime)}</span>
                ) : null}
              </span>
            </div>

            <div className="sidebar-project-actions">
              <button
                className="ghost-button small"
                type="button"
                onClick={() => onOpenProjectPanel(project)}
              >
                Panel tab
              </button>
              {/* The same press as the panel's button, because the sidebar row
                  is where a hidden project is reachable from at all — and the
                  project that has this is often hidden from the dashboard
                  and reached from here, not from a panel. Shared state, so pressing either
                  disables both while it works. */}
              {project.alt_start && onAltStart ? (
                <button
                  className="secondary-button small"
                  type="button"
                  onClick={() => onAltStart(project.id)}
                  disabled={Boolean(altStatus[project.id])}
                  title={
                    altStatus[project.id] ||
                    project.alt_start.hint ||
                    "Start with the alternate mode"
                  }
                >
                  {altStatus[project.id]
                    ? `${project.alt_start.label || "Alt start"}…`
                    : project.alt_start.label || "Alt start"}
                </button>
              ) : null}
              {project.panel?.visible ? (
                <button
                  className="ghost-button small"
                  type="button"
                  onClick={() => onHideProject(project.id)}
                >
                  Hide
                </button>
              ) : (
                <button
                  className="secondary-button small"
                  type="button"
                  onClick={() => onShowProject(project.id)}
                  title="Show this project's panel on the dashboard again"
                >
                  Show
                </button>
              )}
              <button
                className="ghost-button small"
                type="button"
                onClick={() => onEditProject(project)}
              >
                Edit
              </button>
              {onArchiveProject ? (
                <button
                  className="ghost-button small"
                  type="button"
                  onClick={() => onArchiveProject(project.id)}
                  title="Tuck this project into the Archived strip so it stops taking sidebar space"
                >
                  Archive
                </button>
              ) : null}
              {project.runtime?.status === "Running" || project.runtime?.status === "Starting" ? (
                <button
                  className="danger-button small"
                  type="button"
                  onClick={() => onStopProject(project.id)}
                >
                  Stop
                </button>
              ) : (
                <button
                  className="secondary-button small"
                  type="button"
                  onClick={() => onStartProject(project.id)}
                >
                  Start
                </button>
              )}
            </div>
          </div>
        ))}
      </div>

      {archivedProjects.length > 0 ? (
        <div className="sidebar-archived">
          <button
            type="button"
            className="sidebar-archived-toggle"
            onClick={toggleArchivedOpen}
            aria-expanded={archivedOpen}
          >
            <span aria-hidden="true">{archivedOpen ? "▾" : "▸"}</span>
            Archived ({archivedProjects.length})
          </button>
          {archivedOpen ? (
            <div className="sidebar-archived-list">
              {archivedProjects.map((project) => (
                <div className="sidebar-archived-row" key={project.id}>
                  <span className="sidebar-archived-name" title={project.name}>
                    {project.name}
                  </span>
                  <button
                    className="ghost-button small"
                    type="button"
                    onClick={() => onUnarchiveProject && onUnarchiveProject(project.id)}
                    title="Bring this project back into the main list"
                  >
                    Unarchive
                  </button>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </aside>
  );
}

