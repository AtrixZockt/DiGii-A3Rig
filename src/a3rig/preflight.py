"""Resolution and pre-flight checks.

`build_plan` resolves everything and starts nothing, so `run --dry-run` and `doctor` are
the same code path as a real run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .servercfg import (
    ALLOWED_FILE_PATCHING,
    KICK_DUPLICATE,
    LOOPBACK,
    ServerConfigScan,
    scan_server_config,
)
from .config import Config, launch_config_names, resolve_server_paths
from .errors import A3RigError
from .hemtt import (
    HEMTT_IMPLICIT_PARAMETERS,
    HemttGlobalConfig,
    LaunchProfile,
    Project,
    ResolvedMod,
    build_mod_args,
    dedupe,
    find_hemtt_executable,
    load_profile_tables,
    mod_argument,
    read_entry_comments,
    read_presets,
    resolve_launch_profile,
    resolve_mods,
)
from .paths import ArmaPaths, detect_arma_paths

PASS, WARN, FAIL = "pass", "warn", "fail"


@dataclass
class Check:
    """One pre-flight result."""

    name: str
    status: str
    detail: str = ""
    fix: str | None = None


@dataclass
class LaunchPlan:
    """Everything resolved for a run: paths, mods and the exact command lines."""

    project: Project
    config: Config
    arma: ArmaPaths
    profile: LaunchProfile
    launch_names: list[str]
    mods: list[ResolvedMod]
    mod_args: list[str]
    hemtt_exe: Path | None
    hemtt_argv: list[str]
    client2_argv: list[str]
    server_argv: list[str]
    server_scan: ServerConfigScan | None
    #: Resolved `-profiles=` directory, where the server writes its .rpt.
    server_profiles: Path | None = None
    checks: list[Check] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    want_clients: int = 2
    want_server: bool = True
    skip_hemtt: bool = False
    port: int = 2302

    @property
    def failures(self) -> list[Check]:
        return [check for check in self.checks if check.status == FAIL]

    @property
    def server_address(self) -> str:
        return f"127.0.0.1:{self.port}"


def _client_executable(arma: ArmaPaths, profile: LaunchProfile) -> Path:
    """Honour a profile's ``executable`` override, relative to the Arma 3 directory."""
    name = profile.client_executable
    candidate = Path(name)
    if not candidate.is_absolute():
        candidate = arma.client_dir / name
    if candidate.suffix.lower() != ".exe":
        candidate = candidate.with_suffix(".exe")
    return candidate


def build_plan(
    project: Project,
    config: Config,
    launch_names: list[str] | None = None,
    want_clients: int = 2,
    want_server: bool = True,
    skip_hemtt: bool = False,
) -> LaunchPlan:
    """Resolve paths, mods and command lines, collecting checks rather than raising."""
    checks: list[Check] = []
    warnings: list[str] = []

    # --- hemtt -----------------------------------------------------------------------
    hemtt_exe: Path | None = None
    try:
        hemtt_exe = find_hemtt_executable(str(config.get("paths", "hemtt", "hemtt")))
        checks.append(Check("hemtt", PASS, str(hemtt_exe)))
    except A3RigError as exc:
        checks.append(Check("hemtt", FAIL, exc.detail or exc.title, exc.fix))

    # --- Arma ------------------------------------------------------------------------
    arma = detect_arma_paths(
        client_override=config.path("paths", "arma3"),
        server_override=config.path("paths", "arma3_server"),
        workshop_override=config.path("paths", "workshop"),
    )
    warnings.extend(arma.notes)
    checks.append(Check("Arma 3 client", PASS, str(arma.client_exe)))
    if arma.server_exe is None:
        checks.append(
            Check(
                "Arma 3 server",
                FAIL if want_server else WARN,
                "arma3server_x64.exe not found",
                "install the Arma 3 Dedicated Server app, or set [paths] arma3_server",
            )
        )
    else:
        label = str(arma.server_exe) + (" (bundled with the client)" if arma.server_is_bundled else "")
        checks.append(Check("Arma 3 server", PASS, label))
    if arma.workshop is None:
        checks.append(
            Check(
                "Workshop content",
                WARN,
                "workshop/content/107410 not found in any Steam library",
                "subscribe to at least one Arma 3 mod, or set [paths] workshop",
            )
        )
    else:
        checks.append(Check("Workshop content", PASS, str(arma.workshop)))

    # --- launch profile ---------------------------------------------------------------
    launch_names = launch_names or launch_config_names(config)
    tables = load_profile_tables(project)
    hemtt_global = HemttGlobalConfig.load()
    profile = resolve_launch_profile(launch_names, tables, hemtt_global.profiles)
    source = project.launch_toml if project.launch_toml.is_file() else project.project_toml
    checks.append(Check("Launch profile", PASS, f"{' '.join(launch_names)} (from {source.name})"))
    if hemtt_global.path is not None:
        warnings.append(
            f"HEMTT global config in use: {hemtt_global.path} "
            f"({len(hemtt_global.pointers)} pointer(s), {len(hemtt_global.profiles)} profile(s))"
        )

    # --- mods --------------------------------------------------------------------------
    preset_ids, preset_dlc, preset_warnings = read_presets(project, profile)
    warnings.extend(preset_warnings)
    entries = dedupe([*profile.workshop, *preset_ids])
    dlc = dedupe([*profile.dlc, *preset_dlc])
    comments = read_entry_comments(project.launch_toml, project.project_toml)
    mods = resolve_mods(
        entries,
        arma.workshop,
        hemtt_global.pointers,
        published_id=project.published_id,
        comments=comments,
    )

    dev_build = project.dev_build
    mod_paths = [mod.path for mod in mods if mod.path is not None]
    # HEMTT adds the dev build to the same list before sorting, so it is not pinned first.
    mod_paths.append(dev_build)
    mod_args = build_mod_args(dlc, mod_paths)

    unresolved = [mod for mod in mods if not mod.ok and mod.source != "self"]
    for mod in unresolved:
        warnings.append(f"workshop mod {mod.describe()} could not be resolved - {mod.problem}; skipping it")
    skipped_self = [mod for mod in mods if mod.source == "self"]
    for mod in skipped_self:
        warnings.append(f"skipping {mod.entry} - {mod.problem}")
    resolved_count = len([mod for mod in mods if mod.ok])
    checks.append(
        Check(
            "Mods",
            WARN if unresolved else PASS,
            f"{resolved_count}/{len(mods)} workshop entries resolved"
            + (f", {len(dlc)} DLC" if dlc else "")
            + (f", {len(unresolved)} missing" if unresolved else ""),
            "subscribe to the missing mods in Steam, then let them download" if unresolved else None,
        )
    )

    if not skip_hemtt:
        checks.append(Check("Dev build", PASS, f"{dev_build} (rebuilt by hemtt)"))
    elif dev_build.is_dir():
        checks.append(Check("Dev build", PASS, str(dev_build)))
    else:
        checks.append(
            Check(
                "Dev build",
                FAIL,
                f"{dev_build} does not exist",
                "run without --no-hemtt so HEMTT builds it first",
            )
        )

    # --- server config ------------------------------------------------------------------
    server_scan: ServerConfigScan | None = None
    server_paths = resolve_server_paths(config)
    server_config = server_paths.config
    server_cfg = server_paths.cfg
    server_profiles = server_paths.profiles
    port = int(config.get("server", "port", 2302))

    if want_server:
        if server_paths.problems:
            unconfigured = [key for key, message in server_paths.problems if message == "not configured"]
            detail = "; ".join(f"[server] {key}: {message}" for key, message in server_paths.problems)
            fix = (
                'set [server] dir = "D:/arma3-test" to point at the folder holding '
                "server.cfg, basic.cfg and profiles/, or set the keys individually "
                "(`a3rig config --edit`). Run with --no-server to skip the server."
                if unconfigured
                else "check the paths under [server] in `a3rig config --edit`"
            )
            checks.append(Check("Server config", FAIL, detail, fix))
        else:
            if server_paths.base is not None:
                checks.append(Check("Server dir", PASS, str(server_paths.base)))
            for key, path in (("config", server_config), ("cfg", server_cfg)):
                assert path is not None
                if path.is_file():
                    checks.append(Check(f"Server {key}", PASS, str(path)))
                else:
                    checks.append(
                        Check(
                            f"Server {key}",
                            FAIL,
                            f"{path} does not exist",
                            f"create it, or point [server] {key} somewhere that does",
                        )
                    )
            assert server_profiles is not None
            if server_profiles.is_dir():
                checks.append(Check("Server profiles", PASS, str(server_profiles)))
            else:
                # Arma creates the profiles directory itself, so this is not fatal.
                checks.append(
                    Check("Server profiles", WARN, f"{server_profiles} does not exist yet - Arma will create it")
                )

            if server_config is not None and server_config.is_file():
                server_scan = scan_server_config(server_config)

                if server_scan.problem:
                    checks.append(Check("BattlEye", FAIL, server_scan.problem, server_scan.fix_hint))
                else:
                    checks.append(Check("BattlEye", PASS, "BattlEye = 0;"))

                # loopback only matters for the two-client workflow: with Steam auth on,
                # the second client is kicked on SteamID. A single-client run is fine.
                loopback = server_scan.finding(LOOPBACK)
                two_clients = want_clients >= 2
                if loopback.ok:
                    checks.append(Check("Server loopback", PASS, "loopback = 1; (LAN mode)"))
                else:
                    checks.append(
                        Check(
                            "Server loopback",
                            FAIL if two_clients else WARN,
                            (loopback.problem or "")
                            + ("" if two_clients else " (only affects runs with 2 clients)"),
                            f"{loopback.fix_hint(server_scan.path)} - LAN mode keeps the server "
                            "out of the public browser, which is what a local rig wants; "
                            "Direct Connect to 127.0.0.1 still works",
                        )
                    )

                for setting in (KICK_DUPLICATE, ALLOWED_FILE_PATCHING):
                    finding = server_scan.finding(setting)
                    if not finding.ok:
                        warnings.append(f"{finding.problem}; {finding.fix_hint(server_scan.path)}")

    # --- command lines --------------------------------------------------------------------
    hemtt_argv: list[str] = []
    if hemtt_exe is not None and not (skip_hemtt and want_clients == 0):
        if want_clients == 0:
            # No client to launch, so build with `hemtt dev` and pass through the
            # build-affecting options the launch profile would have applied.
            hemtt_argv = [str(hemtt_exe), "dev"]
            for optional in profile.optionals:
                hemtt_argv += ["-o", optional]
            if profile.uses_binarize:
                hemtt_argv.append("--binarize")
            if not profile.uses_rapify:
                hemtt_argv.append("--no-rap")
        else:
            hemtt_argv = [str(hemtt_exe), "launch", *launch_names]
            if skip_hemtt:
                hemtt_argv.append("--quick")

    client_exe = _client_executable(arma, profile)
    client2_argv = dedupe(
        [
            str(client_exe),
            *mod_args,
            *HEMTT_IMPLICIT_PARAMETERS,
            *profile.parameters,
            *(["-filePatching"] if profile.uses_file_patching else []),
            f"-name={config.get('client2', 'profile', 'Dev2')}",
            *[str(p) for p in config.get("client2", "parameters", [])],
        ]
    )

    server_argv: list[str] = []
    if arma.server_exe is not None:
        server_mod_args: list[str] = []
        raw_server_mods = [str(m) for m in config.get("server", "server_mods", [])]
        if raw_server_mods:
            resolved_server_mods = resolve_mods(
                raw_server_mods, arma.workshop, hemtt_global.pointers, comments=comments
            )
            for mod in resolved_server_mods:
                if mod.ok and mod.path is not None:
                    server_mod_args.append(mod_argument(mod.path, "-serverMod"))
                else:
                    warnings.append(f"[server] server_mods entry {mod.entry!r} unresolved - {mod.problem}")
        server_argv = dedupe(
            [
                str(arma.server_exe),
                *mod_args,
                *server_mod_args,
                *([f"-config={server_config}"] if server_config else []),
                *([f"-cfg={server_cfg}"] if server_cfg else []),
                *([f"-profiles={server_profiles}"] if server_profiles else []),
                f"-name={config.get('server', 'name', 'Server')}",
                f"-port={port}",
                *[str(p) for p in config.get("server", "parameters", [])],
                *(["-filePatching"] if profile.uses_file_patching else []),
            ]
        )

    return LaunchPlan(
        project=project,
        config=config,
        arma=arma,
        profile=profile,
        launch_names=launch_names,
        mods=mods,
        mod_args=mod_args,
        hemtt_exe=hemtt_exe,
        hemtt_argv=hemtt_argv,
        client2_argv=client2_argv,
        server_argv=server_argv,
        server_scan=server_scan,
        server_profiles=server_profiles,
        checks=checks,
        warnings=warnings,
        want_clients=want_clients,
        want_server=want_server,
        skip_hemtt=skip_hemtt,
        port=port,
    )


# --------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------

_STATUS_MARK = {PASS: ("[green]ok[/green]", "green"), WARN: ("[yellow]warn[/yellow]", "yellow"), FAIL: ("[red]FAIL[/red]", "red")}


def render_summary(console: Console, plan: LaunchPlan) -> None:
    """The compact table printed before anything is started."""
    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(overflow="fold")

    table.add_row("Project", f"{escape(plan.project.name)}  [dim]({escape(str(plan.project.root))})[/dim]")
    table.add_row("Launch config", escape(" ".join(plan.launch_names)))
    table.add_row("Dev build", Text(str(plan.project.dev_build)))
    table.add_row("Arma 3", Text(str(plan.arma.client_exe)))
    if plan.arma.server_exe is not None:
        table.add_row("Server", Text(str(plan.arma.server_exe)))
    if plan.arma.workshop is not None:
        table.add_row("Workshop", Text(str(plan.arma.workshop)))
    if plan.hemtt_exe is not None:
        table.add_row("HEMTT", Text(str(plan.hemtt_exe)))

    plan_bits = []
    plan_bits.append("build" if not plan.skip_hemtt else "no build")
    plan_bits.append(f"{plan.want_clients} client{'s' if plan.want_clients != 1 else ''}")
    plan_bits.append("server" if plan.want_server else "no server")
    table.add_row("Plan", ", ".join(plan_bits))

    mod_lines = []
    for mod in plan.mods:
        described = escape(mod.describe())
        if mod.ok:
            mod_lines.append(f"[green]+[/green] {described}")
        elif mod.source == "self":
            mod_lines.append(f"[dim]- {described} (this project)[/dim]")
        else:
            mod_lines.append(f"[red]![/red] {described}")
    mod_lines.append("[green]+[/green] [bold].hemttout/dev[/bold]")
    table.add_row(f"Mods ({len(plan.mod_args)})", "\n".join(mod_lines))

    console.print(Panel(table, title="[bold]a3rig[/bold]", title_align="left", border_style="cyan"))

    # Warning-level checks are otherwise only visible in `doctor`, but they are just as
    # relevant to a run - a missing setting the current flags happen to make harmless.
    for check in plan.checks:
        if check.status == WARN:
            console.print(f"  [yellow]![/yellow] {escape(f'{check.name}: {check.detail}')}")
    for warning in plan.warnings:
        console.print(f"  [yellow]![/yellow] {escape(warning)}")


def render_checks(console: Console, plan: LaunchPlan) -> None:
    """The pass/fail report used by `a3rig doctor`."""
    table = Table(box=None, padding=(0, 2, 0, 0))
    table.add_column("", no_wrap=True)
    table.add_column("Check", style="bold", no_wrap=True)
    table.add_column("Detail", overflow="fold")

    for check in plan.checks:
        mark, _ = _STATUS_MARK[check.status]
        # Details carry paths and config keys like `[server] config`, which rich would
        # otherwise parse as markup, so they are rendered as literal text.
        table.add_row(mark, check.name, Text(check.detail))
        if check.fix:
            table.add_row("", "", Text(f"fix: {check.fix}", style="dim"))
    console.print(table)


def render_command(console: Console, title: str, argv: list[str]) -> None:
    """Print one resolved command line, one argument per line so it stays readable."""
    if not argv:
        console.print(f"[bold]{title}[/bold]\n  [dim](not started)[/dim]")
        return
    body = Text()
    body.append(argv[0], style="bold white")
    for arg in argv[1:]:
        body.append("\n  ")
        body.append(arg, style="green" if arg.startswith("-mod=") else "white")
    console.print(Panel(body, title=f"[bold]{title}[/bold]", title_align="left", border_style="dim"))


def raise_on_failures(plan: LaunchPlan) -> None:
    """Turn failed checks into a single actionable error."""
    failures = plan.failures
    if not failures:
        return
    detail = "\n".join(f"- {check.name}: {check.detail}" for check in failures)
    fixes = "\n".join(f"- {check.fix}" for check in failures if check.fix)
    raise A3RigError(
        f"{len(failures)} pre-flight check{'s' if len(failures) != 1 else ''} failed",
        detail,
        fixes or "run `a3rig doctor` for the full report",
    )
