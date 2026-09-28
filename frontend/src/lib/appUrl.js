/**
 * The URL localdeck should open for a project, carrying its build id.
 *
 * Why the build id is there: an app that serves a built bundle hands out an
 * index.html naming content-hashed files. If it serves that index.html without
 * `Cache-Control: no-store`, the browser is allowed to keep it — and since a
 * build that does not empty its output directory leaves the old bundles on disk,
 * the kept page goes on loading old code, answered 200, with nothing broken to
 * see. Restarting the app, localdeck, or the browser does not help, because
 * an HTTP disk cache outlives all three.
 *
 * `deckBuild` changes whenever the project is rebuilt, so the rebuilt app is a
 * different URL and no cached copy of the old page can be used for it. When a
 * project has no build on disk (a dev server compiles every request) there is
 * nothing to bust and the URL is left exactly as configured.
 */
export function appUrl(project, extraParams = null) {
  const configured = project?.web_url;
  if (!configured) {
    return "";
  }

  let url;
  try {
    url = new URL(configured);
  } catch (error) {
    return configured; // not a URL we can extend; never make it worse
  }

  const fingerprint = project?.build?.fingerprint;
  if (fingerprint) {
    url.searchParams.set("deckBuild", String(fingerprint));
  }
  if (extraParams) {
    for (const [key, value] of Object.entries(extraParams)) {
      url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

/**
 * What (if anything) localdeck should warn about for this project's build.
 *
 * Only ever for an app observed serving a build from disk. A dev server compiles
 * every request, so none of this can apply to one, and warning anyway would just
 * teach you to ignore the warnings.
 */
export function buildWarnings(project) {
  const build = project?.build;
  const preview = project?.preview;
  if (!build || preview?.serving !== "built") {
    return [];
  }

  const warnings = [];
  if (build.stale) {
    warnings.push(
      `Serving a stale build: ${build.index} is older than ${build.newer_source}. Rebuild this project's UI.`
    );
  }
  if (build.orphan_bundles > 0) {
    warnings.push(
      `${build.orphan_bundles} old bundle${build.orphan_bundles === 1 ? "" : "s"} left next to ${build.index}, so a cached page can still load old code without any error.`
    );
  }
  if (preview?.html_cacheable) {
    warnings.push(
      "This app serves its HTML without no-store, so a browser can keep showing an older build. localdeck opens it with a build id so this panel and its tabs stay current."
    );
  }
  return warnings;
}

/**
 * What (if anything) to say about this project's Python environment.
 *
 * Only when the project has NO environment that can run — a dead venv sitting
 * beside a working one is how these get repaired, and reporting it would be
 * noise. The backend decides that (see env_check.py); this only renders it.
 */
export function envWarnings(project) {
  const env = project?.env;
  if (!env || env.has_usable || !env.broken?.length) {
    return [];
  }
  return env.broken.map(
    (venv) => `${venv.path} cannot run: ${venv.reason}. Start will fail until it is rebuilt.`
  );
}
