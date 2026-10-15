# execution_config_file_lock.py — cross-process exclusive lock for execution_config.json
#
# threading.Lock in app.py only synchronizes within one process. Balance sync and the dashboard
# can run under multiple workers or accidental double starts; this lock coordinates RMW on disk.
#
# Deployment: prefer a single app process (see app.py port 5099 guard for local dev). If you use
# Gunicorn/Uvicorn, use workers=1 for this app, or rely on this lock for execution_config writes.

from __future__ import annotations

import os
import sys


def execution_config_cross_lock(execution_config_json_path: str):
    """Return a context manager that holds an exclusive lock while mutating execution_config.json."""
    lock_path = execution_config_json_path + ".lock"
    return _ExecutionConfigFileLock(lock_path)


class _ExecutionConfigFileLock:
    def __init__(self, lock_path: str):
        self.lock_path = lock_path
        self._fh = None

    def __enter__(self):
        d = os.path.dirname(os.path.abspath(self.lock_path))
        if d:
            os.makedirs(d, exist_ok=True)
        self._fh = open(self.lock_path, "a+b")
        if sys.platform == "win32":
            import msvcrt

            self._fh.seek(0, os.SEEK_END)
            if self._fh.tell() == 0:
                self._fh.write(b"\0")
                self._fh.flush()
            self._fh.seek(0)
            msvcrt.locking(self._fh.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self._fh is not None:
                if sys.platform == "win32":
                    import msvcrt

                    self._fh.seek(0)
                    try:
                        msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
                else:
                    import fcntl

                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            try:
                if self._fh is not None:
                    self._fh.close()
            finally:
                self._fh = None
        return False
