# logging_config.py — Centralized logging for Elite Swing Trader
# Call setup_logging() once at app startup (e.g. in app.py).

import logging
import sys


def setup_logging(
    level: int = logging.INFO,
    format_string: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    date_fmt: str = "%Y-%m-%d %H:%M:%S",
) -> None:
    """
    Configure root logger with a single stream handler.
    Safe to call multiple times; only the first call applies.
    """
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(level)

    # Windows consoles/redirected files default to cp1252, which raises
    # UnicodeEncodeError on the emoji/✓/📊 used throughout the app's log messages —
    # previously flooding stderr with thousands of logging tracebacks that buried
    # real errors. Reconfigure the stream to UTF-8 with a safe fallback so log
    # output never crashes on a non-Latin-1 character. (Python 3.7+.)
    stream = sys.stdout
    try:
        stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, ValueError):
        pass  # non-reconfigurable stream (e.g. already wrapped in tests) — handler still works

    handler = logging.StreamHandler(stream)
    handler.setLevel(level)
    formatter = logging.Formatter(format_string, datefmt=date_fmt)
    handler.setFormatter(formatter)
    root.addHandler(handler)
