from __future__ import annotations

import os
import time
from pathlib import Path

import psutil
import pytest

from a3rig.processes import Session, TrackedProcess
from a3rig.tail import (
    ERROR,
    INFO,
    WARNING,
    classify,
    find_new_rpt,
    follow,
    make_line_filter,
)

# --------------------------------------------------------------------------------------
# Session
# --------------------------------------------------------------------------------------


def live() -> TrackedProcess:
    """A tracked entry pointing at this very process, so it verifies as alive."""
    me = psutil.Process(os.getpid())
    return TrackedProcess("client1", me.pid, "python.exe", me.create_time(), project="P/live")


def dead(pid: int = 999999) -> TrackedProcess:
    return TrackedProcess("client2", pid, "arma3_x64.exe", 1.0, project="P/dead")


def test_session_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    session = Session(project="E:/GitHub/MyMod")
    session.add(TrackedProcess("client2", 4242, "arma3_x64.exe", 1700000000.0))
    session.add(TrackedProcess("server", 4243, "arma3server_x64.exe", 1700000001.0))
    session.save(path)

    loaded = Session.load(path)
    assert [(p.role, p.pid) for p in loaded.processes] == [("client2", 4242), ("server", 4243)]
    # The project is stamped onto each process, so entries stay attributable once the file
    # holds runs from more than one project.
    assert {p.project for p in loaded.processes} == {"E:/GitHub/MyMod"}


def test_add_stamps_the_current_project_without_overwriting_an_explicit_one(tmp_path: Path) -> None:
    session = Session(project="E:/GitHub/A")
    session.add(TrackedProcess("client1", 1, "a.exe", 1.0))
    session.add(TrackedProcess("client2", 2, "b.exe", 2.0, project="E:/GitHub/B"))
    assert [p.project for p in session.processes] == ["E:/GitHub/A", "E:/GitHub/B"]


def test_session_add_replaces_a_duplicate_pid() -> None:
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


def test_saving_mid_run_is_readable_immediately(tmp_path: Path) -> None:
    """Each spawn is persisted as it happens, so an interrupted run is still stoppable."""
    path = tmp_path / "session.json"
    session = Session(project="E:/GitHub/MyMod")

    session.add(live())
    session.save(path)
    assert len(Session.load(path).processes) == 1

    session.add(TrackedProcess("client2", 4242, "arma3_x64.exe", 1700000000.0))
    session.save(path)
    assert len(Session.load(path).processes) == 2


def test_a_second_project_does_not_orphan_the_first(tmp_path: Path) -> None:
    """The whole point of merging: two rigs at once must both stay stoppable."""
    path = tmp_path / "session.json"

    first = Session(project="E:/GitHub/A")
    first.add(live())
    first.save(path)

    second = Session(project="E:/GitHub/B")
    second.add(TrackedProcess("client1", 4242, "arma3_x64.exe", 1700000000.0))
    second.save(path)

    loaded = Session.load(path)
    assert {p.project for p in loaded.processes} == {"P/live", "E:/GitHub/B"}
    assert len(loaded.processes) == 2


def test_dead_entries_from_earlier_runs_are_pruned(tmp_path: Path) -> None:
    path = tmp_path / "session.json"

    stale = Session(project="E:/GitHub/Old")
    stale.add(dead())
    stale.save(path)
    assert len(Session.load(path).processes) == 1  # written as-is; we just created it

    fresh = Session(project="E:/GitHub/New")
    fresh.add(TrackedProcess("client1", 4242, "arma3_x64.exe", 1700000000.0))
    fresh.save(path)

    loaded = Session.load(path)
    assert [p.pid for p in loaded.processes] == [4242]


def test_by_project_groups_entries(tmp_path: Path) -> None:
    session = Session()
    session.add(TrackedProcess("client1", 1, "a.exe", 1.0, project="A"))
    session.add(TrackedProcess("server", 2, "b.exe", 2.0, project="A"))
    session.add(TrackedProcess("client1", 3, "c.exe", 3.0, project="B"))
    grouped = session.by_project()
    assert sorted(grouped) == ["A", "B"]
    assert [p.pid for p in grouped["A"]] == [1, 2]


def test_entries_without_a_project_are_labelled(tmp_path: Path) -> None:
    session = Session()
    session.add(TrackedProcess("client1", 1, "a.exe", 1.0))
    assert "unknown project" in session.by_project()


def test_live_process_matches_only_the_recorded_start_time() -> None:
    me = psutil.Process(os.getpid())
    exact = TrackedProcess("self", me.pid, "python.exe", me.create_time())
    assert exact.live_process() is not None

    # A PID recycled by a different process must not be treated as ours.
    recycled = TrackedProcess("self", me.pid, "python.exe", me.create_time() - 3600)
    assert recycled.live_process() is None


def test_live_process_for_a_dead_pid_is_none() -> None:
    assert dead().live_process() is None


# --------------------------------------------------------------------------------------
# .rpt classification
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line,expected",
    [
        # Script errors arrive as a three-line block; each line must classify as an error
        # on its own, or the block gets split by filtering.
        (" 1:29:10 Error in expression <IR_currentVanillaVisionMode\",0]);", ERROR),
        (" 1:29:10   Error position: <_curVisVeh isEqualTo _storedVisVeh) && (>", ERROR),
        (" 1:29:10   Error Undefined variable in expression: _curvisveh", ERROR),
        # Engine errors.
        (" 1:28:05 Error: Object(4 : 12) not found", ERROR),
        (" 1:28:05 Error: Bone m1 doesn't exist in skeleton OFP2_ManSkeleton", ERROR),
        (" 1:28:04 Cannot evaluate '' - no file", ERROR),
        # Warnings - the overwhelming majority of a modded log.
        (" 1:28:01 Warning Message: '/' is not a value", WARNING),
        (" 1:28:02 Warning: x\\kat\\addons\\chemical\\models\\kat_mask_m1.p3d:1", WARNING),
        # Everything else.
        (" 1:27:53 Connected to Steam servers", INFO),
        (" 1:28:03 Server: Object 4:12 not found (message Type_91)", INFO),
        ("Version: 2.20.152984", INFO),
        ("", INFO),
    ],
)
def test_classify(line: str, expected: str) -> None:
    assert classify(line) == expected


def test_classification_ignores_the_timestamp_prefix() -> None:
    """Arma prefixes every line with H:MM:SS, which anchored patterns must skip."""
    assert classify("11:59:59 Error: boom") == ERROR
    assert classify("Error: boom") == ERROR


def test_errors_level_keeps_whole_script_error_blocks(fixtures: Path) -> None:
    lines = (fixtures / "server_sample.rpt").read_text(encoding="utf-8").splitlines()
    accepts = make_line_filter("errors")
    kept = [line for line in lines if accepts(line)]

    block = [line for line in kept if "Error in expression" in line or "Error position" in line or "Error Undefined" in line]
    assert len(block) == 3, "all three lines of the script error must survive"
    assert not any("Warning" in line for line in kept)


def test_levels_are_progressively_narrower(fixtures: Path) -> None:
    lines = (fixtures / "server_sample.rpt").read_text(encoding="utf-8").splitlines()
    counts = {
        level: sum(1 for line in lines if make_line_filter(level)(line))
        for level in ("all", "warnings", "errors")
    }
    assert counts["all"] > counts["warnings"] > counts["errors"] > 0
    assert counts["all"] == len(lines)


def test_filter_regex_narrows_within_a_level(fixtures: Path) -> None:
    lines = (fixtures / "server_sample.rpt").read_text(encoding="utf-8").splitlines()
    accepts = make_line_filter("errors", "bone")
    kept = [line for line in lines if accepts(line)]
    assert len(kept) == 1
    assert "Bone m1" in kept[0]


def test_filter_applies_on_top_of_level_not_instead_of_it(fixtures: Path) -> None:
    lines = (fixtures / "server_sample.rpt").read_text(encoding="utf-8").splitlines()
    # 'not found' appears in an error, a warning and an info line.
    assert sum(1 for line in lines if make_line_filter("all", "not found")(line)) == 3
    assert sum(1 for line in lines if make_line_filter("errors", "not found")(line)) == 1


def test_unknown_level_falls_back_to_all_rather_than_hiding_everything() -> None:
    assert make_line_filter("nonsense")(" 1:00:00 anything")


# --------------------------------------------------------------------------------------
# Following the file
# --------------------------------------------------------------------------------------


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
