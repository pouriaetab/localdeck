import { useEffect, useState } from "react";

function serializeEnvironment(environment) {
  return Object.entries(environment ?? {})
    .map(([key, value]) => `${key}=${value}`)
    .join("\n");
}

function parseEnvironment(rawText) {
  return rawText
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .reduce((accumulator, line) => {
      const separatorIndex = line.indexOf("=");
      if (separatorIndex < 1) {
        return accumulator;
      }
      const key = line.slice(0, separatorIndex).trim();
      const value = line.slice(separatorIndex + 1).trim();
      accumulator[key] = value;
      return accumulator;
    }, {});
}

function createDefaultForm(project) {
  if (project) {
    return {
      id: project.id ?? "",
      name: project.name ?? "",
      working_directory: project.working_directory ?? "",
      start_command: project.start_command ?? "",
      stop_command: project.stop_command ?? "",
      project_type: project.project_type ?? "terminal",
      web_url: project.web_url ?? "",
      environment: serializeEnvironment(project.environment),
      startup_timeout: project.startup_timeout ?? 15,
      auto_start: project.auto_start ?? false,
      category: project.category ?? "General",
      hide_terminal: project.hide_terminal ?? false,
      visible: project.panel?.visible ?? true,
      collapsed: project.panel?.collapsed ?? false,
    };
  }

  return {
    id: "",
    name: "",
    working_directory: "",
    start_command: "",
    stop_command: "",
    project_type: "terminal",
    web_url: "",
    environment: "",
    startup_timeout: 15,
    auto_start: false,
    category: "General",
    hide_terminal: false,
    visible: true,
    collapsed: false,
  };
}

export default function ProjectFormModal({
  isOpen,
  project,
  categories = [],
  onClose,
  onDelete,
  onSubmit,
}) {
  const [form, setForm] = useState(createDefaultForm(project));
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    setForm(createDefaultForm(project));
    setIsSaving(false);
  }, [project, isOpen]);

  if (!isOpen) {
    return null;
  }

  async function handleSubmit(event) {
    event.preventDefault();
    setIsSaving(true);
    try {
      await onSubmit({
        id: form.id.trim() || undefined,
        name: form.name.trim(),
        working_directory: form.working_directory.trim(),
        start_command: form.start_command.trim(),
        stop_command: form.stop_command.trim() || null,
        project_type: form.project_type,
        web_url: form.web_url.trim() || null,
        environment: parseEnvironment(form.environment),
        startup_timeout: Number(form.startup_timeout) || 15,
        auto_start: Boolean(form.auto_start),
        category: form.category.trim() || "General",
        hide_terminal: Boolean(form.hide_terminal),
        archived: Boolean(project?.archived),
        panel: {
          ...(project?.panel ?? { x: 0, y: 0, w: 6, h: 10 }),
          visible: Boolean(form.visible),
          collapsed: Boolean(form.collapsed),
        },
      });
      onClose();
    } finally {
      setIsSaving(false);
    }
  }

  function updateField(name, value) {
    setForm((current) => ({
      ...current,
      [name]: value,
    }));
  }

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal-card"
        role="dialog"
        aria-modal="true"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-header">
          <div>
            <p className="eyebrow">{project ? "Edit project" : "New project"}</p>
            <h2>{project ? `Update ${project.name}` : "Add a project"}</h2>
          </div>
          <button className="ghost-button" type="button" onClick={onClose}>
            Close
          </button>
        </div>

        <form className="modal-form" onSubmit={handleSubmit}>
          <label>
            <span>Display name</span>
            <input
              required
              value={form.name}
              onChange={(event) => updateField("name", event.target.value)}
            />
          </label>

          <label>
            <span>Custom ID</span>
            <input
              disabled={Boolean(project)}
              placeholder="Optional on create"
              value={form.id}
              onChange={(event) => updateField("id", event.target.value)}
            />
          </label>

          <label className="wide-field">
            <span>Working directory</span>
            <input
              required
              value={form.working_directory}
              onChange={(event) =>
                updateField("working_directory", event.target.value)
              }
            />
          </label>

          <label className="wide-field">
            <span>Start command</span>
            <input
              required
              value={form.start_command}
              onChange={(event) => updateField("start_command", event.target.value)}
            />
          </label>

          <label className="wide-field">
            <span>Stop command</span>
            <input
              placeholder="Optional graceful stop command"
              value={form.stop_command}
              onChange={(event) => updateField("stop_command", event.target.value)}
            />
          </label>

          <label>
            <span>Project type</span>
            <select
              value={form.project_type}
              onChange={(event) => updateField("project_type", event.target.value)}
            >
              <option value="terminal">Terminal</option>
              <option value="web">Web</option>
              <option value="hybrid">Hybrid</option>
            </select>
          </label>

          <label>
            <span>Startup timeout (seconds)</span>
            <input
              min="1"
              step="1"
              type="number"
              value={form.startup_timeout}
              onChange={(event) => updateField("startup_timeout", event.target.value)}
            />
          </label>

          <label>
            <span>Category</span>
            <input
              list="project-categories"
              placeholder="Web, Tools, ..."
              value={form.category}
              onChange={(event) => updateField("category", event.target.value)}
            />
            <datalist id="project-categories">
              {categories.map((name) => (
                <option key={name} value={name} />
              ))}
            </datalist>
          </label>

          <label className="wide-field">
            <span>Web URL</span>
            <input
              placeholder="http://127.0.0.1:3000"
              value={form.web_url}
              onChange={(event) => updateField("web_url", event.target.value)}
            />
          </label>

          <label className="wide-field">
            <span>Environment variables</span>
            <textarea
              placeholder={"KEY=value\nANOTHER_KEY=value"}
              rows={6}
              value={form.environment}
              onChange={(event) => updateField("environment", event.target.value)}
            />
          </label>

          <label className="checkbox-field">
            <input
              checked={form.auto_start}
              type="checkbox"
              onChange={(event) => updateField("auto_start", event.target.checked)}
            />
            <span>Automatically start with localdeck</span>
          </label>

          <label className="checkbox-field">
            <input
              checked={form.visible}
              type="checkbox"
              onChange={(event) => updateField("visible", event.target.checked)}
            />
            <span>Visible on the dashboard</span>
          </label>

          <label className="checkbox-field">
            <input
              checked={form.hide_terminal}
              type="checkbox"
              onChange={(event) => updateField("hide_terminal", event.target.checked)}
            />
            <span>Hide terminal — show only the web preview</span>
          </label>

          <div className="modal-actions">
            {project ? (
              <button
                className="danger-button"
                type="button"
                onClick={() => onDelete(project)}
              >
                Remove project
              </button>
            ) : null}
            <button className="ghost-button" type="button" onClick={onClose}>
              Cancel
            </button>
            <button className="primary-button" disabled={isSaving} type="submit">
              {isSaving ? "Saving..." : project ? "Save project" : "Create project"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
