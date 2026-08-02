"""Mod resolution and `-mod=` argument building — the parity-critical logic.

Client 2 and the dedicated server must receive exactly the arguments HEMTT gave client 1,
so these tests pin HEMTT's ordering rules rather than something merely reasonable.
"""

from __future__ import annotations

from pathlib import Path

from a3rig.hemtt import build_mod_args, dedupe, mod_argument, resolve_dlc, resolve_mods


def make_workshop(tmp_path: Path, *ids: str) -> Path:
    root = tmp_path / "workshop" / "content" / "107410"
    for mod_id in ids:
        (root / mod_id).mkdir(parents=True)
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_resolves_workshop_ids_to_absolute_paths(tmp_path: Path) -> None:
    workshop = make_workshop(tmp_path, "450814997", "463939057")
    mods = resolve_mods(["450814997", "463939057"], workshop, {})
    assert [m.path for m in mods] == [workshop / "450814997", workshop / "463939057"]
    assert all(m.ok for m in mods)


def test_unresolved_id_is_reported_with_its_comment(tmp_path: Path) -> None:
    workshop = make_workshop(tmp_path, "450814997")
    mods = resolve_mods(
        ["450814997", "3372876344"], workshop, {}, comments={"3372876344": "Ace View"}
    )
    missing = mods[1]
    assert not missing.ok
    assert missing.path is None
    assert missing.describe() == "3372876344  # Ace View"
    assert "not subscribed" in (missing.problem or "")


def test_project_own_published_id_is_skipped(tmp_path: Path) -> None:
    workshop = make_workshop(tmp_path, "450814997", "3372876344")
    mods = resolve_mods(["450814997", "3372876344"], workshop, {}, published_id="3372876344")
    assert mods[1].source == "self"
    assert mods[1].path is None
    assert "meta.cpp" in (mods[1].problem or "")


def test_pointer_overrides_the_workshop_folder(tmp_path: Path) -> None:
    workshop = make_workshop(tmp_path, "463939057")
    local_ace = tmp_path / "Projects" / "ACE3"
    local_ace.mkdir(parents=True)
    mods = resolve_mods(["463939057"], workshop, {"463939057": local_ace})
    assert mods[0].path == local_ace
    assert mods[0].source == "pointer"


def test_pointer_with_missing_target_is_reported(tmp_path: Path) -> None:
    workshop = make_workshop(tmp_path)
    mods = resolve_mods(["463939057"], workshop, {"463939057": tmp_path / "gone"})
    assert not mods[0].ok
    assert "pointer target missing" in (mods[0].problem or "")


def test_pointer_prefix_syntax(tmp_path: Path) -> None:
    workshop = make_workshop(tmp_path)
    unit = tmp_path / "Swifty" / "MyUnit"
    (unit / "@my_unit_gear").mkdir(parents=True)
    mods = resolve_mods(["my_unit:@my_unit_gear"], workshop, {"my_unit": unit})
    assert mods[0].path == unit / "@my_unit_gear"
    assert mods[0].source == "pointer-prefix"


def test_unknown_pointer_prefix_is_reported(tmp_path: Path) -> None:
    workshop = make_workshop(tmp_path)
    mods = resolve_mods(["my_unit:@gear"], workshop, {})
    assert not mods[0].ok
    assert "no pointer named 'my_unit'" in (mods[0].problem or "")


def test_single_letter_prefix_is_a_drive_not_a_pointer(tmp_path: Path) -> None:
    """HEMTT requires pointer names longer than one character so `D:\\...` still works."""
    workshop = make_workshop(tmp_path)
    local = tmp_path / "local_mod"
    local.mkdir()
    # An absolute path lands on the filesystem branch and joins as an absolute path.
    mods = resolve_mods([str(local)], workshop, {})
    assert mods[0].source == "workshop"
    assert mods[0].path == local


def test_mod_values_are_quoted_like_hemtt() -> None:
    """HEMTT wraps the value in literal quotes; workshop paths always contain spaces."""
    assert mod_argument(r"C:\Program Files (x86)\Steam\450814997") == (
        r'-mod="C:\Program Files (x86)\Steam\450814997"'
    )
    assert mod_argument(r"C:\mods\x", "-serverMod") == r'-serverMod="C:\mods\x"'


def test_mod_args_are_sorted_and_deduplicated() -> None:
    paths = [
        Path(r"C:\Steam\workshop\463939057"),
        Path(r"E:\GitHub\MyMod\.hemttout\dev"),
        Path(r"C:\Steam\workshop\450814997"),
        Path(r"C:\Steam\workshop\463939057"),
    ]
    args = build_mod_args([], paths)
    assert args == [
        r'-mod="C:\Steam\workshop\450814997"',
        r'-mod="C:\Steam\workshop\463939057"',
        r'-mod="E:\GitHub\MyMod\.hemttout\dev"',
    ]


def test_dev_build_is_not_forced_first() -> None:
    """HEMTT sorts the dev build in with everything else; it is not pinned to the front."""
    args = build_mod_args(
        [], [Path(r"E:\GitHub\MyMod\.hemttout\dev"), Path(r"C:\Steam\workshop\450814997")]
    )
    assert args[-1] == r'-mod="E:\GitHub\MyMod\.hemttout\dev"'


def test_one_argument_per_mod_never_semicolon_joined() -> None:
    args = build_mod_args([], [Path("/a"), Path("/b")])
    assert len(args) == 2
    assert all(";" not in arg for arg in args)


def test_dlc_comes_first_in_enum_order() -> None:
    args = build_mod_args(["Western Sahara", "contact", "vn"], [Path(r"C:\mods\x")])
    assert args == ['-mod="contact"', '-mod="vn"', '-mod="ws"', r'-mod="C:\mods\x"']


def test_dlc_aliases_and_app_ids_resolve() -> None:
    assert resolve_dlc("S.O.G. Prairie Fire")[1] == "vn"
    assert resolve_dlc("1227700")[1] == "vn"
    assert resolve_dlc("SOG")[1] == "vn"
    assert resolve_dlc("Creator DLC: Global Mobilization")[1] == "gm"
    assert resolve_dlc("nonsense") is None


def test_dlc_is_deduplicated_across_aliases() -> None:
    args = build_mod_args(["vn", "S.O.G. Prairie Fire", "1227700"], [])
    assert args == ['-mod="vn"']


def test_dedupe_keeps_first_occurrence() -> None:
    assert dedupe(["-a", "-b", "-a", "-c", "-b"]) == ["-a", "-b", "-c"]
