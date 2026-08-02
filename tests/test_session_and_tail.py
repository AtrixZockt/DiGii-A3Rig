from __future__ import annotations

import os
import time
from pathlib import Path

import psutil

from a3rig.processes import Session, TrackedProcess
from a3rig.tail import find_new_rpt, follow


def test_session_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    session = Session(project="E:/GitHub/MyMod")
    session.add(TrackedProcess("client2", 4242, "arma3_x64.exe", 1700000000.0))
    session.add(TrackedProcess("server", 4243, "arma3server_x64.exe", 1700000001.0))
    session.save(path)

    loaded = Session.load(path)
    assert loaded.project == "E:/GitHub/MyMod"
    assert [(p.role, p.pid) for p in loaded.processes] == [("client2", 4242), ("server", 4243)]


def test_session_add_replaces_a_duplicate_pid(tmp_path: Path) -> None:
    session = Session()
    session.add(TrackedProcess("client1", 1, "a.exe", 1.0))
    session.add(TrackedProcess("client2", 1, "b.exe", 2.0))
    assert len(session.processes) == 1
    assert session.processes[0].role == "client2"


def test_missing_or_corrupt_session_loads_empty(tmp_path: Path) -> None:
    assert Session.load(tmp_path / "absent.json").processes == []
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert Session.load(broken).processes == []


def test_live_process_matches_only_the_recorded_start_time() -> None:
    me = psutil.Process(os.getpid())
    exact = TrackedProcess("self", me.pid, "python.exe", me.create_time())
    assert exact.live_process() is not None

    # A PID recycled by a different process must not be treated as ours.
    recycled = TrackedProcess("self", me.pid, "python.exe", me.create_time() - 3600)
    assert recycled.live_process() is None


def test_live_process_for_a_dead_pid_is_none() -> None:
    assert TrackedProcess("gone", 999999, "arma3_x64.exe", 1.0).live_process() is None


def test_find_new_rpt_picks_the_newest_file_created_after_the_server_started(tmp_path: Path) -> None:
    old = tmp_path / "old.rpt"
    old.write_text("stale", encoding="utf-8")
    os.utime(old, (time.time() - 3600, time.time() - 3600))

    since = time.time()
    nested = tmp_path / "Users" / "Server"
    nested.mkdir(parents=True)
    fresh = nested / "arma3server_2026-08-01.rpt"
    fresh.write_text("fresh", encoding="utf-8")

    assert find_new_rpt(tmp_path, since, timeout=1.0) == fresh


def test_find_new_rpt_times_out_when_nothing_appears(tmp_path: Path) -> None:
    assert find_new_rpt(tmp_path, time.time(), timeout=0.5, poll_interval=0.1) is None


def test_follow_yields_lines_as_they_are_written(tmp_path: Path) -> None:
    path = tmp_path / "server.rpt"
    path.write_text("first\nsecond\n", encoding="utf-8")

    lines: list[str] = []
    stop = {"now": False}
    for line in follow(path, should_stop=lambda: stop["now"], poll_interval=0.01):
        lines.append(line)
        if len(lines) == 2:
            path.write_text("first\nsecond\nthird\n", encoding="utf-8")
        if len(lines) == 3:
            stop["now"] = True
    assert lines == ["first", "second", "third"]


def test_follow_restarts_when_the_file_is_truncated(tmp_path: Path) -> None:
    path = tmp_path / "server.rpt"
    path.write_text("aaaa\nbbbb\n", encoding="utf-8")

    lines: list[str] = []
    stop = {"now": False}
    for line in follow(path, should_stop=lambda: stop["now"], poll_interval=0.01):
        lines.append(line)
        if len(lines) == 2:
            path.write_text("c\n", encoding="utf-8")  # rotated: now shorter
        if len(lines) == 3:
            stop["now"] = True
    assert lines == ["aaaa", "bbbb", "c"]
