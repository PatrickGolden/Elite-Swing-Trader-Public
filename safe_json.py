# =============================================================================
# safe_json.py — Thread-safe, atomic JSON file I/O
# Eliminates race conditions when multiple threads read/write the same JSON files.
#
# Features:
#   - Per-path threading locks (no two threads write the same file simultaneously)
#   - Atomic writes (write to .tmp, then os.replace → no partial/corrupt files)
#   - Graceful fallback on read errors (returns default)
#   - JSONL append with automatic rotation (prevents unbounded growth)
# =============================================================================

import json
import os
import logging
import threading
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Test-write tripwire (2026-09-26). The integration suite runs the REAL refresh path, and
# for months every full run appended fixture rows ("SIDEWAYS", 1 coin) to the production
# refresh_snapshots.jsonl and overwrote data/coins_cache.json — found only because a new
# write-once file got blocked by test junk. Every refresh writer goes through this module,
# so this is the chokepoint: while pytest is executing a test, a write aimed at the live
# data/ directory or a root-level state file is REFUSED and recorded; tests/conftest.py
# fails the offending test. Writes to tmp_path (outside the project) are unaffected.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
_PROD_DATA_DIR = PROJECT_ROOT / "data"
_STATE_SUFFIXES = (".json", ".jsonl")
TEST_WRITE_VIOLATIONS: list = []


class ProductionWriteUnderTest(RuntimeError):
    """A test tried to write live state. Redirect the path to tmp_path in the test."""


def is_production_path(path) -> bool:
    try:
        p = Path(path).resolve()
    except (OSError, RuntimeError):
        return False
    if p == _PROD_DATA_DIR or _PROD_DATA_DIR in p.parents:
        return True
    return p.parent == PROJECT_ROOT and p.suffix in _STATE_SUFFIXES


def check_test_write(path) -> None:
    """Raise if a pytest test is about to write production state. No-op outside pytest."""
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        return
    if is_production_path(path):
        TEST_WRITE_VIOLATIONS.append((os.environ.get("PYTEST_CURRENT_TEST", "?"), str(path)))
        raise ProductionWriteUnderTest(f"test attempted to write production file: {path}")


# Per-path locks for thread safety within a single process
_path_locks: dict = {}
_meta_lock = threading.Lock()


def _get_lock(path: str) -> threading.Lock:
    """Get or create a lock for a specific file path."""
    key = os.path.abspath(path)
    if key not in _path_locks:
        with _meta_lock:
            if key not in _path_locks:
                _path_locks[key] = threading.Lock()
    return _path_locks[key]


def load(path: str, default: Any = None) -> Any:
    """
    Thread-safe JSON load. Returns `default` if file missing or corrupt.

    Parameters:
        path: File path to read
        default: Value to return if file doesn't exist or is invalid (default: None)
    """
    lock = _get_lock(path)
    with lock:
        if not Path(path).exists():
            return default() if callable(default) else default
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError, IOError) as e:
            logger.warning("[safe_json] Failed to load %s: %s", path, e)
            return default() if callable(default) else default


_REPLACE_RETRIES = 3
_REPLACE_DELAY = 0.15  # seconds between retries


def save(path: str, data: Any, indent: int = 2, **kwargs) -> bool:
    """
    Thread-safe, atomic JSON save. Writes to temp file then replaces atomically.
    On Windows, os.replace can fail transiently (antivirus, concurrent readers),
    so we retry the replace a few times before giving up.

    Parameters:
        path: Destination file path
        data: JSON-serializable data
        indent: JSON indent (default 2)
        **kwargs: Extra args passed to json.dump (e.g. default=str)

    Returns:
        True on success, False on failure.
    """
    check_test_write(path)
    lock = _get_lock(path)
    with lock:
        try:
            dir_path = os.path.dirname(os.path.abspath(path))
            fd, tmp_path = tempfile.mkstemp(suffix='.tmp', dir=dir_path)
            try:
                with os.fdopen(fd, 'w') as f:
                    json.dump(data, f, indent=indent, **kwargs)
                last_err = None
                for attempt in range(_REPLACE_RETRIES):
                    try:
                        os.replace(tmp_path, path)
                        return True
                    except OSError as e:
                        last_err = e
                        if attempt < _REPLACE_RETRIES - 1:
                            time.sleep(_REPLACE_DELAY * (attempt + 1))
                if last_err:
                    raise last_err
                return True  # pragma: no cover
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except (OSError, IOError, TypeError, ValueError) as e:
            logger.warning("[safe_json] Failed to save %s: %s", path, e)
            return False


# ---------------------------------------------------------------------------
# JSONL helpers — thread-safe append with automatic rotation
# ---------------------------------------------------------------------------
_DEFAULT_MAX_LINES = 10_000
_ROTATION_CHECK_EVERY = 100
_append_counts: dict[str, int] = {}


def append_jsonl(path: str, entry: dict, max_lines: int = _DEFAULT_MAX_LINES, **kwargs) -> bool:
    """
    Thread-safe JSONL append. Rotates to `<path>.bak` when line count exceeds
    *max_lines*, keeping the most recent half.

    Parameters:
        path:      JSONL file path
        entry:     Dict to serialize as one JSON line
        max_lines: Trigger rotation when file exceeds this many lines (default 10 000)
        **kwargs:  Extra args passed to json.dumps (e.g. default=str)

    Returns:
        True on success, False on failure.
    """
    check_test_write(path)
    lock = _get_lock(path)
    with lock:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            line = json.dumps(entry, **kwargs) + "\n"
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
            # Avoid O(n) full-file counting on every append; check periodically.
            key = os.path.abspath(path)
            _append_counts[key] = _append_counts.get(key, 0) + 1
            if _append_counts[key] >= _ROTATION_CHECK_EVERY:
                _append_counts[key] = 0
                _maybe_rotate_jsonl(path, max_lines)
            return True
        except (OSError, IOError, TypeError, ValueError) as e:
            logger.warning("[safe_json] Failed to append JSONL %s: %s", path, e)
            return False


def _maybe_rotate_jsonl(path: str, max_lines: int) -> None:
    """If *path* exceeds *max_lines*, keep only the newest half and archive the rest."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) <= max_lines:
            return
        keep = max_lines // 2
        archived = lines[:-keep]
        kept = lines[-keep:]
        # Append archived portion to .bak
        bak_path = path + ".bak"
        with open(bak_path, "a", encoding="utf-8") as f:
            f.writelines(archived)
        # Overwrite original with kept lines (atomic via temp)
        dir_path = os.path.dirname(os.path.abspath(path))
        fd, tmp_path = tempfile.mkstemp(suffix='.tmp', dir=dir_path)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.writelines(kept)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        logger.info("[safe_json] Rotated %s: archived %d lines, kept %d", path, len(archived), len(kept))
    except Exception as e:
        logger.warning("[safe_json] JSONL rotation failed for %s: %s", path, e)
