"""Self-check for run classification and the block score. Run: python pipeline/test_backoff.py"""
import backoff as b

# line shapes copied from a real gosom v1.19.0 log
FOUND = '{"level":"info","component":"scrapemate","jobid":"CA|real estate agency|Toronto","time":"2026-11-19T17:46:58Z","message":"20 places found"}'


def search_job(status):
    return ('{"level":"info","component":"scrapemate","job":"Job{ID: CA|real estate agency|Toronto, Method: GET, '
            'URL: https://www.google.com/maps/search/real+estate+agency+in+Toronto, UrlParams: map[hl:en]}",'
            f'"status":"{status}","duration":2812.45,"time":"2026-11-19T17:46:58Z","message":"job finished"}}')


PLACE_JOB = ('{"level":"info","component":"scrapemate","job":"Job{ID: 7b74, Method: GET, '
             'URL: https://www.google.com/maps/place/Wins+Lai/data=!4m7, UrlParams: map[hl:en]}",'
             '"status":"failed","message":"job finished"}')  # place-page failures are not search failures
ok_log = "\n".join([FOUND, search_job("success"), PLACE_JOB, PLACE_JOB])

assert b.classify_run(0, False, 40, 2, ok_log) == "ok"
assert b.classify_run(0, False, 0, 2, ok_log) == "blocked"                       # nothing from a real batch
assert b.classify_run(0, False, 40, 2, ok_log + "\nOur systems have detected Unusual Traffic") == "blocked"
assert b.classify_run(0, False, 40, 2, ok_log + '\n<div id="infoDiv0"><a href="https://www.google.com/policies/terms/">') == "blocked"
assert b.classify_run(0, False, 40, 3, "\n".join([search_job("failed"), search_job("error"), search_job("success")])) == "blocked"
assert b.classify_run(0, False, 40, 3, "\n".join([search_job("failed"), search_job("success"), search_job("success")])) == "ok"
assert b.classify_run(1, False, 40, 2, ok_log) == "error"
assert b.classify_run(130, True, 0, 2, "") == "ok"                               # Stop is not a block

# four blocks in a row: pause doubles to the 240-minute cap, then the loop stops itself
s = {}
pauses = []
for _ in range(4):
    s = b.after_run(s, "blocked", 30)
    pauses.append(s["pause_min"])
assert pauses == [60, 120, 240, 240] and s["stopped"] and s["score"] == 4

# one lucky run between blocks doesn't reset the danger
s = {}
for kind in ["blocked", "ok", "blocked", "blocked", "blocked"]:
    s = b.after_run(s, kind, 30)
assert s["score"] == 3.5 and not s["stopped"] and s["kind"] == "blocked"
assert b.after_run(s, "ok", 30)["pause_min"] == 30                              # clean run resets the pause
assert b.after_run(s, "blocked", 30)["stopped"]
assert b.after_run({"score": 1, "pause_min": 120}, "error", 30) == {"score": 1, "pause_min": 30, "stopped": False, "kind": "error"}
assert b.after_run({}, "ok", 30)["score"] == 0                                   # floor at 0
print("all checks passed")
