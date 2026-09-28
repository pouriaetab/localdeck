import { useRef, useState } from "react";
import { appUrl } from "./appUrl";

/**
 * The alternate start (for example "Phone": start with a tunnel) shared by every button that
 * offers it.
 *
 * It lives here rather than in a panel because the same press has to be
 * available from two places at once (the project's panel and the sidebar row),
 * and two copies of a sequence this stateful would drift: one of them would
 * keep restarting an app whose tunnel was already up, or open a stale address,
 * and only on alternate Tuesdays. One implementation, one busy state, both
 * buttons.
 *
 * What a press does:
 *   1. If the project is already running AND has already published its address
 *      on this run, nothing is restarted — the links simply open. Restarting a
 *      running app to reach a tunnel that is already up costs a minute for
 *      nothing.
 *   2. Otherwise it starts (or restarts) the project with the alternate
 *      arguments and waits for the address the project prints itself.
 *   3. It opens that address, then the ordinary desktop app once it answers.
 *
 * `getProject` must return the CURRENT project object, not the one captured
 * when the button was rendered: this sequence waits on the project's own
 * output, so a snapshot that stopped updating would wait forever.
 */
export function useAltStart({ getProject, onStart, onStop, onRestart, onOpenAppTab }) {
  // projectId -> what its alternate start is doing right now, or absent.
  const [status, setStatus] = useState({});
  // projectId -> the address this project published, kept AFTER the run ends.
  //
  // Opening it automatically is not enough and never was: window.open only
  // counts as user-initiated while the click is still on the stack, and this
  // sequence waits a minute for a tunnel first. By then Chrome treats it as a
  // pop-up and drops it silently — no tab, no error, and the operator is left
  // with "nothing opened and I have no idea where the link is".
  //
  // So the address is kept and rendered as an ordinary link. Clicking that IS a
  // gesture, so it can never be blocked, and the link stays there to copy to a
  // phone rather than existing for one instant in a tab that may not appear.
  const [urls, setUrls] = useState({});
  const statusRef = useRef(status);
  statusRef.current = status;

  function setFor(projectId, message) {
    setStatus((current) => {
      const next = { ...current };
      if (message === null) {
        delete next[projectId];
      } else {
        next[projectId] = message;
      }
      return next;
    });
  }

  /**
   * Find a URL the project printed itself, ignoring anything from an earlier run.
   *
   * The address is read out of the project's own output rather than guessed or
   * stored: for a tunnel, the address is random, survives app restarts,
   * and changes when the tunnel restarts, so the only
   * reliable source is the line the app just printed. `log_seq` counts every
   * line the project has ever printed, so "since I pressed the button" is exact
   * even once the 2000-line buffer has rolled.
   */
  function findLoggedUrl(projectId, pattern, sinceSeq) {
    let regex;
    try {
      regex = new RegExp(pattern);
    } catch (error) {
      return null; // a bad pattern in config must not break the button
    }
    const runtime = getProject(projectId)?.runtime ?? {};
    const logs = runtime.logs ?? [];
    const seq = typeof runtime.log_seq === "number" ? runtime.log_seq : logs.length;
    const pending = Math.max(0, seq - sinceSeq);
    const fresh = logs.slice(Math.max(0, logs.length - pending));
    for (let i = fresh.length - 1; i >= 0; i -= 1) {
      const match = fresh[i].match(regex);
      if (match) {
        return match[0];
      }
    }
    return null;
  }

  async function waitForLoggedUrl(projectId, pattern, sinceSeq, attempts = 60) {
    for (let i = 0; i < attempts; i += 1) {
      const found = findLoggedUrl(projectId, pattern, sinceSeq);
      if (found) {
        return found;
      }
      await new Promise((r) => setTimeout(r, 2000));
    }
    return null;
  }

  /** Wait for the app to actually answer before opening a tab at it. */
  async function waitUntilAnswering(url, attempts = 45) {
    for (let i = 0; i < attempts; i += 1) {
      try {
        // no-cors because this is a different port and therefore a different
        // origin. The response is opaque, but a resolved promise still means
        // something accepted the connection, which is all this needs to know.
        await fetch(url, { mode: "no-cors", cache: "no-store" });
        return true;
      } catch (error) {
        await new Promise((r) => setTimeout(r, 2000));
      }
    }
    return false;
  }

  async function runAltStart(projectOrId) {
    const projectId = typeof projectOrId === "string" ? projectOrId : projectOrId?.id;
    const project = getProject(projectId);
    const alt = project?.alt_start;
    if (!alt) return;
    if (statusRef.current[projectId]) return; // one at a time; a second press does nothing

    const label = alt.label || "Alt start";
    const pattern = alt.open_url_pattern;
    const seqAtClick = project.runtime?.log_seq ?? 0;
    const isRunning =
      project.runtime?.status === "Running" || project.runtime?.status === "Starting";
    const alreadyPublished =
      pattern && isRunning ? findLoggedUrl(projectId, pattern, 0) : null;

    try {
      let publishedUrl = alreadyPublished;
      if (!alreadyPublished) {
        if (isRunning) {
          // ONE restart call, not stop + wait + start.
          //
          // Two seconds is not enough. Once this stopped an app and the start
          // that should have followed never happened: uvicorn and vite had not
          // released their ports yet, so the start
          // was refused and the app was left DOWN with the panel still saying
          // "starting". The backend's own restart does both halves in order and
          // clears the ports in between, which is the part a fixed sleep cannot
          // get right — the teardown takes as long as it takes.
          setFor(projectId, `Restarting (${label})…`);
          await onRestart(projectId, alt.args || null);
        } else {
          setFor(projectId, `Starting ${project.name}…`);
          await onStart(projectId, alt.args || null);
        }
        if (pattern) {
          setFor(projectId, "Waiting for the address the project prints…");
          publishedUrl = await waitForLoggedUrl(projectId, pattern, seqAtClick);
        }
      }

      if (publishedUrl) {
        setUrls((current) => ({ ...current, [projectId]: publishedUrl }));
        setFor(projectId, "Opening the published address…");
        onOpenAppTab(publishedUrl);   // may be blocked; the link below is the reliable path
      } else if (pattern) {
        // Two minutes and no address. Say so rather than opening one tab and
        // leaving you to guess what happened to the other.
        setFor(
          projectId,
          `No address printed yet — check the terminal below. ${label} again once it appears.`
        );
        window.setTimeout(() => setFor(projectId, null), 12000);
        if (!project.web_url) return;
      }

      if (!project.web_url) return;
      const url = appUrl(getProject(projectId) ?? project);
      if (publishedUrl || !pattern) {
        setFor(projectId, "Waiting for the app to answer…");
      }
      await waitUntilAnswering(url);
      onOpenAppTab(url);
    } finally {
      // Never leave a button stuck: clear unless a message is deliberately
      // lingering (the no-address case clears itself on its own timer).
      setStatus((current) => {
        const message = current[projectId];
        if (message && message.startsWith("No address printed")) {
          return current;
        }
        const next = { ...current };
        delete next[projectId];
        return next;
      });
    }
  }

  return { altStatus: status, altUrl: urls, runAltStart };
}
