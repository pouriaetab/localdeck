# Lessons: the failures that are NOT automatable

`backend/app/standards_rules.py` holds every rule a machine can check. This file holds
the ones it cannot. Read it when starting a project; add to it after every incident.

The split matters: roughly half of real bugs are domain-specific and cannot be checked
generically. Pretending otherwise turns a rule set into a ritual.

## Process

1. **Touch real data in the first hour.** The single highest-value rule here. On
   2026-09-07 four bugs in one project (colliding provider ids across sources, a
   population prior drawn from the wrong reference class, a design effect inflated by
   singleton clusters, a non-monotone test statistic) were all found by running against
   real data, and *none* would have been caught by a test written beforehand. Tests came
   after, to lock the fixes in. That order is the lesson.
2. **Verify a provider contract before building on its shape.** Assume field names,
   ids and availability are all wrong until a probe script proves otherwise. Write the
   parser to fail loudly on an unexpected shape rather than coerce it into something
   plausible.
3. **Ask what the estimator's reference class is.** A prior pooled over a population
   that does not match the target biases every downstream number. No test catches this;
   only asking does.
4. **A statistic computed on a degenerate design is not that statistic.** An ICC over
   mostly-singleton clusters measures nothing and reports a confident number anyway.
   Report the degeneracy diagnostic (singleton fraction, effective sample size) next to
   every estimate.
5. **Check monotonicity of anything with a knob.** If a parameter is supposed to make a
   test more conservative, assert that it does, across a range. Discretisation and
   rounding break monotonicity silently.
6. **A null result is a result.** Build the explicit NEGLIGIBLE / INSUFFICIENT_EVIDENCE
   verdict before the analysis, so the pipeline can conclude "nothing here" instead of
   manufacturing an effect.
7. **Distinguish "the tool cannot" from "the environment will not".** Blocked network,
   read-only mounts and missing wheels look like code bugs and are not.

## Environment

8. **Never share a build artefact between machines.** venvs and any compiled artefact
   are platform-specific. Suffix the path.
9. **A partial install is worse than none.** Write a success marker last; treat its
   absence as a failed install to rebuild.
10. **Prefer failing fast to falling back.** `pip` compiling from source, an ORM
    auto-creating a schema, a parser coercing a bad row: each turns a clear error into
    a silent delay or silent corruption.
11. **Give every entrypoint a `--doctor`.** When something will not start, guessing is
    expensive; a status dump is free.
12. **Shell dialect matters.** zsh aborts a whole command on an unmatched glob; bash
    does not. Quote globs in anything a person will paste.

## Working with an assistant

13. **The assistant's shell may not be your machine.** If it runs on Linux and you are
    on macOS, every platform-specific failure is invisible to it. Paste real terminal
    output rather than describing the symptom.
14. **Re-read the contract on long sessions.** Ask it to re-read `CLAUDE.md` and the
    design doc and report what has drifted. Quality degrades as recent messages crowd
    out early constraints.
15. **Make it diagnose before it patches.** "What is the root cause?" before "fix it".
    Fast patching is where the worst work comes from.

## Startup and tooling portability

16. **A startup check must never be able to fail because of somebody else's code.**
    Scan an explicit whitelist of your own source paths. A whole-tree scan with
    exclusions depends on the grep implementation: GNU grep expands
    `--exclude-dir='.venv*'`, BSD grep on macOS does not, so the identical check passes
    on Linux and kills the app on a Mac.
17. **Never exit non-zero silently.** Add an `ERR` trap naming the line and exit code.
    To a supervisor showing a spinner, a silent exit and a hang look the same.
18. **The launcher's PATH is not your shell's PATH.** GUI- and launchd-spawned
    processes get a minimal PATH; homebrew, uv, nvm and pnpm are invisible. Repair it
    at the top of the entrypoint.
19. **macOS TCC blocks ~/Desktop, ~/Documents and ~/Downloads** for GUI-spawned
    processes and reports it as "Operation not permitted" on a file you can read fine
    in Terminal. Detect it and explain it in words.
20. **A retry that changes nothing is not a retry.** If an install fails, the second
    attempt must change something material: a different package manager, a different
    resolver input. Re-running the identical command and re-checking is a loop, not a
    fallback.
21. **Never trust a package manager's exit code; verify the artefact.** pnpm exits
    non-zero on harmless advisories and exits zero while silently skipping the install
    script that fetches a native binary. The only honest test is loading the thing you
    need: `node -e "require('rollup');require('esbuild')"`.
22. **pnpm 10+ blocks package build scripts by default.** Anything with a native binary
    (esbuild, sharp, better-sqlite3) needs
    `"pnpm": {"onlyBuiltDependencies": [...]}` in package.json, or it installs
    successfully and does not work.
23. **`[[ -d venv ]]` does not mean the venv works.** After the macOS 27 upgrade removed
    Rosetta, every venv built on an Intel-only miniconda interpreter died with
    `zsh:1: bad CPU type in executable`, and every run.sh that guards its setup with
    "does the folder exist" skipped the rebuild and ran a dead interpreter anyway
    (three projects here did exactly this). Guard on whether the interpreter
    RUNS and can import what you need, never on whether a directory is present. This is
    rule 21 again, in a different costume.
24. **A venv cannot be repaired by re-pointing it at a new interpreter.** The compiled
    packages inside it are built for the old one. Rebuild it, and suffix the path by
    platform (rule 8) so the broken one is still there to compare against.
25. **A scrape that can stop early must say whether it finished.** A daily capture job
    scrolls a page until it passes the target day, then computes from what it collected.
    It could also stop because the page went quiet or a scroll budget ran out, and it
    returned the partial set either way, having computed the "did I reach the boundary"
    flag and stored it in a debug dict without ever acting on it. A cold first run
    produced a confident, wrong number; a second run against a warm page was right.
    Completeness is a precondition, not a diagnostic: refuse to compute rather than
    publish a plausible wrong answer.
26. **A guard that only fires against a HEALTHY rival leaves the worst case unguarded.**
    One app refuses to start when another instance's backend answers `/health`.
    On 2026-09-17 the surviving instance was wedged: it held its port and answered
    nothing. So it was not a "live instance" to refuse for, and not a corpse the sweep
    could clear either: its supervisor relaunched it faster than the new run could
    bind. The new run then logged `address already in use`, six times, under the advice
    "fix the error shown above", when no error in that log was fixable. Whenever a
    check asks "is the rival alive?", write down what happens when the answer is
    "sort of", and make that branch say the true thing out loud.
27. **A test fixture must keep up with the rules it claims to satisfy.** localdeck's
    `test_conformant_project_passes` built a project and asserted it scored `pass`.
    Four checks were added to the standards afterwards, so the "conformant" project
    stopped being conformant and the test failed at HEAD, reading at a glance as if
    the standards were broken rather than the fixture. A fixture named for a property
    has to be regenerated whenever that property's definition moves.

## Security

28. **Loopback is not a security boundary for a browser-facing tool.** Binding to
    `127.0.0.1` keeps other machines out, but every web page open in your browser can
    still reach the port. A review of this dashboard found three holes, all now closed
    and pinned by `backend/tests/test_security.py`:
    * a request for `/..%2f..%2f<file>` escaped the built UI folder and served any file
      the account could read, because the path was joined but never contained;
    * no `Host` check, so a DNS-rebinding page could talk to the API as same-origin,
      register a project with any command and start it;
    * no `Origin` check, so any site could fire Start, Stop or Shutdown blindly.
    The fixes are small: resolve the path and require it to stay inside the build
    folder; refuse any `Host` that is not a loopback name; refuse state-changing
    requests and websocket handshakes whose `Origin` is not local. A tool that can run
    commands deserves a threat model even when it only listens on localhost.
