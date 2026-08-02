"""Server config scanning.

Each of these settings fails silently at runtime — the server starts, and you only find
out when a client gets bounced — so they are pinned here.

`BattlEye` is eight letters. `battleEye` is a common typo that Arma's config parser
ignores outright, leaving BattlEye enabled, so it is treated as its own failure mode
rather than accepted as an alias.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a3rig.servercfg import (
    ALLOWED_FILE_PATCHING,
    BATTLEYE,
    KICK_DUPLICATE,
    LOOPBACK,
    patch_battleye,
    patch_server_config,
    scan_server_config,
)

GOOD = """\
hostname = "Dev";
maxPlayers = 32;
kickDuplicate = 0;
allowedFilePatching = 2;
loopback = 1;
BattlEye = 0;
"""

FASTER_DEFAULT = """\
hostname = "default";
maxPlayers = 32;
kickDuplicate = 0;
allowedFilePatching = 2;
loopback = 0;
BattlEye = 0;
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "server.cfg"
    path.write_text(text, encoding="utf-8")
    return path


# --- scanning ---------------------------------------------------------------------------


def test_a_fully_correct_config_has_no_findings(tmp_path: Path) -> None:
    scan = scan_server_config(write(tmp_path, GOOD))
    assert scan.broken == []
    assert scan.problem is None
    assert scan.file_patching_note is None


def test_loopback_zero_is_the_steamid_kick(tmp_path: Path) -> None:
    """The real-world case: FASTER's default config bounces the second client."""
    scan = scan_server_config(write(tmp_path, FASTER_DEFAULT))
    finding = scan.finding(LOOPBACK)
    assert not finding.ok
    assert finding.value == 0
    assert finding.line == 5
    assert "same SteamID already in game" in (finding.problem or "")
    # kickDuplicate is already 0 here, so it must NOT be reported - it is the wrong lever.
    assert scan.finding(KICK_DUPLICATE).ok
    assert [f.setting for f in scan.broken] == [LOOPBACK]


def test_missing_setting_is_reported_as_missing(tmp_path: Path) -> None:
    scan = scan_server_config(write(tmp_path, 'hostname = "Dev";\n'))
    for setting in (BATTLEYE, LOOPBACK, ALLOWED_FILE_PATCHING):
        finding = scan.finding(setting)
        assert finding.value is None
        assert f"no `{setting.canonical}` setting" in (finding.problem or "")
    # kickDuplicate's wanted value is 0, but absent is still not 0.
    assert not scan.finding(KICK_DUPLICATE).ok


def test_settings_are_case_insensitive_like_arma(tmp_path: Path) -> None:
    scan = scan_server_config(write(tmp_path, "LOOPBACK = 1;\nbattleye  =  0 ;\nKickDuplicate=0;\n"))
    assert scan.finding(LOOPBACK).ok
    assert scan.finding(BATTLEYE).ok
    assert scan.finding(KICK_DUPLICATE).ok


def test_commented_out_settings_are_ignored(tmp_path: Path) -> None:
    scan = scan_server_config(write(tmp_path, "// loopback = 1;\nloopback = 0;\n"))
    assert scan.finding(LOOPBACK).value == 0
    assert scan.finding(LOOPBACK).line == 2


def test_first_occurrence_wins(tmp_path: Path) -> None:
    scan = scan_server_config(write(tmp_path, "loopback = 1;\nloopback = 0;\n"))
    assert scan.finding(LOOPBACK).line == 1
    assert scan.finding(LOOPBACK).ok


def test_misspelled_battleye_is_reported_rather_than_accepted(tmp_path: Path) -> None:
    """`battleEye = 0;` looks right but Arma ignores it, so BattlEye stays on."""
    scan = scan_server_config(write(tmp_path, 'hostname = "Dev";\nbattleEye = 0;\n'))
    assert scan.battleye_value is None
    assert scan.typo_line == 2
    assert "does not recognise" in (scan.problem or "")
    assert "line 2" in scan.fix_hint


def test_correct_spelling_wins_over_a_stray_typo(tmp_path: Path) -> None:
    scan = scan_server_config(write(tmp_path, "battleEye = 1;\nBattlEye = 0;\n"))
    assert scan.battleye_disabled
    assert scan.problem is None


def test_file_patching_note(tmp_path: Path) -> None:
    scan = scan_server_config(write(tmp_path, "allowedFilePatching = 0;\n"))
    assert "blocks -filePatching" in (scan.file_patching_note or "")
    scan = scan_server_config(write(tmp_path, "BattlEye = 0;\n"))
    assert "no `allowedFilePatching`" in (scan.file_patching_note or "")


def test_unreadable_config_is_actionable(tmp_path: Path) -> None:
    from a3rig.errors import A3RigError

    with pytest.raises(A3RigError) as excinfo:
        scan_server_config(tmp_path / "absent.cfg")
    assert "server config could not be read" in excinfo.value.title


# --- patching ---------------------------------------------------------------------------


def test_patch_fixes_every_broken_setting_at_once(tmp_path: Path) -> None:
    path = write(tmp_path, "kickDuplicate = 1;\nallowedFilePatching = 0;\nloopback = 0;\nBattlEye = 1;\n")
    patch_server_config(path)
    scan = scan_server_config(path)
    assert scan.broken == []
    assert path.read_text(encoding="utf-8").splitlines() == [
        "kickDuplicate = 0;",
        "allowedFilePatching = 2;",
        "loopback = 1;",
        "BattlEye = 0;",
    ]


def test_patch_can_target_a_single_setting(tmp_path: Path) -> None:
    path = write(tmp_path, FASTER_DEFAULT)
    patch_server_config(path, [LOOPBACK])
    assert scan_server_config(path).finding(LOOPBACK).ok
    assert "maxPlayers = 32;" in path.read_text(encoding="utf-8")


def test_patch_appends_a_missing_setting(tmp_path: Path) -> None:
    path = write(tmp_path, 'hostname = "Dev";\n')
    patch_server_config(path, [LOOPBACK])
    assert path.read_text(encoding="utf-8").splitlines()[-1] == "loopback = 1;"


def test_patch_backs_the_file_up_first(tmp_path: Path) -> None:
    path = write(tmp_path, FASTER_DEFAULT)
    changes = patch_server_config(path)
    assert (tmp_path / "server.cfg.bak").read_text(encoding="utf-8") == FASTER_DEFAULT
    assert any("backup" in change for change in changes)


def test_patch_is_a_no_op_when_nothing_is_broken(tmp_path: Path) -> None:
    path = write(tmp_path, GOOD)
    assert patch_server_config(path) == []
    assert not (tmp_path / "server.cfg.bak").exists()


def test_patch_preserves_crlf_line_endings(tmp_path: Path) -> None:
    path = tmp_path / "server.cfg"
    path.write_bytes(b'hostname = "Dev";\r\n')
    patch_server_config(path, [BATTLEYE])
    assert path.read_bytes().endswith(b"BattlEye = 0;\r\n")


def test_patch_corrects_a_misspelled_battleye_in_place(tmp_path: Path) -> None:
    path = write(tmp_path, 'hostname = "Dev";\nbattleEye = 0;\n')
    message = patch_battleye(path)
    assert "misspelled" in message
    scan = scan_server_config(path)
    assert scan.battleye_disabled
    assert scan.typo_line is None
    assert path.read_text(encoding="utf-8").splitlines() == ['hostname = "Dev";', "BattlEye = 0;"]


def test_patch_battleye_only_touches_battleye(tmp_path: Path) -> None:
    path = write(tmp_path, FASTER_DEFAULT.replace("BattlEye = 0;", "BattlEye = 1;"))
    patch_battleye(path)
    scan = scan_server_config(path)
    assert scan.battleye_disabled
    assert not scan.finding(LOOPBACK).ok  # left alone


def test_patch_battleye_no_op_when_already_disabled(tmp_path: Path) -> None:
    path = write(tmp_path, GOOD)
    assert "already disabled" in patch_battleye(path)
