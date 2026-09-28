import { useEffect, useRef, useState } from "react";
import { useAltStart } from "./lib/altStart";
import GridLayout, { WidthProvider } from "react-grid-layout";
import ProjectFormModal from "./components/ProjectFormModal";
import ProjectPanel from "./components/ProjectPanel";
import Sidebar from "./components/Sidebar";
import SettingsModal from "./components/SettingsModal";
import SchedulesModal from "./components/SchedulesModal";
import StandardsModal from "./components/StandardsModal";
import { apiRequest, buildWebSocketUrl } from "./lib/api";

const AutoWidthGrid = WidthProvider(GridLayout);

// The server beats every 15s. Three missed beats is dead, not quiet.
const STALE_CONNECTION_MS = 45000;

function updateProject(projects, projectId, updater) {
  return projects.map((project) => {
    if (project.id !== projectId) {
      return project;
    }
    return typeof updater === "function" ? updater(project) : { ...project, ...updater };
  });
}

function currentFocusedProjectId() {
  return new URLSearchParams(window.location.search).get("project");
}

function readSidebarCollapsed() {
  try {
    return window.localStorage.getItem("projectDeck.sidebarCollapsed") === "1";
  } catch (error) {
    return false;
  }
}

function readTheme() {
  try {
    const value = window.localStorage.getItem("projectDeck.theme");
    // Default to "auto" (follows local day/night) for a fresh install.
    return value === "dark" || value === "light" || value === "auto" ? value : "auto";
  } catch (error) {
    return "auto";
  }
}

export default function App() {
  const [appState, setAppState] = useState({ dashboard: {}, projects: [] });
  const appStateRef = useRef(appState);
  appStateRef.current = appState;

  const [searchQuery, setSearchQuery] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [isFormOpen, setIsFormOpen] = useState(false);
  const [editingProject, setEditingProject] = useState(null);
  const [viewportWidth, setViewportWidth] = useState(window.innerWidth);
  const [focusedProjectId, setFocusedProjectId] = useState(currentFocusedProjectId());
  const [sidebarCollapsed, setSidebarCollapsed] = useState(readSidebarCollapsed());
  const [activeCategory, setActiveCategory] = useState("All");
  const [standardsProject, setStandardsProject] = useState(null);
  const [standardsData, setStandardsData] = useState(null);
  const [standardsLoading, setStandardsLoading] = useState(false);
  const [standardsApplying, setStandardsApplying] = useState(false);
  const [standardsApplyResult, setStandardsApplyResult] = useState(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [schedulesOpen, setSchedulesOpen] = useState(false);
  const [portsResolving, setPortsResolving] = useState(false);
  const [isShuttingDown, setIsShuttingDown] = useState(false);
  const [restartState, setRestartState] = useState(null); // null | "working" | "failed"
  const [connectionLive, setConnectionLive] = useState(true);
  const [themeMode, setThemeMode] = useState(readTheme());

  useEffect(() => {
    function effectiveTheme(mode) {
      if (mode === "auto") {
        const hour = new Date().getHours();
        return hour >= 19 || hour < 7 ? "dark" : "light";
      }
      return mode;
    }
    function apply() {
      document.documentElement.dataset.theme = effectiveTheme(themeMode);
    }
    apply();
    try {
      window.localStorage.setItem("projectDeck.theme", themeMode);
    } catch (error) {
      /* ignore storage failures */
    }
    if (themeMode === "auto") {
      const id = window.setInterval(apply, 60000); // follow local time
      return () => window.clearInterval(id);
    }
    return undefined;
  }, [themeMode]);

  async function handleQuit() {
    const confirmed = window.confirm(
      "Quit localdeck? This stops every running project and shuts down the server."
    );
    if (!confirmed) {
      return;
    }
    setIsShuttingDown(true);
    try {
      await apiRequest("/api/shutdown", { method: "POST" });
    } catch (error) {
      /* server may close the connection before responding — that's expected */
    }
  }

  async function handleRestartDeck() {
    const confirmed = window.confirm(
      "Restart localdeck? Every running project is stopped and the server restarts " +
        "itself in place — the Terminal window stays open and this page reconnects on its own."
    );
    if (!confirmed) {
      return;
    }
    setRestartState("working");
    try {
      await apiRequest("/api/restart", { method: "POST" });
    } catch (error) {
      const message = error?.message ?? "";
      // A dropped connection is expected here: the server is replacing itself,
      // so the response may never arrive. Anything else is a real refusal.
      if (message && !/fetch|network|load failed/i.test(message)) {
        setRestartState(null);
        setErrorMessage(
          /not found|method not allowed/i.test(message)
            ? "This localdeck is still running the server it started with, which has no " +
                "restart endpoint yet. Quit and relaunch once — after that, Restart deck works " +
                "from here."
            : message
        );
        return;
      }
    }

    // Wait for the replacement to answer, then reload so the page is definitely
    // the one the new server serves.
    const deadline = Date.now() + 90000;
    while (Date.now() < deadline) {
      await new Promise((resolve) => window.setTimeout(resolve, 700));
      try {
        const health = await apiRequest("/api/health");
        if (health?.status === "ok") {
          window.location.reload();
          return;
        }
      } catch (error) {
        /* still down; keep waiting */
      }
    }
    setRestartState("failed");
  }

  function toggleSidebar() {
    setSidebarCollapsed((current) => {
      const next = !current;
      try {
        window.localStorage.setItem("projectDeck.sidebarCollapsed", next ? "1" : "0");
      } catch (error) {
        /* ignore storage failures */
      }
      window.requestAnimationFrame(() => window.dispatchEvent(new Event("resize")));
      return next;
    });
  }

  useEffect(() => {
    let isMounted = true;
    let socket = null;
    let reconnectTimer = null;
    let watchdog = null;
    let attempts = 0;
    // When the last frame of any kind arrived. The server beats every 15s, so
    // prolonged silence means the connection is dead, not that nothing happened.
    let lastFrameAt = Date.now();

    async function refreshState() {
      try {
        const state = await apiRequest("/api/state");
        if (isMounted) {
          setAppState(state);
        }
      } catch (error) {
        if (isMounted) {
          setErrorMessage(error.message);
        }
      }
    }

    function handleMessage(event) {
      const message = JSON.parse(event.data);
      // "heartbeat" needs no handling; arriving at all is the whole point.
      if (message.event === "snapshot") {
        setAppState(message.payload);
        return;
      }
      if (message.event === "project-runtime") {
        setAppState((current) => ({
          ...current,
          projects: updateProject(current.projects, message.payload.project_id, (project) => ({
            ...project,
            runtime: message.payload.runtime,
          })),
        }));
        return;
      }
      if (message.event === "project-log") {
        setAppState((current) => ({
          ...current,
          projects: updateProject(current.projects, message.payload.project_id, (project) => ({
            ...project,
            // log_seq travels with the lines it describes — the terminal uses it
            // to know what is new once the log buffer stops growing.
            runtime: {
              ...project.runtime,
              logs: message.payload.logs,
              log_seq: message.payload.log_seq,
            },
          })),
        }));
        return;
      }
      if (message.event === "project-preview") {
        setAppState((current) => ({
          ...current,
          projects: updateProject(current.projects, message.payload.project_id, (project) => ({
            ...project,
            preview: message.payload.preview,
          })),
        }));
      }
    }

    function connect() {
      if (!isMounted) {
        return;
      }
      // Every listener checks that it still belongs to the current socket, so a
      // socket we have given up on can never schedule a second reconnect or
      // reset the clock for the one that replaced it.
      const ws = new WebSocket(buildWebSocketUrl());
      socket = ws;
      lastFrameAt = Date.now();

      ws.addEventListener("open", () => {
        if (!isMounted || socket !== ws) {
          return;
        }
        attempts = 0;
        lastFrameAt = Date.now();
        setConnectionLive(true);
        setErrorMessage("");
        refreshState(); // resync anything missed while disconnected
      });
      ws.addEventListener("message", (event) => {
        if (!isMounted || socket !== ws) {
          return;
        }
        lastFrameAt = Date.now();
        setConnectionLive(true);
        handleMessage(event);
      });
      ws.addEventListener("close", () => {
        if (!isMounted || socket !== ws) {
          return;
        }
        socket = null;
        setConnectionLive(false);
        const delay = Math.min(1000 * 2 ** attempts, 8000);
        attempts += 1;
        reconnectTimer = window.setTimeout(connect, delay);
      });
      ws.addEventListener("error", () => {
        try {
          ws.close();
        } catch (error) {
          /* ignore */
        }
      });
    }

    /** Throw away the current connection and start a new one immediately. */
    function reconnectNow() {
      if (!isMounted) {
        return;
      }
      attempts = 0; // a fresh problem deserves a prompt retry, not a long backoff
      if (reconnectTimer) {
        window.clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      const dead = socket;
      socket = null;
      if (dead) {
        try {
          dead.close();
        } catch (error) {
          /* ignore */
        }
      }
      connect();
    }

    refreshState();
    connect();

    // The watchdog. Without it, a connection that dies without saying so leaves
    // the dashboard silently frozen — Start really starts the project and the
    // panel never changes, which is indistinguishable from a broken button.
    watchdog = window.setInterval(() => {
      if (!isMounted || document.visibilityState !== "visible") {
        return; // a hidden tab's timers are throttled; judging it would be unfair
      }
      const silentFor = Date.now() - lastFrameAt;
      const open = socket && socket.readyState === WebSocket.OPEN;
      if (!open || silentFor > STALE_CONNECTION_MS) {
        setConnectionLive(false);
        if (open) {
          reconnectNow(); // looks open, delivers nothing: replace it
        }
      }
    }, 5000);

    // Coming back to the tab (or to the network) is exactly when the connection
    // is most likely to be stale, and exactly when he is looking at it.
    function resync() {
      if (!isMounted || document.visibilityState !== "visible") {
        return;
      }
      refreshState(); // correct on screen right away, whatever the socket is doing
      if (!socket || socket.readyState !== WebSocket.OPEN) {
        reconnectNow();
      }
    }

    const resizeHandler = () => setViewportWidth(window.innerWidth);
    const popstateHandler = () => setFocusedProjectId(currentFocusedProjectId());
    window.addEventListener("resize", resizeHandler);
    window.addEventListener("popstate", popstateHandler);
    window.addEventListener("online", resync);
    window.addEventListener("focus", resync);
    document.addEventListener("visibilitychange", resync);

    return () => {
      isMounted = false;
      if (reconnectTimer) {
        window.clearTimeout(reconnectTimer);
      }
      if (watchdog) {
        window.clearInterval(watchdog);
      }
      const dead = socket;
      socket = null; // makes every remaining listener a no-op
      if (dead) {
        try {
          dead.close();
        } catch (error) {
          /* ignore */
        }
      }
      window.removeEventListener("resize", resizeHandler);
      window.removeEventListener("popstate", popstateHandler);
      window.removeEventListener("online", resync);
      window.removeEventListener("focus", resync);
      document.removeEventListener("visibilitychange", resync);
    };
  }, []);

  const categoryList = Array.from(
    new Set([
      ...(appState.dashboard?.categories ?? []),
      ...appState.projects.map((project) => project.category).filter(Boolean),
    ])
  );
  const visibleProjects = appState.projects.filter((project) => project.panel?.visible);
  const expandedProjectId = appState.dashboard?.expanded_project_id ?? null;
  const expandedProject = appState.projects.find((project) => project.id === expandedProjectId) ?? null;
  const focusedProject = appState.projects.find((project) => project.id === focusedProjectId) ?? null;
  const isCompact = viewportWidth < 960;

  async function perform(requestFactory, options = {}) {
    setErrorMessage("");
    try {
      return await requestFactory();
    } catch (error) {
      setErrorMessage(error.message);
      if (options.rethrow) {
        throw error;
      }
    }
  }

  async function handleStart(projectId, extraArgs = null) {
    await perform(() =>
      apiRequest(`/api/projects/${projectId}/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ extra_args: extraArgs }),
      })
    );
  }

  async function handleStop(projectId) {
    await perform(() => apiRequest(`/api/projects/${projectId}/stop`, { method: "POST" }));
  }

  // The alternate start: one implementation, shared by the panel button and
  // the sidebar row, so both show the same progress and neither can run twice.
  const { altStatus, altUrl, runAltStart } = useAltStart({
    getProject: (projectId) =>
      appStateRef.current.projects.find((project) => project.id === projectId),
    onStart: handleStart,
    onStop: handleStop,
    onRestart: handleRestart,
    onOpenAppTab: openApplicationTab,
  });

  async function handleRestart(projectId, extraArgs = null) {
    await perform(() =>
      apiRequest(`/api/projects/${projectId}/restart`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ extra_args: extraArgs }),
      })
    );
  }

  async function handleClearLogs(projectId) {
    await perform(() => apiRequest(`/api/projects/${projectId}/clear-logs`, { method: "POST" }));
  }

  async function handleSetVisibility(projectId, visible) {
    await perform(() =>
      apiRequest(`/api/projects/${projectId}/visibility`, {
        method: "POST",
        body: JSON.stringify({ visible }),
      })
    );
  }

  async function handleSetArchived(projectId, archived) {
    await perform(() =>
      apiRequest(`/api/projects/${projectId}/archive`, {
        method: "POST",
        body: JSON.stringify({ archived }),
      })
    );
  }

  async function handleSetExpanded(projectId) {
    await perform(() =>
      apiRequest("/api/dashboard/expanded", {
        method: "POST",
        body: JSON.stringify({ project_id: projectId }),
      })
    );
  }

  async function handleStartAll() {
    await perform(() => apiRequest("/api/projects/start-all", { method: "POST" }));
  }

  async function handleStopAll() {
    await perform(() => apiRequest("/api/projects/stop-all", { method: "POST" }));
  }

  async function handleResetLayout() {
    await perform(() => apiRequest("/api/dashboard/reset-layout", { method: "POST" }));
  }

  async function handleResolvePorts() {
    setPortsResolving(true);
    await perform(() => apiRequest("/api/dashboard/resolve-ports", { method: "POST" }));
    setPortsResolving(false);
  }

  async function handleReorderProjects(orderedIds) {
    await perform(() =>
      apiRequest("/api/projects/reorder", {
        method: "POST",
        body: JSON.stringify({ order: orderedIds }),
      })
    );
  }

  async function handleAddCategory(name) {
    await perform(() =>
      apiRequest("/api/dashboard/categories/add", {
        method: "POST",
        body: JSON.stringify({ name }),
      })
    );
  }

  async function handleRemoveCategory(name) {
    if (activeCategory === name) {
      setActiveCategory("All");
    }
    await perform(() =>
      apiRequest("/api/dashboard/categories/remove", {
        method: "POST",
        body: JSON.stringify({ name }),
      })
    );
  }

  async function handleSetProjectCategory(projectId, category) {
    await perform(() =>
      apiRequest(`/api/projects/${projectId}/category`, {
        method: "POST",
        body: JSON.stringify({ category }),
      })
    );
  }

  async function handleSubmitProject(payload) {
    if (editingProject) {
      await perform(
        () =>
          apiRequest(`/api/projects/${editingProject.id}`, {
            method: "PUT",
            body: JSON.stringify(payload),
          }),
        { rethrow: true }
      );
      return;
    }

    await perform(
      () =>
        apiRequest("/api/projects", {
          method: "POST",
          body: JSON.stringify(payload),
        }),
      { rethrow: true }
    );
  }

  async function handleDeleteProject(project) {
    const confirmed = window.confirm(
      `Remove ${project.name} from localdeck? This will only delete the dashboard configuration.`
    );
    if (!confirmed) {
      return;
    }

    await perform(() =>
      apiRequest(`/api/projects/${project.id}`, {
        method: "DELETE",
      })
    );
    setIsFormOpen(false);
    setEditingProject(null);
    if (focusedProjectId === project.id) {
      closeFocusedView();
    }
  }

  async function handlePersistPanel(projectId, nextPanel) {
    await perform(() =>
      apiRequest(`/api/projects/${projectId}/panel`, {
        method: "POST",
        body: JSON.stringify(nextPanel),
      })
    );
  }

  async function handleLayoutCommit(layout) {
    const updates = layout
      .map((item) => {
        const project = appState.projects.find((candidate) => candidate.id === item.i);
        if (!project) {
          return null;
        }

        const nextPanel = {
          ...project.panel,
          x: item.x,
          y: item.y,
          w: item.w,
          h: item.h,
        };

        const didChange =
          nextPanel.x !== project.panel.x ||
          nextPanel.y !== project.panel.y ||
          nextPanel.w !== project.panel.w ||
          nextPanel.h !== project.panel.h;

        return didChange ? { projectId: project.id, panel: nextPanel } : null;
      })
      .filter(Boolean);

    if (updates.length === 0) {
      return;
    }

    await perform(async () => {
      for (const update of updates) {
        await apiRequest(`/api/projects/${update.projectId}/panel`, {
          method: "POST",
          body: JSON.stringify(update.panel),
        });
      }
    });
  }

  async function openStandards(project) {
    setStandardsProject(project);
    setStandardsData(null);
    setStandardsApplyResult(null);
    setStandardsLoading(true);
    await perform(async () => {
      const result = await apiRequest(`/api/projects/${project.id}/conformance`);
      setStandardsData(result.scorecard);
    });
    setStandardsLoading(false);
  }

  function closeStandards() {
    setStandardsProject(null);
    setStandardsData(null);
    setStandardsApplyResult(null);
  }

  async function applyStandards() {
    if (!standardsProject) {
      return;
    }
    setStandardsApplying(true);
    await perform(async () => {
      const result = await apiRequest(`/api/projects/${standardsProject.id}/conformance/apply`, {
        method: "POST",
      });
      setStandardsData(result.scorecard);
      setStandardsApplyResult(result);
    });
    setStandardsApplying(false);
  }

  function openProjectForm(project = null) {
    setEditingProject(project);
    setIsFormOpen(true);
  }

  function openBackgroundTab(href) {
    // Open the tab with "noopener" so the browser gives it its own renderer
    // process. Without it, every app launched from here shares one Chrome
    // process with the dashboard (all of it is 127.0.0.1, i.e. the same site),
    // so a memory-hungry app inflates the dashboard's process and killing that
    // process takes the dashboard and every launched app down together.
    //
    // This used to keep a handle to the child to blur() it and re-focus the
    // dashboard; modern browsers ignore both calls (window.blur() is a no-op
    // per the HTML spec and scripts can't steal focus from a freshly opened
    // tab), so nothing visible changes. ⌘-click / middle-click still opens a
    // background tab natively via the real link.
    window.open(href, "_blank", "noopener,noreferrer");
  }

  function panelTabHref(project) {
    if (project.web_url) {
      return `${window.location.origin}/launch/${project.id}`;
    }
    const url = new URL(window.location.href);
    url.searchParams.set("project", project.id);
    return url.toString();
  }

  function openProjectPanelTab(project, event) {
    // Panel tab = start the project, then open it in a new tab.
    const running =
      project.runtime?.status === "Running" || project.runtime?.status === "Starting";
    if (!running) {
      handleStart(project.id);
    }
    // If the user used a modifier / middle click on the real link, let the browser
    // open it natively in the background (the only reliable way to stay on this page).
    const modified =
      event && (event.metaKey || event.ctrlKey || event.shiftKey || event.button === 1);
    if (modified) {
      return; // don't preventDefault — browser handles the background tab
    }
    if (event && event.preventDefault) {
      event.preventDefault();
    }
    openBackgroundTab(panelTabHref(project));
  }

  function openApplicationTab(url) {
    window.open(url, "_blank", "noopener,noreferrer");
  }

  function closeFocusedView() {
    const url = new URL(window.location.href);
    url.searchParams.delete("project");
    window.history.pushState({}, "", url.toString());
    setFocusedProjectId(null);
  }

  if (focusedProject) {
    return (
      <div className="focused-page">
        <div className="focused-topbar">
          <button className="ghost-button" type="button" onClick={closeFocusedView}>
            Back to dashboard
          </button>
          <span>Dedicated project panel</span>
        </div>
        <ProjectPanel
          project={focusedProject}
          isExpanded
          isFocusedView
          onStart={handleStart}
          onStop={handleStop}
          onRestart={handleRestart}
          onClearLogs={handleClearLogs}
          onHide={(projectId) => handleSetVisibility(projectId, false)}
          onToggleCollapsed={(project) =>
            handlePersistPanel(project.id, {
              ...project.panel,
              collapsed: !project.panel.collapsed,
              h: project.panel.collapsed ? Math.max(project.panel.h, 10) : 3,
            })
          }
          onToggleExpanded={closeFocusedView}
          onEdit={openProjectForm}
          onOpenStandards={openStandards}
          onOpenPanelTab={openProjectPanelTab}
          onOpenAppTab={openApplicationTab}
          onAltStart={runAltStart}
          altStatus={altStatus[focusedProject.id] ?? null}
          altUrl={altUrl[focusedProject.id] ?? null}
        />
        <ProjectFormModal
          isOpen={isFormOpen}
          project={editingProject}
          categories={categoryList}
          onClose={() => setIsFormOpen(false)}
          onDelete={handleDeleteProject}
          onSubmit={handleSubmitProject}
        />
        <StandardsModal
          isOpen={Boolean(standardsProject)}
          project={standardsProject}
          scorecard={standardsData}
          isLoading={standardsLoading}
          isApplying={standardsApplying}
          applyResult={standardsApplyResult}
          onApply={applyStandards}
          onRefresh={() => standardsProject && openStandards(standardsProject)}
          onClose={closeStandards}
        />
      </div>
    );
  }

  return (
    <div className={`app-shell ${sidebarCollapsed ? "sidebar-collapsed" : ""}`}>
      {sidebarCollapsed ? null : (
        <Sidebar
          projects={appState.projects}
          query={searchQuery}
          onQueryChange={setSearchQuery}
          onAddProject={() => openProjectForm(null)}
          onEditProject={openProjectForm}
          onShowProject={(projectId) => handleSetVisibility(projectId, true)}
          onHideProject={(projectId) => handleSetVisibility(projectId, false)}
          onArchiveProject={(projectId) => handleSetArchived(projectId, true)}
          onUnarchiveProject={(projectId) => handleSetArchived(projectId, false)}
          onOpenProjectPanel={openProjectPanelTab}
          onStartProject={handleStart}
          onStopProject={handleStop}
          onAltStart={runAltStart}
          altStatus={altStatus}
          onStartAll={handleStartAll}
          onStopAll={handleStopAll}
          onResetLayout={handleResetLayout}
          onCollapseSidebar={toggleSidebar}
          onOpenSettings={() => setSettingsOpen(true)}
          onOpenSchedules={() => setSchedulesOpen(true)}
          onQuit={handleQuit}
          onRestartDeck={handleRestartDeck}
          categories={categoryList}
          activeCategory={activeCategory}
          onCategoryChange={setActiveCategory}
          onReorder={handleReorderProjects}
          onAddCategory={handleAddCategory}
          onRemoveCategory={handleRemoveCategory}
          onSetCategory={handleSetProjectCategory}
        />
      )}

      <main className="dashboard">
        <div className="dashboard-topbar">
          <div className="dashboard-topbar-lead">
            <button
              className="sidebar-toggle"
              type="button"
              onClick={toggleSidebar}
              title={sidebarCollapsed ? "Show sidebar" : "Hide sidebar"}
              aria-label={sidebarCollapsed ? "Show sidebar" : "Hide sidebar"}
            >
              {sidebarCollapsed ? "☰" : "⟨"}
            </button>
            <h2>Dashboard</h2>
          </div>
          <div className="dashboard-topbar-actions">
            {connectionLive ? null : (
              <div
                className="error-banner"
                title="Buttons still work — they are ordinary requests — but nothing on this page will change until the live connection is back."
              >
                Not receiving live updates — reconnecting…
              </div>
            )}
            {errorMessage ? <div className="error-banner">{errorMessage}</div> : null}
            <button
              className="sidebar-toggle"
              type="button"
              onClick={() =>
                setThemeMode((m) => (m === "light" ? "dark" : m === "dark" ? "auto" : "light"))
              }
              title={
                themeMode === "light"
                  ? "Theme: Light (click for Dark)"
                  : themeMode === "dark"
                    ? "Theme: Dark (click for Auto by time of day)"
                    : "Theme: Auto by local time (click for Light)"
              }
              aria-label="Toggle theme"
            >
              {themeMode === "light" ? "☀" : themeMode === "dark" ? "☾" : "◐"}
            </button>
          </div>
        </div>

        {visibleProjects.length === 0 ? (
          <div className="empty-state">
            <h3>No visible project panels</h3>
            <p>Reopen hidden panels from the sidebar or add a new project to the deck.</p>
          </div>
        ) : (
          <AutoWidthGrid
            className="layout"
            cols={isCompact ? 1 : 12}
            rowHeight={26}
            margin={[18, 18]}
            isResizable={!isCompact}
            isDraggable={!isCompact}
            draggableHandle=".panel-drag-handle"
            draggableCancel=".panel-actions, button, .terminal-shell, .iframe-shell, input, textarea, select"
            resizeHandles={["s", "e", "se", "sw", "ne", "nw", "n", "w"]}
            compactType="vertical"
            onDragStop={handleLayoutCommit}
            onResizeStop={handleLayoutCommit}
            layout={visibleProjects.map((project) => ({
              i: project.id,
              x: isCompact ? 0 : project.panel.x,
              y: project.panel.y,
              w: isCompact ? 1 : project.panel.w,
              h: project.panel.collapsed ? 3 : project.panel.h,
              minW: isCompact ? 1 : 2,
              minH: 3,
              maxW: isCompact ? 1 : 12,
            }))}
          >
            {visibleProjects.map((project) => (
              <div className="grid-item" key={project.id}>
                <ProjectPanel
                  project={project}
                  isExpanded={expandedProject?.id === project.id}
                  onStart={handleStart}
                  onStop={handleStop}
                  onRestart={handleRestart}
                  onClearLogs={handleClearLogs}
                  onHide={(projectId) => handleSetVisibility(projectId, false)}
                  onToggleCollapsed={(item) =>
                    handlePersistPanel(item.id, {
                      ...item.panel,
                      collapsed: !item.panel.collapsed,
                      h: item.panel.collapsed ? Math.max(item.panel.h, 10) : 3,
                    })
                  }
                  onToggleExpanded={(projectId) =>
                    handleSetExpanded(expandedProject?.id === projectId ? null : projectId)
                  }
                  onEdit={openProjectForm}
                  onOpenStandards={openStandards}
                  onOpenPanelTab={openProjectPanelTab}
                  onOpenAppTab={openApplicationTab}
                  onAltStart={runAltStart}
                  altStatus={altStatus[project.id] ?? null}
                  altUrl={altUrl[project.id] ?? null}
                />
              </div>
            ))}
          </AutoWidthGrid>
        )}

        {expandedProject ? (
          <div className="focus-overlay" role="presentation" onClick={() => handleSetExpanded(null)}>
            <div className="focus-panel" onClick={(event) => event.stopPropagation()}>
              <ProjectPanel
                project={expandedProject}
                isExpanded
                onStart={handleStart}
                onStop={handleStop}
                onRestart={handleRestart}
                onClearLogs={handleClearLogs}
                onHide={(projectId) => handleSetVisibility(projectId, false)}
                onToggleCollapsed={(item) =>
                  handlePersistPanel(item.id, {
                    ...item.panel,
                    collapsed: !item.panel.collapsed,
                    h: item.panel.collapsed ? Math.max(item.panel.h, 10) : 3,
                  })
                }
                onToggleExpanded={() => handleSetExpanded(null)}
                onEdit={openProjectForm}
                onOpenStandards={openStandards}
                onOpenPanelTab={openProjectPanelTab}
                onOpenAppTab={openApplicationTab}
                onAltStart={runAltStart}
                altStatus={altStatus[expandedProject.id] ?? null}
                altUrl={altUrl[expandedProject.id] ?? null}
              />
            </div>
          </div>
        ) : null}
      </main>

      <ProjectFormModal
        isOpen={isFormOpen}
        project={editingProject}
        categories={categoryList}
        onClose={() => setIsFormOpen(false)}
        onDelete={handleDeleteProject}
        onSubmit={handleSubmitProject}
      />
      <StandardsModal
        isOpen={Boolean(standardsProject)}
        project={standardsProject}
        scorecard={standardsData}
        isLoading={standardsLoading}
        isApplying={standardsApplying}
        applyResult={standardsApplyResult}
        onApply={applyStandards}
        onRefresh={() => standardsProject && openStandards(standardsProject)}
        onClose={closeStandards}
      />
      <SettingsModal
        isOpen={settingsOpen}
        projects={appState.projects}
        reservedPort={Number(window.location.port) || 8900}
        isResolving={portsResolving}
        onResolvePorts={handleResolvePorts}
        onClose={() => setSettingsOpen(false)}
      />
      <SchedulesModal isOpen={schedulesOpen} onClose={() => setSchedulesOpen(false)} />
      {restartState ? (
        <div className="shutdown-overlay">
          <div className="shutdown-card">
            {restartState === "working" ? (
              <>
                <h2>Restarting localdeck…</h2>
                <p>
                  Projects stopped, server restarting in place. This page reloads itself as soon
                  as it answers again.
                </p>
              </>
            ) : (
              <>
                <h2>localdeck did not come back</h2>
                <p>
                  It stopped answering and has not restarted within 90 seconds. Check the Terminal
                  window it was launched from for the reason.
                </p>
              </>
            )}
          </div>
        </div>
      ) : null}
      {isShuttingDown ? (
        <div className="shutdown-overlay">
          <div className="shutdown-card">
            <h2>localdeck stopped</h2>
            <p>All projects were stopped and the server is shutting down. You can close the Terminal window now.</p>
          </div>
        </div>
      ) : null}
    </div>
  );
}
