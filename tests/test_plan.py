"""End-to-end resolution: launch.toml + config in, three command lines out."""

from __future__ import annotations

from pathlib import Path

import pytest

from a3rig import preflight
from a3rig.config import Config, DEFAULTS, deep_merge
from a3rig.hemtt import load_project
from a3rig.paths import ArmaPaths
from a3rig.preflight import FAIL, build_plan

LAUNCH_TOML = """
[default]
workshop = [
    "450814997",    # CBA_A3
    "463939057",    # ACE3
    "3372876344",   # Ace View
]
parameters = [
    "-skipIntro",
    "-noSplash",
    "-showScriptErrors",
    "-debug",
    "-filePatching",
    "-noPause",
]
mission = "TestSave.Stratis"
"""


@pytest.fixture
def rig(tmp_path: Path, project_factory, monkeypatch: pytest.MonkeyPatch):
    """A fake Arma install, workshop folder and HEMTT project wired together."""
    arma_dir = tmp_path / "Steam" / "steamapps" / "common" / "Arma 3"
    arma_dir.mkdir(parents=True)
    client_exe = arma_dir / "arma3_x64.exe"
    server_exe = arma_dir / "arma3server_x64.exe"
    client_exe.touch()
    server_exe.touch()

    workshop = tmp_path / "Steam" / "steamapps" / "workshop" / "content" / "107410"
    for mod_id in ("450814997", "463939057", "3372876344"):
        (workshop / mod_id).mkdir(parents=True)

    hemtt_exe = tmp_path / "hemtt.exe"
    hemtt_exe.touch()

    root = project_factory(launch_toml=LAUNCH_TOML)
    (root / ".hemttout" / "dev").mkdir(parents=True)

    monkeypatch.setattr(
        preflight,
        "detect_arma_paths",
        lambda **_: ArmaPaths(
            client_dir=arma_dir,
            client_exe=client_exe,
            server_exe=server_exe,
            workshop=workshop,
            steam_root=tmp_path / "Steam",
            server_is_bundled=True,
            notes=[],
        ),
    )

    def make_config(**overrides) -> Config:
        data = deep_merge(DEFAULTS, {"paths": {"hemtt": str(hemtt_exe)}})
        return Config(deep_merge(data, overrides), [])

    return {
        "root": root,
        "arma_dir": arma_dir,
        "server_exe": server_exe,
        "workshop": workshop,
        "hemtt_exe": hemtt_exe,
        "config": make_config,
    }


def mods_of(argv: list[str]) -> list[str]:
    return [arg for arg in argv if arg.startswith("-mod=")]


def test_client2_and_server_get_identical_mod_arguments(rig) -> None:
    plan = build_plan(load_project(rig["root"]), rig["config"](), want_server=False)
    assert mods_of(plan.client2_argv) == plan.mod_args
    assert mods_of(plan.server_argv) == plan.mod_args


def test_mod_arguments_are_sorted_with_the_dev_build_last(rig) -> None:
    plan = build_plan(load_project(rig["root"]), rig["config"](), want_server=False)
    workshop = rig["workshop"]
    assert plan.mod_args == [
        f'-mod="{workshop / "3372876344"}"',
        f'-mod="{workshop / "450814997"}"',
        f'-mod="{workshop / "463939057"}"',
        f'-mod="{rig["root"] / ".hemttout" / "dev"}"',
    ]


def test_client2_inherits_hemtt_parameters_and_gets_its_own_profile(rig) -> None:
    plan = build_plan(load_project(rig["root"]), rig["config"](), want_server=False)
    argv = plan.client2_argv
    assert argv[0] == str(rig["arma_dir"] / "arma3_x64.exe")
    for implicit in ("-skipIntro", "-noSplash", "-showScriptErrors", "-debug"):
        assert implicit in argv
    assert "-noPause" in argv  # from launch.toml parameters
    assert "-name=Dev2" in argv
    assert "-noLauncher" in argv  # from [client2] parameters
    assert argv.count("-filePatching") == 1  # deduplicated


def test_client2_never_receives_the_mission_or_a_connect_argument(rig) -> None:
    plan = build_plan(load_project(rig["root"]), rig["config"](), want_server=False)
    joined = " ".join(plan.client2_argv)
    assert "TestSave" not in joined
    assert "-connect" not in joined
    assert "password" not in joined.lower()


def test_server_command_line(rig, tmp_path: Path) -> None:
    server_dir = tmp_path / "a3server"
    server_dir.mkdir()
    (server_dir / "server.cfg").write_text(GOOD_SERVER_CFG, encoding="utf-8")
    (server_dir / "basic.cfg").write_text("MinBandwidth = 131072;\n", encoding="utf-8")
    (server_dir / "profiles").mkdir()

    config = rig["config"](
        server={
            "config": str(server_dir / "server.cfg"),
            "cfg": str(server_dir / "basic.cfg"),
            "profiles": str(server_dir / "profiles"),
            "port": 2402,
        }
    )
    plan = build_plan(load_project(rig["root"]), config)

    argv = plan.server_argv
    assert argv[0] == str(rig["server_exe"])
    assert f"-config={server_dir / 'server.cfg'}" in argv
    assert f"-cfg={server_dir / 'basic.cfg'}" in argv
    assert f"-profiles={server_dir / 'profiles'}" in argv
    assert "-port=2402" in argv
    assert "-name=Server" in argv
    assert "-world=empty" in argv
    assert plan.server_address == "127.0.0.1:2402"
    # Client-only rendering flags must not reach the dedicated server.
    assert "-showScriptErrors" not in argv
    assert not plan.failures


def make_server_dir(tmp_path: Path, server_cfg: str) -> Path:
    server_dir = tmp_path / "a3server"
    server_dir.mkdir(exist_ok=True)
    (server_dir / "server.cfg").write_text(server_cfg, encoding="utf-8")
    (server_dir / "basic.cfg").touch()
    (server_dir / "profiles").mkdir(exist_ok=True)
    return server_dir


GOOD_SERVER_CFG = "BattlEye = 0;\nloopback = 1;\nkickDuplicate = 0;\nallowedFilePatching = 2;\n"
# FASTER's default: loopback = 0 means client 2 is kicked on SteamID.
LOOPBACK_OFF_CFG = "BattlEye = 0;\nloopback = 0;\nkickDuplicate = 0;\nallowedFilePatching = 2;\n"


def test_loopback_off_fails_a_two_client_run(rig, tmp_path: Path) -> None:
    """The reported bug: two clients on one Steam account get bounced by Steam auth."""
    server_dir = make_server_dir(tmp_path, LOOPBACK_OFF_CFG)
    plan = build_plan(
        load_project(rig["root"]), rig["config"](server={"dir": str(server_dir)}), want_clients=2
    )
    failure = next(c for c in plan.failures if c.name == "Server loopback")
    assert "same SteamID already in game" in failure.detail
    assert "loopback = 1;" in (failure.fix or "")
    assert "127.0.0.1" in (failure.fix or "")


def test_loopback_off_only_warns_for_a_single_client_run(rig, tmp_path: Path) -> None:
    """One client against the same server is legitimate, so it must not be blocked."""
    server_dir = make_server_dir(tmp_path, LOOPBACK_OFF_CFG)
    plan = build_plan(
        load_project(rig["root"]), rig["config"](server={"dir": str(server_dir)}), want_clients=1
    )
    assert not plan.failures
    check = next(c for c in plan.checks if c.name == "Server loopback")
    assert check.status == "warn"
    assert "only affects runs with 2 clients" in check.detail


def test_correct_server_config_passes(rig, tmp_path: Path) -> None:
    server_dir = make_server_dir(tmp_path, GOOD_SERVER_CFG)
    plan = build_plan(
        load_project(rig["root"]), rig["config"](server={"dir": str(server_dir)}), want_clients=2
    )
    assert not plan.failures
    assert next(c for c in plan.checks if c.name == "Server loopback").status == "pass"


def test_kick_duplicate_and_file_patching_only_warn(rig, tmp_path: Path) -> None:
    server_dir = make_server_dir(
        tmp_path, "BattlEye = 0;\nloopback = 1;\nkickDuplicate = 1;\nallowedFilePatching = 0;\n"
    )
    plan = build_plan(
        load_project(rig["root"]), rig["config"](server={"dir": str(server_dir)}), want_clients=2
    )
    assert not plan.failures
    joined = " ".join(plan.warnings)
    assert "kickDuplicate" in joined
    assert "allowedFilePatching" in joined


def test_battleye_enabled_fails_preflight(rig, tmp_path: Path) -> None:
    server_dir = make_server_dir(tmp_path, GOOD_SERVER_CFG.replace("BattlEye = 0;", "BattlEye = 1;"))
    config = rig["config"](
        server={
            "config": str(server_dir / "server.cfg"),
            "cfg": str(server_dir / "basic.cfg"),
            "profiles": str(server_dir / "profiles"),
        }
    )
    plan = build_plan(load_project(rig["root"]), config)
    assert [c.name for c in plan.failures] == ["BattlEye"]


def test_unconfigured_server_paths_name_the_missing_keys(rig) -> None:
    plan = build_plan(load_project(rig["root"]), rig["config"]())
    failure = next(c for c in plan.failures if c.name == "Server config")
    assert "[server] config" in failure.detail
    assert "[server] cfg" in failure.detail
    assert "[server] profiles" in failure.detail
    assert "[server] dir" in (failure.fix or "")
    assert "--no-server" in (failure.fix or "")


def test_server_dir_alone_is_enough(rig, tmp_path: Path) -> None:
    """One folder, and a3rig finds server.cfg, basic.cfg and profiles/ inside it."""
    server_dir = tmp_path / "arma3-test"
    server_dir.mkdir()
    (server_dir / "server.cfg").write_text(GOOD_SERVER_CFG, encoding="utf-8")
    (server_dir / "basic.cfg").touch()
    (server_dir / "profiles").mkdir()

    plan = build_plan(load_project(rig["root"]), rig["config"](server={"dir": str(server_dir)}))

    assert not plan.failures
    assert f"-config={server_dir / 'server.cfg'}" in plan.server_argv
    assert f"-cfg={server_dir / 'basic.cfg'}" in plan.server_argv
    assert f"-profiles={server_dir / 'profiles'}" in plan.server_argv
    assert plan.server_profiles == server_dir / "profiles"


def test_missing_profiles_folder_warns_rather_than_failing(rig, tmp_path: Path) -> None:
    server_dir = tmp_path / "arma3-test"
    server_dir.mkdir()
    (server_dir / "server.cfg").write_text(GOOD_SERVER_CFG, encoding="utf-8")
    (server_dir / "basic.cfg").touch()

    plan = build_plan(load_project(rig["root"]), rig["config"](server={"dir": str(server_dir)}))
    assert not plan.failures
    profiles_check = next(c for c in plan.checks if c.name == "Server profiles")
    assert profiles_check.status == "warn"


def test_no_server_skips_the_server_checks(rig) -> None:
    plan = build_plan(load_project(rig["root"]), rig["config"](), want_server=False)
    assert not plan.failures


def test_missing_workshop_mod_warns_but_does_not_fail(rig) -> None:
    root = rig["root"]
    (root / ".hemtt" / "launch.toml").write_text(
        '[default]\nworkshop = ["450814997", "999999999"]  # nope\n', encoding="utf-8"
    )
    plan = build_plan(load_project(root), rig["config"](), want_server=False)
    assert not plan.failures
    assert any("999999999" in warning for warning in plan.warnings)
    assert all("999999999" not in arg for arg in plan.mod_args)


def test_hemtt_command_lines_per_mode(rig) -> None:
    project = load_project(rig["root"])
    hemtt = str(rig["hemtt_exe"])

    default = build_plan(project, rig["config"](), want_server=False)
    assert default.hemtt_argv == [hemtt, "launch", "default"]

    quick = build_plan(project, rig["config"](), want_server=False, skip_hemtt=True)
    assert quick.hemtt_argv == [hemtt, "launch", "default", "--quick"]

    build_only = build_plan(project, rig["config"](), want_clients=0, want_server=False)
    assert build_only.hemtt_argv == [hemtt, "dev"]

    nothing = build_plan(
        project, rig["config"](), want_clients=0, want_server=False, skip_hemtt=True
    )
    assert nothing.hemtt_argv == []


def test_chained_launch_configs_reach_hemtt_and_the_mod_list(rig) -> None:
    root = rig["root"]
    (root / ".hemtt" / "launch.toml").write_text(
        '[default]\nworkshop = ["450814997"]\n\n[ace]\nworkshop = ["463939057"]\n', encoding="utf-8"
    )
    plan = build_plan(load_project(root), rig["config"](), ["default", "ace"], want_server=False)
    assert plan.hemtt_argv[1:] == ["launch", "default", "ace"]
    assert len(plan.mod_args) == 3  # two workshop mods plus the dev build


def test_no_hemtt_without_a_dev_build_fails(rig) -> None:
    import shutil

    shutil.rmtree(rig["root"] / ".hemttout")
    plan = build_plan(load_project(rig["root"]), rig["config"](), want_server=False, skip_hemtt=True)
    failure = next(c for c in plan.failures if c.name == "Dev build")
    assert failure.status == FAIL
    assert "--no-hemtt" in (failure.fix or "")


def test_profile_executable_override_is_honoured(rig) -> None:
    root = rig["root"]
    (root / ".hemtt" / "launch.toml").write_text(
        '[default]\nworkshop = []\nexecutable = "arma3profiling_x64"\n', encoding="utf-8"
    )
    plan = build_plan(load_project(root), rig["config"](), want_server=False)
    assert plan.client2_argv[0] == str(rig["arma_dir"] / "arma3profiling_x64.exe")


def test_server_mods_resolve_to_server_mod_arguments(rig) -> None:
    config = rig["config"](server={"server_mods": ["450814997"], "enabled": False})
    plan = build_plan(load_project(rig["root"]), config, want_server=False)
    assert f'-serverMod="{rig["workshop"] / "450814997"}"' in plan.server_argv
