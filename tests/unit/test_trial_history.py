# Owner request (2026-10-11): "Record these tests so we have history of it and can learn
# from it." The machine record (data/trials.jsonl) grows automatically; the human record
# (docs/TRIAL_HISTORY.md) only grows if someone writes it. This test makes forgetting
# impossible: every trial that has a result must have an entry in the history.

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TRIALS = PROJECT_ROOT / "data" / "trials.jsonl"
HISTORY = PROJECT_ROOT / "docs" / "TRIAL_HISTORY.md"


def _resulted_trials():
    """Ids for every row carrying a verdict: trial_id for registered trials, the first
    8 chars of config_hash for the legacy June rows (they predate trial ids)."""
    ids = set()
    for line in TRIALS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if not r.get("verdict"):
            continue
        ids.add(r.get("trial_id") or (r.get("config_hash") or "")[:8])
    return ids


def test_every_trial_with_a_result_is_in_the_history():
    history = HISTORY.read_text(encoding="utf-8")
    missing = sorted(i for i in _resulted_trials() if i and i not in history)
    assert not missing, (
        "trials with results but no entry in docs/TRIAL_HISTORY.md: "
        + ", ".join(missing) + " — add what was tested, the deciding numbers, and the lesson")
