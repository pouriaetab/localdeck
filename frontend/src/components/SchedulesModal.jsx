import { useEffect, useState } from "react";
import { apiRequest } from "../lib/api";

// UI label -> launchd weekday number (0/7 = Sun, 1 = Mon ... 6 = Sat)
const WEEKDAYS = [
  ["Mon", 1],
  ["Tue", 2],
  ["Wed", 3],
  ["Thu", 4],
  ["Fri", 5],
  ["Sat", 6],
  ["Sun", 0],
];

const PRESETS = [
  ["Every weekday", [1, 2, 3, 4, 5]],
  ["Wed & Fri", [3, 5]],
  ["Fri only", [5]],
];

function pad(n) {
  return String(n).padStart(2, "0");
}

export default function SchedulesModal({ isOpen, onClose }) {
  const [tasks, setTasks] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(null);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState({ time: "09:00", weekdays: [1, 2, 3, 4, 5] });
  const [logFor, setLogFor] = useState(null);
  const [logData, setLogData] = useState(null);

  function runClass(s) {
    if (s === "success") return "pass";
    if (s === "running") return "warn";
    if (typeof s === "string" && s.startsWith("failed")) return "fail";
    return "";
  }
  function runIcon(s) {
    if (s === "success") return "✓";
    if (s === "running") return "•";
    if (typeof s === "string" && s.startsWith("failed")) return "✗";
    return "";
  }

  async function load() {
    setError("");
    try {
      const result = await apiRequest("/api/schedules");
      setTasks(result.tasks || []);
    } catch (err) {
      setError(err.message);
      setTasks([]);
    }
  }

  useEffect(() => {
    if (isOpen) {
      setEditing(null);
      load();
    }
  }, [isOpen]);

  if (!isOpen) {
    return null;
  }

  async function act(label, action) {
    setBusy(label);
    setError("");
    try {
      await apiRequest(`/api/schedules/${encodeURIComponent(label)}/action`, {
        method: "POST",
        body: JSON.stringify({ action }),
      });
      await load();
    } catch (err) {
      setError(err.message);
    }
    setBusy(null);
  }

  function openEdit(task) {
    const raw = task.raw_schedule || {};
    setForm({
      time: `${pad(raw.hour ?? 9)}:${pad(raw.minute ?? 0)}`,
      weekdays: raw.weekdays && raw.weekdays.length ? raw.weekdays : [1, 2, 3, 4, 5],
    });
    setEditing(task.label);
  }

  function toggleDay(d) {
    setForm((f) => ({
      ...f,
      weekdays: f.weekdays.includes(d) ? f.weekdays.filter((x) => x !== d) : [...f.weekdays, d],
    }));
  }

  async function toggleLog(label) {
    if (logFor === label) {
      setLogFor(null);
      setLogData(null);
      return;
    }
    setLogFor(label);
    setLogData(null);
    try {
      const r = await apiRequest(`/api/schedules/${encodeURIComponent(label)}/log`);
      setLogData(r);
    } catch (err) {
      setLogData({ stderr: err.message, stdout: "" });
    }
  }

  async function saveEdit(label) {
    const [h, m] = form.time.split(":").map(Number);
    if (!form.weekdays.length) {
      setError("Pick at least one day.");
      return;
    }
    setBusy(label);
    setError("");
    try {
      await apiRequest(`/api/schedules/${encodeURIComponent(label)}/reschedule`, {
        method: "POST",
        body: JSON.stringify({ hour: h, minute: m, weekdays: form.weekdays }),
      });
      setEditing(null);
      await load();
    } catch (err) {
      setError(err.message);
    }
    setBusy(null);
  }

  return (
    <div className="modal-backdrop standards-backdrop" role="presentation" onClick={onClose}>
      <div className="modal-card" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div>
            <p className="eyebrow">Automate</p>
            <h2>Scheduled tasks</h2>
          </div>
          <div className="panel-actions">
            <button className="ghost-button small" type="button" onClick={load}>
              Refresh
            </button>
            <button className="ghost-button" type="button" onClick={onClose}>
              Close
            </button>
          </div>
        </div>

        {error ? <div className="error-banner">{error}</div> : null}

        {tasks === null ? (
          <p className="settings-note">Loading your local scheduled jobs…</p>
        ) : tasks.length === 0 ? (
          <p className="settings-note">
            No scheduled tasks found in <code>~/Library/LaunchAgents</code>.
          </p>
        ) : (
          <ul className="sched-list">
            {tasks.map((t) => (
              <li key={t.label} className="sched-item">
                <div className="sched-row">
                  <div className="sched-main">
                    <strong>{t.project}</strong>
                    <span className="sched-desc">{t.description}</span>
                    <span className="sched-when">⏱ {t.schedule}</span>
                    <span className={`sched-run ${runClass(t.run_status)}`}>
                      ↳ last run: {t.last_run}
                      {runIcon(t.run_status) ? ` ${runIcon(t.run_status)} ${t.run_status}` : ""}
                    </span>
                  </div>
                  <div className="sched-controls">
                    <span className={`status-pill ${t.status === "active" ? "running" : "stopped"}`}>
                      {t.status}
                    </span>
                    {t.status === "active" ? (
                      <button className="ghost-button small" disabled={busy === t.label}
                              type="button" onClick={() => act(t.label, "pause")}>
                        Pause
                      </button>
                    ) : (
                      <button className="secondary-button small" disabled={busy === t.label}
                              type="button" onClick={() => act(t.label, "resume")}>
                        Resume
                      </button>
                    )}
                    <button className="ghost-button small" disabled={busy === t.label}
                            type="button" onClick={() => act(t.label, "run")} title="Run once now">
                      Run now
                    </button>
                    <button className="ghost-button small" disabled={busy === t.label}
                            type="button" onClick={() => act(t.label, "stop")} title="Stop a running instance">
                      Stop
                    </button>
                    <button className="ghost-button small" type="button" onClick={() => toggleLog(t.label)}>
                      {logFor === t.label ? "Hide log" : "Log"}
                    </button>
                    <button className="ghost-button small" type="button"
                            onClick={() => (editing === t.label ? setEditing(null) : openEdit(t))}>
                      {editing === t.label ? "Cancel" : "Reschedule"}
                    </button>
                  </div>
                </div>

                {logFor === t.label ? (
                  <div className="sched-log">
                    {logData === null ? (
                      <p className="settings-note">Loading recent log…</p>
                    ) : (
                      <>
                        {logData.stderr ? (
                          <>
                            <div className="sched-log-label">stderr (most recent)</div>
                            <pre className="sched-log-pre err">{logData.stderr}</pre>
                          </>
                        ) : null}
                        {logData.stdout ? (
                          <>
                            <div className="sched-log-label">stdout (most recent)</div>
                            <pre className="sched-log-pre">{logData.stdout}</pre>
                          </>
                        ) : null}
                        {!logData.stderr && !logData.stdout ? (
                          <p className="settings-note">No log output found for this task.</p>
                        ) : null}
                      </>
                    )}
                  </div>
                ) : null}

                {editing === t.label ? (
                  <div className="sched-edit">
                    <div className="sched-edit-row">
                      <label className="sched-time">
                        <span>Time</span>
                        <input type="time" value={form.time}
                               onChange={(e) => setForm({ ...form, time: e.target.value })} />
                      </label>
                      <div className="sched-days">
                        {WEEKDAYS.map(([name, num]) => (
                          <button key={num} type="button"
                                  className={`day-chip ${form.weekdays.includes(num) ? "on" : ""}`}
                                  onClick={() => toggleDay(num)}>
                            {name}
                          </button>
                        ))}
                      </div>
                    </div>
                    <div className="sched-edit-row">
                      <div className="sched-presets">
                        {PRESETS.map(([name, days]) => (
                          <button key={name} type="button" className="ghost-button small"
                                  onClick={() => setForm({ ...form, weekdays: days })}>
                            {name}
                          </button>
                        ))}
                      </div>
                      <button className="primary-button small" disabled={busy === t.label}
                              type="button" onClick={() => saveEdit(t.label)}>
                        Save schedule
                      </button>
                    </div>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        )}

        <p className="settings-note" style={{ marginTop: "8px" }}>
          Runs use your Mac's launchd. Weekday + time schedules are supported here; "every other
          Friday" or "3rd Friday" cadences need a date check inside the task itself. Holidays: launchd
          has no holiday calendar, so jobs still fire on public holidays unless the task itself skips them.
        </p>
      </div>
    </div>
  );
}
