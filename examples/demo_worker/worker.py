"""A long-running worker for trying localdeck: prints a status line every second.

Colours are plain ANSI escapes, to show that the dashboard terminal renders them.
"""
from __future__ import annotations

import itertools
import random
import time

GREEN, YELLOW, DIM, RESET = "\033[32m", "\033[33m", "\033[2m", "\033[0m"


def main() -> None:
    print("Demo worker started. Stop it from the dashboard.", flush=True)
    queue = 0
    for tick in itertools.count(1):
        queue = max(0, queue + random.randint(-2, 3))
        colour = YELLOW if queue > 8 else GREEN
        print(f"{DIM}{time.strftime('%H:%M:%S')}{RESET} tick {tick:>4}  "
              f"queue {colour}{queue:>2}{RESET}", flush=True)
        time.sleep(1)


if __name__ == "__main__":
    main()
