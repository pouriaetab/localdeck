"""A one-shot task for trying localdeck: does a few steps, then exits.

The sidebar shows when a task like this last ran and how it ended, because a
script that finishes in seconds otherwise leaves no sign it ever started.
"""
from __future__ import annotations

import time

STEPS = ["read input", "validate rows", "compute summary", "write report"]


def main() -> None:
    for number, step in enumerate(STEPS, start=1):
        print(f"[{number}/{len(STEPS)}] {step}...", flush=True)
        time.sleep(0.8)
    print("Done. Exiting with status 0.", flush=True)


if __name__ == "__main__":
    main()
