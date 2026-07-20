"""Eval harness for the CAD Assist Control Loop.

Each sprint ships a behavioral eval suite that drives the LangGraph workflow
end-to-end (with an isolated temp checkpoint DB) and scores capability-level
checks. Run with:

    python -m evals            # run all sprint suites
    python -m evals sprint4    # run one suite
    python -m evals --json     # machine-readable report
"""
