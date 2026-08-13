"""Process launching, discovery and the session state that makes `a3rig stop` safe."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import psutil

from .config import state_dir
from .errors import A3RigError
from .paths import CLIENT_EXE, SERVER_EXE

SESSION_FILE = "session.json"

#: Flags that keep a spawned game alive after this terminal closes.
if sys.platform == "win32":
    _DETACHED = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
else:  # pragma: no cover - Windows is the target platform
    _DETACHED = 0


def session_path() -> Path:
    return state_dir() / SESSION_FILE


@dataclass
class TrackedProcess:
    """A process a3rig started (or, for client 1, watched HEMTT start)."""

    role: str
    pid: int
    exe: str
    #: psutil's process start time. Guards against killing a recycled PID.
    create_time: float
    #: Project root this was started for, so `stop` can group across concurrent rigs.
    project: str = ""
    started_at: float = field(default_factory=time.time)

    def live_process(self) -> psutil.Process | None:
        """The running process, only if it is still the same one we recorded."""
        try:
            proc = psutil.Process(self.pid)
            if abs(proc.create_time() - self.create_time) > 1.0:
                return None
            return proc
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None


@dataclass
class Session:
    """Processes a3rig has started, persisted so `stop` can find them again.

    The file accumulates across runs *and* projects. Starting a rig in one project must
    not orphan a rig still running in another, so `save` merges with whatever is already
    on disk rather than replacing it, dropping only the entries whose process has since
    exited.
    """

    project: str = ""
    started_at: float = field(default_factory=time.time)
    processes: list[TrackedProcess] = field(default_factory=list)

    def add(self, process: TrackedProcess) -> None:
        """Record a process, stamping it with this run's project."""
        if not process.project:
            process.project = self.project
        self.processes = [p for p in self.processes if p.pid != process.pid]
        self.processes.append(process)

    def save(self, path: Path | None = None) -> Path:
        """Write the session, merging with any still-running entries already on disk.

        Safe to call repeatedly mid-run: each spawn should be persisted as it happens, so
        an interrupted run still leaves something `stop` can act on.
        """
        path = path or session_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        ours = {p.pid for p in self.processes}
        # Entries we did not just create are only kept if still alive - this is what
        # prunes previous runs without discarding a concurrent one.
        surviving = [
            p
            for p in Session.load(path).processes
            if p.pid not in ours and p.live_process() is not None
        ]
        payload = {
            "processes": [asdict(p) for p in surviving + self.processes],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path | None = None) -> "Session":
        path = path or session_path()
        if not path.is_file():
            return cls()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        processes = [
            TrackedProcess(
                role=str(item.get("role", "?")),
                pid=int(item.get("pid", 0)),
                exe=str(item.get("exe", "")),
                create_time=float(item.get("create_time", 0.0)),
                project=str(item.get("project", "")),
                started_at=float(item.get("started_at", 0.0)),
            )
            for item in payload.get("processes", [])
            if isinstance(item, dict)
        ]
        return cls(processes=processes)

    def by_project(self) -> dict[str, list[TrackedProcess]]:
        """Tracked processes grouped by the project that started them."""
        grouped: dict[str, list[TrackedProcess]] = {}
        for process in self.processes:
            grouped.setdefault(process.project or "unknown project", []).append(process)
        return grouped


# --------------------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------------------


def arma_pids(names: tuple[str, ...] = (CLIENT_EXE,)) -> dict[int, float]:
    """Currently running Arma processes, as ``{pid: create_time}``."""
    wanted = {name.lower() for name in names}
    found: dict[int, float] = {}
    for proc in psutil.process_iter(["name", "create_time"]):
        try:
            name = (proc.info["name"] or "").lower()
            if name in wanted:
                found[proc.pid] = proc.info["create_time"]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return found


def wait_for_hemtt(
    process: subprocess.Popen,
    before: dict[int, float],
    poll_interval: float = 0.5,
    timeout: float = 900.0,
) -> tuple[str, dict[int, float]]:
    """Wait until HEMTT exits or a new Arma client appears, whichever happens first.

    HEMTT 1.19 spawns Arma and returns without waiting for it, so in practice the exit
    fires first — but a future version that blocks would still work here.

    Returns the reason (``"exited"``, ``"client-appeared"`` or ``"timeout"``) and the new
    ``{pid: create_time}`` entries that appeared.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = arma_pids()
        new = {pid: t for pid, t in current.items() if pid not in before}
        if new:
            return "client-appeared", new
        if process.poll() is not None:
            time.sleep(poll_interval)  # let the freshly spawned client register
            current = arma_pids()
            new = {pid: t for pid, t in current.items() if pid not in before}
            return "exited", new
        time.sleep(poll_interval)
    return "timeout", {}


# --------------------------------------------------------------------------------------
# Launching
# --------------------------------------------------------------------------------------


def spawn_detached(argv: list[str], cwd: Path | None = None) -> TrackedProcess:
    """Start a game process detached, so closing this terminal does not kill it."""
    exe = argv[0]
    try:
        popen = subprocess.Popen(
            argv,
            cwd=str(cwd) if cwd else None,
            creationflags=_DETACHED,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise A3RigError("could not start process", f"{exe}: {exc}", "check the path and permissions") from exc

    try:
        create_time = psutil.Process(popen.pid).create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        create_time = time.time()
    return TrackedProcess(role="", pid=popen.pid, exe=exe, create_time=create_time)


def run_streaming(argv: list[str], cwd: Path) -> subprocess.Popen:
    """Start a subprocess that writes straight to this terminal.

    stdout/stderr are inherited rather than piped so HEMTT keeps its colours and
    progress bars — we detect completion from the process and from psutil, never by
    parsing its output.
    """
    try:
        return subprocess.Popen(argv, cwd=str(cwd))
    except OSError as exc:
        raise A3RigError("could not start hemtt", f"{argv[0]}: {exc}", "check [paths] hemtt") from exc


# --------------------------------------------------------------------------------------
# Stopping
# --------------------------------------------------------------------------------------


def terminate(proc: psutil.Process, timeout: float = 10.0) -> bool:
    """Ask a process to close, then kill it if it will not. True if it is gone."""
    try:
        proc.terminate()
        proc.wait(timeout=timeout)
        return True
    except psutil.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=5)
            return True
        except (psutil.NoSuchProcess, psutil.TimeoutExpired, psutil.AccessDenied):
            return False
    except psutil.NoSuchProcess:
        return True
    except psutil.AccessDenied:
        return False


def stop_session(session: Session) -> list[tuple[TrackedProcess, str]]:
    """Terminate every process recorded in the session.

    Each entry is re-verified by PID *and* start time first, so a PID that has since been
    recycled by an unrelated program is never touched.
    """
    results: list[tuple[TrackedProcess, str]] = []
    for tracked in session.processes:
        proc = tracked.live_process()
        if proc is None:
            results.append((tracked, "not running"))
            continue
        results.append((tracked, "stopped" if terminate(proc) else "could not stop"))
    return results


def find_all_arma_processes() -> list[psutil.Process]:
    """Every Arma client and server on the machine, whoever started them."""
    wanted = {CLIENT_EXE.lower(), SERVER_EXE.lower()}
    found: list[psutil.Process] = []
    for proc in psutil.process_iter(["name"]):
        try:
            if (proc.info["name"] or "").lower() in wanted:
                found.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return found


def clear_session(path: Path | None = None) -> None:
    path = path or session_path()
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass
