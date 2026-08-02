from __future__ import annotations

from pathlib import Path

import pytest

from a3rig import config as config_mod
from a3rig.config import (
    DEFAULTS,
    Config,
    deep_merge,
    ensure_global_config,
    launch_config_names,
    load_config,
    resolve_server_paths,
)
from a3rig.errors import A3RigError


@pytest.fixture
def isolated_appdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the global config at a temp dir so tests never touch the real one."""
    appdata = tmp_path / "appdata"
    appdata.mkdir()
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(appdata))
    return appdata


def test_deep_merge_recurses_into_tables() -> None:
    base = {"a": {"x": 1, "y": 2}, "b": 1}
    result = deep_merge(base, {"a": {"y": 99}, "c": 3})
    assert result == {"a": {"x": 1, "y": 99}, "b": 1, "c": 3}


def test_deep_merge_replaces_lists_outright() -> None:
    result = deep_merge({"a": {"p": ["-one", "-two"]}}, {"a": {"p": ["-three"]}})
    assert result["a"]["p"] == ["-three"]


def test_deep_merge_does_not_mutate_the_base() -> None:
    base = {"a": {"x": 1}}
    deep_merge(base, {"a": {"x": 2}})
    assert base == {"a": {"x": 1}}


def test_global_config_is_created_with_commented_defaults(isolated_appdata: Path) -> None:
    path, created = ensure_global_config()
    assert created is True
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "# a3rig configuration" in text
    assert "BattlEye = 0" in text  # the comment that explains the [server] config key

    _, created_again = ensure_global_config()
    assert created_again is False


def test_defaults_apply_when_nothing_is_configured(isolated_appdata: Path) -> None:
    cfg = load_config(None)
    assert cfg.get("launch", "delay_after_hemtt") == 25
    assert cfg.get("client2", "profile") == "Dev2"
    assert cfg.get("server", "port") == 2302


def test_project_config_overrides_global(isolated_appdata: Path, project_factory) -> None:
    (isolated_appdata / "a3rig").mkdir(parents=True, exist_ok=True)
    (isolated_appdata / "a3rig" / "config.toml").write_text(
        '[launch]\ndelay_after_hemtt = 40\n[client2]\nprofile = "GlobalDev"\n', encoding="utf-8"
    )
    root = project_factory(launch_toml="[default]\n")
    (root / ".hemtt" / "a3rig.toml").write_text('[client2]\nprofile = "ProjectDev"\n', encoding="utf-8")

    cfg = load_config(root, create_global=False)
    assert cfg.get("client2", "profile") == "ProjectDev"  # project wins
    assert cfg.get("launch", "delay_after_hemtt") == 40  # global still applies
    assert cfg.get("server", "port") == 2302  # default still applies
    assert len(cfg.sources) == 2


def test_cli_flags_override_both() -> None:
    cfg = Config(DEFAULTS, []).apply_overrides({"launch": {"delay_after_hemtt": 1}})
    assert cfg.get("launch", "delay_after_hemtt") == 1


def test_unset_cli_flags_are_ignored() -> None:
    cfg = Config(DEFAULTS, []).apply_overrides({"launch": {"delay_after_hemtt": None}})
    assert cfg.get("launch", "delay_after_hemtt") == 25


def test_empty_path_values_read_as_none() -> None:
    cfg = Config(DEFAULTS, [])
    assert cfg.path("server", "config") is None
    assert cfg.path("paths", "arma3") is None
    configured = Config(deep_merge(DEFAULTS, {"server": {"config": "D:/a3/server.cfg"}}), [])
    assert configured.path("server", "config") == Path("D:/a3/server.cfg")


def server_config(tmp_path: Path, **overrides) -> Config:
    return Config(deep_merge(DEFAULTS, {"server": overrides}), [])


def test_server_dir_fills_in_all_three_paths(tmp_path: Path) -> None:
    (tmp_path / "server.cfg").touch()
    (tmp_path / "basic.cfg").touch()
    (tmp_path / "profiles").mkdir()

    paths = resolve_server_paths(server_config(tmp_path, dir=str(tmp_path)))
    assert paths.problems == []
    assert paths.config == tmp_path / "server.cfg"
    assert paths.cfg == tmp_path / "basic.cfg"
    assert paths.profiles == tmp_path / "profiles"


def test_server_dir_accepts_the_alternative_filenames(tmp_path: Path) -> None:
    (tmp_path / "config.cfg").touch()
    (tmp_path / "network.cfg").touch()
    paths = resolve_server_paths(server_config(tmp_path, dir=str(tmp_path)))
    assert paths.config == tmp_path / "config.cfg"
    assert paths.cfg == tmp_path / "network.cfg"


def test_profiles_is_derived_even_when_it_does_not_exist_yet(tmp_path: Path) -> None:
    """Arma creates the profiles folder itself, so it need not exist up front."""
    (tmp_path / "server.cfg").touch()
    (tmp_path / "basic.cfg").touch()
    paths = resolve_server_paths(server_config(tmp_path, dir=str(tmp_path)))
    assert paths.profiles == tmp_path / "profiles"
    assert paths.problems == []


def test_explicit_keys_override_the_dir(tmp_path: Path) -> None:
    (tmp_path / "server.cfg").touch()
    (tmp_path / "basic.cfg").touch()
    other = tmp_path / "other.cfg"
    other.touch()

    paths = resolve_server_paths(server_config(tmp_path, dir=str(tmp_path), config=str(other)))
    assert paths.config == other
    assert paths.cfg == tmp_path / "basic.cfg"  # still from dir


def test_a_config_key_pointing_at_a_folder_is_searched(tmp_path: Path) -> None:
    (tmp_path / "server.cfg").touch()
    (tmp_path / "basic.cfg").touch()
    paths = resolve_server_paths(
        server_config(tmp_path, config=str(tmp_path), cfg=str(tmp_path), profiles=str(tmp_path))
    )
    assert paths.config == tmp_path / "server.cfg"
    assert paths.cfg == tmp_path / "basic.cfg"


def test_missing_files_under_dir_are_reported_per_key(tmp_path: Path) -> None:
    (tmp_path / "server.cfg").touch()
    paths = resolve_server_paths(server_config(tmp_path, dir=str(tmp_path)))
    assert [key for key, _ in paths.problems] == ["cfg"]
    assert "basic.cfg or network.cfg" in paths.problems[0][1]


def test_dir_that_is_not_a_directory_is_reported(tmp_path: Path) -> None:
    bogus = tmp_path / "nope"
    paths = resolve_server_paths(server_config(tmp_path, dir=str(bogus)))
    assert paths.problems[0][0] == "dir"
    assert "not a directory" in paths.problems[0][1]


def test_nothing_configured_reports_each_key(tmp_path: Path) -> None:
    paths = resolve_server_paths(Config(DEFAULTS, []))
    assert [key for key, _ in paths.problems] == ["config", "cfg", "profiles"]
    assert all(message == "not configured" for _, message in paths.problems)


def test_launch_config_accepts_a_string_or_a_list() -> None:
    assert launch_config_names(Config(DEFAULTS, [])) == ["default"]
    chained = Config(deep_merge(DEFAULTS, {"launch": {"config": ["default", "ace"]}}), [])
    assert launch_config_names(chained) == ["default", "ace"]


def test_launch_config_rejects_other_types() -> None:
    bad = Config(deep_merge(DEFAULTS, {"launch": {"config": 3}}), [])
    with pytest.raises(A3RigError):
        launch_config_names(bad)


def test_config_saved_with_a_utf8_bom_still_parses(isolated_appdata: Path) -> None:
    """Notepad and other Windows editors add a BOM, which plain tomllib rejects."""
    path = isolated_appdata / "a3rig" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'\xef\xbb\xbf[launch]\ndelay_after_hemtt = 7\n')
    assert load_config(None, create_global=False).get("launch", "delay_after_hemtt") == 7


def test_launch_toml_with_a_utf8_bom_still_parses(project_factory) -> None:
    root = project_factory(launch_toml="[default]\n")
    (root / ".hemtt" / "launch.toml").write_bytes(b'\xef\xbb\xbf[default]\nworkshop = ["450814997"]\n')
    from a3rig.hemtt import load_profile_tables, load_project, resolve_extends

    tables = load_profile_tables(load_project(root))
    assert resolve_extends("default", tables).workshop == ["450814997"]


def test_broken_config_reports_the_file(isolated_appdata: Path) -> None:
    path = isolated_appdata / "a3rig" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[launch\nbroken", encoding="utf-8")
    with pytest.raises(A3RigError) as excinfo:
        load_config(None, create_global=False)
    assert "config.toml" in (excinfo.value.detail or "")


def test_windows_path_with_backslashes_gets_a_targeted_hint(isolated_appdata: Path) -> None:
    """The commonest edit mistake: a Windows path pasted into a TOML basic string."""
    path = isolated_appdata / "a3rig" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[paths]\narma3 = "C:\\Program Files (x86)\\Steam"\n', encoding="utf-8")
    with pytest.raises(A3RigError) as excinfo:
        load_config(None, create_global=False)
    assert "forward slashes" in (excinfo.value.fix or "")


def test_forward_slashes_and_literal_strings_both_work(isolated_appdata: Path) -> None:
    path = isolated_appdata / "a3rig" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[paths]\narma3 = \"C:/Program Files (x86)/Steam\"\n"
        "arma3_server = 'E:\\Arma3Server\\arma3server_x64.exe'\n",
        encoding="utf-8",
    )
    cfg = load_config(None, create_global=False)
    assert cfg.path("paths", "arma3") == Path("C:/Program Files (x86)/Steam")
    assert cfg.path("paths", "arma3_server") == Path(r"E:\Arma3Server\arma3server_x64.exe")


def test_generated_config_is_valid_toml_matching_the_defaults(isolated_appdata: Path) -> None:
    """The commented template and DEFAULTS must not drift apart."""
    import tomllib

    ensure_global_config()
    data = tomllib.loads(config_mod.global_config_path().read_text(encoding="utf-8"))
    for section, values in data.items():
        for key in values:
            assert key in DEFAULTS[section], f"{section}.{key} is in the template but not DEFAULTS"
    for section, values in DEFAULTS.items():
        for key in values:
            assert key in data[section], f"{section}.{key} is in DEFAULTS but not the template"
