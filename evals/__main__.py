"""CLI entrypoint for the eval harness.

    python -m evals            # run all sprint suites
    python -m evals sprint4    # run a single suite
    python -m evals --json     # machine-readable output
"""

from __future__ import annotations

import sys

from .harness import run_and_report
from .sprint4 import SUITE as SPRINT4
from .sprint5 import SUITE as SPRINT5

SUITES = {"sprint4": SPRINT4, "sprint5": SPRINT5}


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    names = [a for a in argv if not a.startswith("-")]
    if names:
        selected = []
        for name in names:
            if name not in SUITES:
                print(f"Unknown suite: {name}. Options: {', '.join(SUITES)}")
                return 2
            selected.append(SUITES[name])
    else:
        selected = list(SUITES.values())
    ok = run_and_report(selected, as_json=as_json)
    if not as_json:
        print("\nOVERALL:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
