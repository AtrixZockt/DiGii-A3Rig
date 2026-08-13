"""Finding, classifying and tailing the dedicated server's ``.rpt`` log.

A modded Arma log is overwhelmingly noise - one real 3.4 MB server log here contains 7,010
copies of a single warning - while the reason to watch it at all is to catch script errors.
So lines are classified, and the tail can be narrowed to a severity or a regex.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Callable, Iterator

#: Filesystem timestamps can lag slightly behind the moment we spawned the server.
_MTIME_SKEW = 5.0

ERROR, WARNING, INFO = "error", "warning", "info"

#: Severities a `--tail-level` may name, each mapping to the classes it lets through.
LEVELS: dict[str, frozenset[str]] = {
    "all": frozenset({ERROR, WARNING, INFO}),
    "warnings": frozenset({ERROR, WARNING}),
    "errors": frozenset({ERROR}),
}

#: Arma prefixes every line with `H:MM:SS `, which has to go before anchoring anything.
_TIMESTAMP_RE = re.compile(r"^\s*\d{1,2}:\d{2}:\d{2}(\.\d+)?\s*")

# Patterns below are taken from real server and client logs, not from documentation.
# Script errors arrive as three-line blocks - `Error in expression <...>`, `Error
# position: <...>`, `Error Undefined variable in expression: ...` - and because every line
# of the block starts with `Error`, classifying line by line keeps the block together.
_ERROR_RE = re.compile(
    r"""^(?:
          Error\b                     # Error in expression / Error position / Error: ...
        | Cannot\s+(?:evaluate|load|open|create|delete)\b
        | SCRIPT\s+ERROR\b
        )""",
    re.IGNORECASE | re.VERBOSE,
)
_WARNING_RE = re.compile(r"^Warning\b", re.IGNORECASE)


def classify(line: str) -> str:
    """Return ``ERROR``, ``WARNING`` or ``INFO`` for one raw ``.rpt`` line."""
    body = _TIMESTAMP_RE.sub("", line)
    if _ERROR_RE.search(body):
        return ERROR
    if _WARNING_RE.search(body):
        return WARNING
    return INFO


def make_line_filter(
    level: str = "all", pattern: str | None = None
) -> Callable[[str], bool]:
    """Build a predicate combining a severity level with an optional regex.

    Both must pass, so ``--tail-level errors --tail-filter ace`` shows only ACE errors.
    An unknown level is treated as ``all`` rather than silently hiding everything.
    """
    allowed = LEVELS.get(level, LEVELS["all"])
    regex = re.compile(pattern, re.IGNORECASE) if pattern else None

    def accepts(line: str) -> bool:
        if classify(line) not in allowed:
            return False
        return regex.search(line) is not None if regex else True

    return accepts


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
