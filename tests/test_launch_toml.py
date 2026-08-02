from __future__ import annotations

from pathlib import Path

import pytest

from a3rig.errors import A3RigError
from a3rig.hemtt import (
    find_project_root,
    load_profile_tables,
    load_project,
    read_entry_comments,
    read_presets,
    resolve_extends,
    resolve_launch_profile,
)


def test_finds_project_root_by_walking_up(project_factory) -> None:
    root = project_factory(launch_toml="[default]\n")
    nested = root / "addons" / "main" / "functions"
    nested.mkdir(parents=True)
    assert find_project_root(nested) == root.resolve()


def test_missing_project_is_actionable(tmp_path: Path) -> None:
    with pytest.raises(A3RigError) as excinfo:
        find_project_root(tmp_path)
    assert "not inside a HEMTT project" in excinfo.value.title


def test_reads_launch_toml_top_level_tables(project_factory) -> None:
    root = project_factory(
        launch_toml="""
[default]
workshop = ["450814997", "463939057"]
parameters = ["-skipIntro", "-noPause"]
mission = "TestSave.Stratis"
"""
    )
    tables = load_profile_tables(load_project(root))
    profile = resolve_extends("default", tables)
    assert profile.workshop == ["450814997", "463939057"]
    assert profile.parameters == ["-skipIntro", "-noPause"]
    assert profile.mission == "TestSave.Stratis"


def test_falls_back_to_hemtt_launch_in_project_toml(project_factory) -> None:
    root = project_factory(
        project_toml="""
name = "Test Mod"
prefix = "test"
mainprefix = "z"

[hemtt.launch.default]
workshop = ["450814997"]
file_patching = false
"""
    )
    tables = load_profile_tables(load_project(root))
    profile = resolve_extends("default", tables)
    assert profile.workshop == ["450814997"]
    assert profile.uses_file_patching is False


def test_launch_toml_wins_over_project_toml(project_factory) -> None:
    root = project_factory(
        project_toml="""
name = "Test Mod"
prefix = "test"

[hemtt.launch.default]
workshop = ["111"]

[hemtt.launch.other]
workshop = ["999"]
""",
        launch_toml='[default]\nworkshop = ["222"]\n',
    )
    tables = load_profile_tables(load_project(root))
    assert resolve_extends("default", tables).workshop == ["222"]
    # Profiles only present in project.toml are still available.
    assert resolve_extends("other", tables).workshop == ["999"]


def test_extends_concatenates_arrays_and_overrides_scalars(project_factory) -> None:
    root = project_factory(
        launch_toml="""
[default]
workshop = ["450814997"]
parameters = ["-skipIntro"]
file_patching = true
executable = "arma3_x64"

[ace]
extends = "default"
workshop = ["463939057"]
parameters = ["-window"]
file_patching = false
"""
    )
    tables = load_profile_tables(load_project(root))
    profile = resolve_extends("ace", tables)
    assert profile.workshop == ["450814997", "463939057"]
    assert profile.parameters == ["-skipIntro", "-window"]
    assert profile.uses_file_patching is False
    assert profile.executable == "arma3_x64"  # inherited, not overridden


def test_extends_resolves_recursively(project_factory) -> None:
    root = project_factory(
        launch_toml="""
[base]
workshop = ["1"]

[middle]
extends = "base"
workshop = ["2"]

[leaf]
extends = "middle"
workshop = ["3"]
"""
    )
    tables = load_profile_tables(load_project(root))
    assert resolve_extends("leaf", tables).workshop == ["1", "2", "3"]


def test_extends_cycle_is_detected(project_factory) -> None:
    root = project_factory(
        launch_toml="""
[a]
extends = "b"

[b]
extends = "c"

[c]
extends = "a"
"""
    )
    tables = load_profile_tables(load_project(root))
    with pytest.raises(A3RigError) as excinfo:
        resolve_extends("a", tables)
    assert "cycle" in excinfo.value.title
    assert "a -> b -> c -> a" in (excinfo.value.detail or "")


def test_self_extends_cycle_is_detected(project_factory) -> None:
    root = project_factory(launch_toml='[a]\nextends = "a"\n')
    tables = load_profile_tables(load_project(root))
    with pytest.raises(A3RigError):
        resolve_extends("a", tables)


def test_unknown_profile_lists_the_alternatives(project_factory) -> None:
    root = project_factory(launch_toml="[default]\n[vn]\n")
    tables = load_profile_tables(load_project(root))
    with pytest.raises(A3RigError) as excinfo:
        resolve_extends("nope", tables)
    assert "default, vn" in (excinfo.value.detail or "")


def test_profile_chaining_overlays_left_to_right(project_factory) -> None:
    root = project_factory(
        launch_toml="""
[default]
workshop = ["450814997"]
parameters = ["-skipIntro"]

[ace]
workshop = ["463939057"]

[windowed]
parameters = ["-window"]
executable = "arma3"
"""
    )
    tables = load_profile_tables(load_project(root))
    profile = resolve_launch_profile(["default", "ace", "windowed"], tables)
    assert profile.workshop == ["450814997", "463939057"]
    assert profile.parameters == ["-skipIntro", "-window"]
    assert profile.client_executable == "arma3"


def test_cdlc_modifier_implies_default_profile(project_factory) -> None:
    root = project_factory(launch_toml='[default]\nworkshop = ["450814997"]\n')
    tables = load_profile_tables(load_project(root))
    profile = resolve_launch_profile(["+ws"], tables)
    assert profile.workshop == ["450814997"]  # default was used as the base
    assert profile.dlc == ["ws"]


def test_unknown_cdlc_is_rejected(project_factory) -> None:
    root = project_factory(launch_toml="[default]\n")
    tables = load_profile_tables(load_project(root))
    with pytest.raises(A3RigError) as excinfo:
        resolve_launch_profile(["+nope"], tables)
    assert "unknown CDLC" in excinfo.value.title


def test_global_profile_reference(project_factory) -> None:
    root = project_factory(launch_toml='[default]\nworkshop = ["450814997"]\n')
    tables = load_profile_tables(load_project(root))
    profile = resolve_launch_profile(
        ["default", "@adt"], tables, {"adt": {"workshop": ["3499977893"]}}
    )
    assert profile.workshop == ["450814997", "3499977893"]


def test_missing_global_profile_is_actionable(project_factory) -> None:
    root = project_factory(launch_toml="[default]\n")
    tables = load_profile_tables(load_project(root))
    with pytest.raises(A3RigError) as excinfo:
        resolve_launch_profile(["@nope"], tables, {})
    assert "global launch profile" in excinfo.value.title


def test_defaults_match_hemtt(project_factory) -> None:
    root = project_factory(launch_toml="[default]\n")
    tables = load_profile_tables(load_project(root))
    profile = resolve_extends("default", tables)
    assert profile.uses_file_patching is True
    assert profile.uses_rapify is True
    assert profile.uses_binarize is False
    assert profile.client_executable == "arma3_x64"


def test_non_list_array_field_is_rejected(project_factory) -> None:
    root = project_factory(launch_toml='[default]\nworkshop = "450814997"\n')
    tables = load_profile_tables(load_project(root))
    with pytest.raises(A3RigError) as excinfo:
        resolve_extends("default", tables)
    assert "must be a list" in excinfo.value.title


def test_entry_comments_are_recovered(project_factory) -> None:
    root = project_factory(
        launch_toml="""
[default]
workshop = [
    "450814997",    # CBA_A3
    "3372876344",   # Ace View
    "463939057",
]
"""
    )
    comments = read_entry_comments(root / ".hemtt" / "launch.toml")
    assert comments["450814997"] == "CBA_A3"
    assert comments["3372876344"] == "Ace View"
    assert "463939057" not in comments


def test_presets_expand_to_workshop_ids_and_dlc(project_factory, fixtures: Path) -> None:
    root = project_factory(launch_toml='[default]\npresets = ["main"]\n')
    presets_dir = root / ".hemtt" / "presets"
    presets_dir.mkdir(parents=True)
    (presets_dir / "main.html").write_text(
        (fixtures / "preset_main.html").read_text(encoding="utf-8"), encoding="utf-8"
    )
    project = load_project(root)
    profile = resolve_extends("default", load_profile_tables(project))
    ids, dlc, warnings = read_presets(project, profile)
    assert ids == ["450814997", "463939057"]  # deduplicated, order preserved
    assert dlc == ["1227700"]
    assert any("999999" in warning for warning in warnings)


def test_missing_preset_warns_rather_than_raising(project_factory) -> None:
    root = project_factory(launch_toml='[default]\npresets = ["absent"]\n')
    project = load_project(root)
    profile = resolve_extends("default", load_profile_tables(project))
    ids, dlc, warnings = read_presets(project, profile)
    assert (ids, dlc) == ([], [])
    assert any("absent" in warning for warning in warnings)
