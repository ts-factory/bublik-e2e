"""Shared constants describing fixture results, statuses, and conclusions."""

from __future__ import annotations

RESULT_TYPES = {
    "passed": "PASSED",
    "failed": "FAILED",
    "killed": "KILLED",
    "cored": "CORED",
    "skipped": "SKIPPED",
    "faked": "FAKED",
    "incomplete": "INCOMPLETE",
}
RESULT_PROPERTIES = {"expected", "unexpected", "notRun"}
RESULT_PROPERTY_ALIASES = {"not_run": "notRun"}
ABNORMAL_STATUSES = {"KILLED", "CORED", "FAKED", "INCOMPLETE"}
MATRIX_KEYS = (
    "expectedPassed",
    "unexpectedPassed",
    "expectedFailed",
    "unexpectedFailed",
    "expectedSkipped",
    "unexpectedSkipped",
    "expectedKilled",
    "unexpectedKilled",
    "expectedCored",
    "unexpectedCored",
    "expectedFaked",
    "unexpectedFaked",
    "expectedIncomplete",
    "unexpectedIncomplete",
    "abnormal",
)
RUN_STATUS_BY_CONCLUSION = {
    "ok": "DONE",
    "nok-warning": "DONE",
    "nok-error": "DONE",
    "warning": "WARNING",
    "error": "ERROR",
    "running": "RUNNING",
    "busy": "BUSY",
    "stopped": "STOPPED",
    "interrupted": "INTERRUPTED",
    "compromised": "DONE",
}
EXPECTED_CONCLUSION = {
    "ok": "run-ok",
    "nok-warning": "run-warning",
    "nok-error": "run-error",
    "warning": "run-warning",
    "error": "run-error",
    "running": "run-running",
    "busy": "run-busy",
    "stopped": "run-stopped",
    "interrupted": "run-interrupted",
    "compromised": "run-compromised",
}
NOK_BORDERS = (20, 80)

# File Bublik fetches at the run URL to decide a run is complete (RUN_COMPLETE_FILE,
# schema default in bublik per_conf.json). Its presence makes the importer store
# run.finish; its absence leaves Finish/Duration empty.
RUN_COMPLETE_FILE = ".done"

# Conclusions whose run is still in progress, so they get neither a finish timestamp
# nor the complete-marker. Every other conclusion is treated as finished.
UNFINISHED_CONCLUSIONS = {"running", "busy"}
