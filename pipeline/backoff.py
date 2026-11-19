"""Spot blocked runs and slow down. One block score per source, so a block on one never pauses another."""
import json
import re

MAX_PAUSE_MIN = 240
STOP_SCORE = 4
STOP_MESSAGE = "Google is blocking this IP. Stopped to protect it."
UNUSUAL = re.compile(r"unusual traffic", re.I)


def search_jobs(log_text):
    """(finished, failed) Maps search jobs in a gosom log; place pages and email jobs don't count."""
    finished = failed = 0
    for line in log_text.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict) and e.get("message") == "job finished" and "/maps/search" in str(e.get("job")):
            finished += 1
            failed += e.get("status") != "success"
    return finished, failed


def classify_run(code, stopped, places, inputs, log_text):
    """'ok' | 'blocked' | 'error'. Stopping by hand is never a block."""
    finished, failed = search_jobs(log_text)
    honest_empty = finished >= inputs and not failed  # every search ran clean; Google just had nothing there
    if not stopped and (
            (inputs > 0 and places == 0 and not honest_empty)
            or failed * 2 > finished
            or UNUSUAL.search(log_text)
            or ("infoDiv0" in log_text and "google.com/policies/terms" in log_text)):  # Google's block page
        return "blocked"
    return "error" if code != 0 and not stopped else "ok"


def after_run(block, kind, base_pause_min):
    """Next block state: the pause doubles per block (capped), resets on a clean run; stop at score 4."""
    score, pause = block.get("score", 0), block.get("pause_min", base_pause_min)
    if kind == "blocked":
        score, pause = score + 1, min(max(pause, base_pause_min) * 2, MAX_PAUSE_MIN)
    else:
        score, pause = (max(0, score - 0.5) if kind == "ok" else score), base_pause_min
    return {"score": score, "pause_min": pause, "stopped": score >= STOP_SCORE, "kind": kind}
