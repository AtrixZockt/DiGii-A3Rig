from __future__ import annotations

from pathlib import Path

from a3rig.paths import parse_library_folders, parse_vdf


def test_parse_vdf_nests_blocks(fixtures: Path) -> None:
    data = parse_vdf((fixtures / "libraryfolders.vdf").read_text(encoding="utf-8"))
    root = data["libraryfolders"]
    assert set(root) == {"0", "1", "2"}
    assert root["0"]["path"] == r"C:\Program Files (x86)\Steam"
    assert root["0"]["apps"]["107410"] == "168938712614"
    assert root["1"]["label"] == "games"


def test_parse_vdf_unescapes_backslashes() -> None:
    data = parse_vdf(r'"root" { "path" "D:\\Games\\Steam" }')
    assert data["root"]["path"] == r"D:\Games\Steam"


def test_parse_vdf_ignores_comments() -> None:
    data = parse_vdf('// leading comment\n"root"\n{\n\t"a" "1" // trailing\n}\n')
    assert data["root"] == {"a": "1"}


def test_library_folders_modern_schema(fixtures: Path) -> None:
    libraries = parse_library_folders((fixtures / "libraryfolders.vdf").read_text(encoding="utf-8"))
    assert [str(lib.path) for lib in libraries] == [
        r"C:\Program Files (x86)\Steam",
        r"D:\SteamLibrary",
        r"E:\SteamLibrary",
    ]
    assert "107410" in libraries[0].apps
    assert libraries[2].apps == {"233800"}


def test_library_folders_legacy_schema(fixtures: Path) -> None:
    libraries = parse_library_folders((fixtures / "libraryfolders_legacy.vdf").read_text(encoding="utf-8"))
    # Only the numeric keys are libraries; TimeNextStatsReport and ContentStatsID are not.
    assert [str(lib.path) for lib in libraries] == [r"D:\SteamLibrary", r"E:\Games\Steam"]
    assert libraries[0].apps == set()


def test_library_workshop_path(fixtures: Path) -> None:
    libraries = parse_library_folders((fixtures / "libraryfolders.vdf").read_text(encoding="utf-8"))
    assert libraries[0].workshop.as_posix().endswith("steamapps/workshop/content/107410")
