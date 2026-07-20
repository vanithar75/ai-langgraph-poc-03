"""Minimal, dependency-free eval harness.

An eval *case* is a function returning a list of ``Check`` results. A *suite*
is a named list of cases. The runner executes every case, aggregates checks,
prints a readable report, and reports overall pass/fail so it can gate CI.
"""

from __future__ import annotations

import json
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class CaseResult:
    name: str
    checks: list[Check] = field(default_factory=list)
    error: str | None = None
    seconds: float = 0.0

    @property
    def passed(self) -> bool:
        return self.error is None and all(c.passed for c in self.checks)


EvalCase = Callable[[], list[Check]]


def expect(name: str, condition: bool, detail: str = "") -> Check:
    return Check(name=name, passed=bool(condition), detail=detail)


def expect_eq(name: str, actual: object, expected: object) -> Check:
    ok = actual == expected
    detail = "" if ok else f"expected {expected!r}, got {actual!r}"
    return Check(name=name, passed=ok, detail=detail)


@dataclass
class Suite:
    name: str
    cases: list[tuple[str, EvalCase]]


def run_suite(suite: Suite) -> list[CaseResult]:
    results: list[CaseResult] = []
    for case_name, case in suite.cases:
        started = time.perf_counter()
        try:
            checks = case()
            result = CaseResult(name=case_name, checks=checks)
        except Exception:
            result = CaseResult(name=case_name, error=traceback.format_exc())
        result.seconds = round(time.perf_counter() - started, 3)
        results.append(result)
    return results


def _summarize(results: list[CaseResult]) -> tuple[int, int, int, int]:
    cases_passed = sum(1 for r in results if r.passed)
    checks = [c for r in results for c in r.checks]
    checks_passed = sum(1 for c in checks if c.passed)
    return cases_passed, len(results), checks_passed, len(checks)


def print_report(suite_name: str, results: list[CaseResult]) -> bool:
    print(f"\n=== Eval suite: {suite_name} ===")
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"\n[{status}] {r.name}  ({r.seconds}s)")
        if r.error:
            print("  ! case raised an exception:")
            for line in r.error.strip().splitlines():
                print(f"    {line}")
            continue
        for c in r.checks:
            mark = "  \u2713" if c.passed else "  \u2717"
            extra = f" — {c.detail}" if c.detail and not c.passed else ""
            print(f"{mark} {c.name}{extra}")
    cp, ct, kp, kt = _summarize(results)
    print(f"\n--- {suite_name}: cases {cp}/{ct}, checks {kp}/{kt} ---")
    return cp == ct


def report_json(suite_name: str, results: list[CaseResult]) -> dict:
    cp, ct, kp, kt = _summarize(results)
    return {
        "suite": suite_name,
        "cases_passed": cp,
        "cases_total": ct,
        "checks_passed": kp,
        "checks_total": kt,
        "cases": [
            {
                "name": r.name,
                "passed": r.passed,
                "seconds": r.seconds,
                "error": r.error,
                "checks": [
                    {"name": c.name, "passed": c.passed, "detail": c.detail}
                    for c in r.checks
                ],
            }
            for r in results
        ],
    }


def run_and_report(suites: list[Suite], as_json: bool = False) -> bool:
    all_ok = True
    json_payload = []
    for suite in suites:
        results = run_suite(suite)
        if as_json:
            json_payload.append(report_json(suite.name, results))
            all_ok = all_ok and all(r.passed for r in results)
        else:
            ok = print_report(suite.name, results)
            all_ok = all_ok and ok
    if as_json:
        print(json.dumps(json_payload, indent=2))
    return all_ok
