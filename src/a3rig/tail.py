"""Finding and tailing the dedicated server's ``.rpt`` log."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Iterator

#: Filesystem timestamps can lag slightly behind the moment we spawned the server.
_MTIME_SKEW = 5.0


def find_new_rpt(
    profiles_dir: Path,
    since: float,
    timeout: float = 20.0,
    poll_interval: float = 0.5,
) -> Path | None:
    """Wait for the ``.rpt`` the server creates after ``since``, and return it.

    The search is recursive because Arma's layout under ``-profiles`` varies between the
    directory root and a ``Users/<name>`` subfolder depending on version and flags.
    """
    deadline = time.monotonic() + timeout
    while True:
        candidates: list[tuple[float, Path]] = []
        try:
            for path in profiles_dir.rglob("*.rpt"):
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if mtime >= since - _MTIME_SKEW:
                    candidates.append((mtime, path))
        except OSError:
            candidates = []
        if candidates:
            return max(candidates)[1]
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll_interval)


def follow(
    path: Path,
    should_stop: Callable[[], bool] | None = None,
    poll_interval: float = 0.25,
) -> Iterator[str]:
    """Yield lines from ``path`` as they are written, until ``should_stop`` returns True.

    Arma keeps the file open while writing, so reads are retried on transient sharing
    errors, and the offset resets if the file shrinks (rotated or recreated).
    """
    offset = 0
    buffer = ""
    while True:
        if should_stop is not None and should_stop():
            return
        try:
            size = path.stat().st_size
            if size < offset:  # rotated or truncated
                offset = 0
                buffer = ""
            if size > offset:
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    handle.seek(offset)
                    chunk = handle.read()
                    offset = handle.tell()
                buffer += chunk
                while "\n" in buffer:
                    line, _, buffer = buffer.partition("\n")
                    yield line.rstrip("\r")
        except (OSError, PermissionError):
            pass  # locked mid-write; try again next tick
        time.sleep(poll_interval)
