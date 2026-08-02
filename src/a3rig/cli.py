"""The `a3rig` command line."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel

from . import APP_NAME, __version__
from .servercfg import patch_server_config
from .config import (
    Config,
    ensure_global_config,
    global_config_path,
    launch_config_names,
    load_config,
)
from .errors import A3RigError
from .paths import CLIENT_EXE, SERVER_EXE
from .preflight import (
    build_plan,
    raise_on_failures,
    render_checks,
    render_command,
    render_summary,
)
from .processes import (
    Session,
    TrackedProcess,
    arma_pids,
    clear_session,
    find_all_arma_processes,
    run_streaming,
    spawn_detached,
    stop_session,
    terminate,
    wait_for_hemtt,
)
from .tail import find_new_rpt, follow
from .hemtt import find_project_root, load_project

console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    name=APP_NAME,
    help="Start a local Arma 3 test rig: HEMTT dev build + 2 clients + dedicated server.",
    add_completion=False,
    no_args_is_help=False,
)

COMMANDS = {"run", "stop", "config", "doctor"}


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"{APP_NAME} {__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show the version and exit."
    ),
) -> None:
    """Start a local Arma 3 test rig from any HEMTT project directory."""


# --------------------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------------------


def _load(verbose: bool) -> tuple[Path, Config]:
    path, created = ensure_global_config()
    if created:
        console.print(f"[dim]created default config at {path}[/dim]")
    root = find_project_root()
    config = load_config(root, create_global=False)
    if verbose:
        for source in config.sources:
            console.print(f"[dim]config: {source}[/dim]")
    return root, config


def _sleep_with_notice(seconds: float, what: str) -> None:
    if seconds <= 0:
        return
    console.print(f"[dim]waiting {seconds:g}s before starting {what}...[/dim]")
    time.sleep(seconds)


@app.command()
def run(
    clients: int = typer.Option(2, "--clients", min=0, max=2, help="0 = build only, 1 = HEMTT's client, 2 = both."),
    no_server: bool = typer.Option(False, "--no-server", help="Do not start the dedicated server."),
    no_hemtt: bool = typer.Option(False, "--no-hemtt", help="Skip the build; requires an existing .hemttout/dev."),
    launch_config: Optional[list[str]] = typer.Option(
        None, "--launch-config", "-c", help="launch.toml profile. Repeat to chain profiles."
    ),
    no_tail: bool = typer.Option(False, "--no-tail", help="Do not tail the server .rpt."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Resolve everything and print the commands only."),
    fix_server_config: bool = typer.Option(
        False,
        "--fix-server-config",
        "--fix-battleye",
        help="Offer to correct the server.cfg settings a local rig needs.",
    ),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Show extra detail."),
) -> None:
    """Build the dev version and start the clients and the dedicated server."""
    root, config = _load(verbose)
    project = load_project(root)

    want_server = not no_server and bool(config.get("server", "enabled", True))
    names = list(launch_config) if launch_config else launch_config_names(config)

    plan = build_plan(
        project=project,
        config=config,
        launch_names=names,
        want_clients=clients,
        want_server=want_server,
        skip_hemtt=no_hemtt,
    )

    render_summary(console, plan)

    if fix_server_config and plan.server_scan is not None and plan.server_scan.broken:
        scan = plan.server_scan
        console.print(f"\n[yellow]Server config needs changes in[/yellow] {escape(str(scan.path))}")
        for finding in scan.broken:
            console.print(f"  - {escape(finding.problem or '')}")
            console.print(f"    [cyan]->[/cyan] {escape(finding.setting.assignment)}")
        if typer.confirm("Apply these changes?", default=False):
            for change in patch_server_config(scan.path, None, scan):
                console.print(f"  [green]{escape(change)}[/green]")
            plan = build_plan(
                project=project,
                config=config,
                launch_names=names,
                want_clients=clients,
                want_server=want_server,
                skip_hemtt=no_hemtt,
            )

    if dry_run:
        console.print()
        if plan.hemtt_argv:
            render_command(console, "1. hemtt", plan.hemtt_argv)
        if clients >= 2:
            render_command(console, "2. client 2", plan.client2_argv)
        if want_server:
            render_command(console, "3. dedicated server", plan.server_argv)
        where = f" Server would be at {plan.server_address}." if want_server else ""
        console.print(f"\n[dim]dry run - nothing was started.{where}[/dim]")
        if plan.failures:
            console.print(f"[yellow]note: {len(plan.failures)} check(s) would fail - run `a3rig doctor`[/yellow]")
        return

    raise_on_failures(plan)

    if plan.server_scan is not None and plan.server_scan.problem:
        console.print(
            Panel(
                escape(f"{plan.server_scan.problem}\n\n{plan.server_scan.fix_hint}"),
                title="[bold red]BattlEye is enabled[/bold red]",
                border_style="red",
            )
        )

    session = Session(project=str(project.root))
    console.print()

    # --- 3. hemtt -------------------------------------------------------------------
    before = arma_pids()
    if plan.hemtt_argv:
        console.print(f"[bold cyan]>[/bold cyan] {' '.join(plan.hemtt_argv)}\n")
        process = run_streaming(plan.hemtt_argv, cwd=project.root)
        if clients == 0:
            code = process.wait()
            if code != 0:
                raise A3RigError(
                    "hemtt dev failed", f"exit code {code}", "fix the build errors above and try again"
                )
        else:
            reason, new = wait_for_hemtt(process, before)
            if reason == "timeout":
                raise A3RigError(
                    "timed out waiting for HEMTT",
                    "hemtt neither exited nor started an Arma 3 client within 15 minutes",
                    "run `hemtt launch` by hand to see what it is doing",
                )
            if process.poll() not in (None, 0):
                raise A3RigError(
                    "hemtt launch failed",
                    f"exit code {process.poll()}",
                    "fix the build errors above and try again",
                )
            for pid, create_time in new.items():
                session.add(TrackedProcess("client1", pid, CLIENT_EXE, create_time))
            if new:
                console.print(f"\n[green]ok[/green] client 1 running (pid {', '.join(map(str, new))})")
            else:
                console.print("\n[yellow]![/yellow] HEMTT exited but no Arma 3 client was detected")

    # --- 4. verify the dev build -----------------------------------------------------
    if not plan.project.dev_build.is_dir():
        raise A3RigError(
            "dev build missing after HEMTT ran",
            str(plan.project.dev_build),
            "check the HEMTT output above - client 2 and the server both need this folder",
        )

    # --- 5. client 2 ------------------------------------------------------------------
    if clients >= 2:
        _sleep_with_notice(float(config.get("launch", "delay_after_hemtt", 25)), "client 2")
        tracked = spawn_detached(plan.client2_argv, cwd=plan.arma.client_dir)
        tracked.role = "client2"
        session.add(tracked)
        console.print(
            f"[green]ok[/green] client 2 running (pid {tracked.pid}, "
            f"profile {config.get('client2', 'profile', 'Dev2')})"
        )

    # --- 6. dedicated server -----------------------------------------------------------
    server_started_at = 0.0
    if plan.want_server:
        _sleep_with_notice(float(config.get("launch", "delay_after_client", 5)), "the dedicated server")
        server_started_at = time.time()
        tracked = spawn_detached(plan.server_argv, cwd=plan.arma.server_exe.parent)  # type: ignore[union-attr]
        tracked.role = "server"
        tracked.exe = SERVER_EXE
        session.add(tracked)
        console.print(f"[green]ok[/green] dedicated server running (pid {tracked.pid})")

    saved = session.save()
    if verbose:
        console.print(f"[dim]session: {saved}[/dim]")

    # --- 7. address and tail -------------------------------------------------------------
    if plan.want_server:
        console.print(
            Panel(
                f"[bold green]{plan.server_address}[/bold green]\n"
                "[dim]connect from the main menu: Multiplayer > Direct Connect[/dim]",
                title="[bold]Server[/bold]",
                title_align="left",
                border_style="green",
            )
        )

    if plan.want_server and not no_tail:
        if plan.server_profiles is not None:
            _tail_server_log(plan.server_profiles, server_started_at)
    else:
        console.print(f"[dim]processes are detached. `{APP_NAME} stop` terminates them.[/dim]")


def _tail_server_log(profiles_dir: Path, since: float) -> None:
    console.print(f"[dim]looking for the server .rpt in {profiles_dir}...[/dim]")
    rpt = find_new_rpt(profiles_dir, since)
    if rpt is None:
        console.print(
            f"[yellow]![/yellow] no new .rpt appeared in {profiles_dir}. "
            "The server may still be starting, or writes its log elsewhere."
        )
        return
    console.print(f"[dim]tailing {rpt}  (Ctrl+C stops the tail, not the game)[/dim]\n")
    for line in follow(rpt):
        console.print(f"[dim cyan][rpt][/dim cyan] {line}", highlight=False, markup=False)


# --------------------------------------------------------------------------------------
# stop
# --------------------------------------------------------------------------------------


@app.command()
def stop(
    all_processes: bool = typer.Option(
        False, "--all", help=f"Also kill every {CLIENT_EXE} / {SERVER_EXE} on this machine."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not ask for confirmation with --all."),
) -> None:
    """Terminate everything this tool started."""
    session = Session.load()
    if not session.processes:
        console.print("[dim]no session recorded[/dim]")
    else:
        console.print(f"[dim]session from {session.project or 'unknown project'}[/dim]")
        for tracked, outcome in stop_session(session):
            style = "green" if outcome == "stopped" else "dim" if outcome == "not running" else "red"
            console.print(f"  [{style}]{outcome}[/{style}]  {tracked.role} (pid {tracked.pid})")
        clear_session()

    if not all_processes:
        return

    remaining = find_all_arma_processes()
    if not remaining:
        console.print("[dim]no other Arma 3 processes running[/dim]")
        return

    console.print(f"\n[yellow]{len(remaining)} Arma 3 process(es) still running:[/yellow]")
    for proc in remaining:
        try:
            console.print(f"  pid {proc.pid}  {proc.name()}")
        except Exception:  # noqa: BLE001 - process may vanish mid-listing
            continue
    if not yes and not typer.confirm("Kill all of them? This includes any you started yourself.", default=False):
        console.print("[dim]cancelled[/dim]")
        return
    for proc in remaining:
        outcome = "stopped" if terminate(proc) else "could not stop"
        console.print(f"  [{'green' if outcome == 'stopped' else 'red'}]{outcome}[/] pid {proc.pid}")


# --------------------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------------------


@app.command()
def config(
    show_path: bool = typer.Option(False, "--path", help="Print the config file path and exit."),
    edit: bool = typer.Option(False, "--edit", help="Open the config in $EDITOR (notepad on Windows)."),
) -> None:
    """Show or edit the configuration."""
    path, created = ensure_global_config()
    if created:
        console.print(f"[dim]created default config at {path}[/dim]")

    if show_path:
        console.print(str(path))
        return

    if edit:
        editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
        argv = [editor, str(path)] if editor else ["notepad.exe", str(path)]
        if not editor and sys.platform != "win32":
            argv = ["vi", str(path)]
        try:
            subprocess.run(argv, check=False)
        except OSError as exc:
            raise A3RigError("could not open an editor", f"{argv[0]}: {exc}", "set $EDITOR") from exc
        return

    import tomlkit

    try:
        root: Path | None = find_project_root()
    except A3RigError:
        root = None
    merged = load_config(root, create_global=False)
    console.print(f"[dim]# effective configuration[/dim]")
    for source in merged.sources:
        console.print(f"[dim]# from {source}[/dim]")
    if root is None:
        console.print(f"[dim]# (not inside a HEMTT project - project overrides not applied)[/dim]")
    console.print(tomlkit.dumps(merged.data), highlight=False, markup=False)


# --------------------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------------------


@app.command()
def doctor(
    launch_config: Optional[list[str]] = typer.Option(
        None, "--launch-config", "-c", help="launch.toml profile to check. Repeat to chain profiles."
    ),
    show_commands: bool = typer.Option(False, "--commands", help="Also print the resolved command lines."),
) -> None:
    """Run every pre-flight check and print a pass/fail report."""
    root, cfg = _load(verbose=False)
    project = load_project(root)
    names = list(launch_config) if launch_config else launch_config_names(cfg)

    plan = build_plan(
        project=project,
        config=cfg,
        launch_names=names,
        want_clients=2,
        want_server=bool(cfg.get("server", "enabled", True)),
    )

    console.print()
    render_checks(console, plan)
    console.print()
    render_summary(console, plan)

    if show_commands:
        console.print()
        render_command(console, "1. hemtt", plan.hemtt_argv)
        render_command(console, "2. client 2", plan.client2_argv)
        render_command(console, "3. dedicated server", plan.server_argv)

    failures = plan.failures
    if failures:
        console.print(f"\n[red]{len(failures)} check(s) failed.[/red]")
        raise typer.Exit(1)
    console.print("\n[green]all checks passed.[/green]")


# --------------------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------------------


def main() -> None:
    """Console-script entry point. Bare `a3rig` and `a3rig --flags` mean `a3rig run`."""
    argv = sys.argv[1:]
    if not argv:
        sys.argv.append("run")
    elif argv[0] not in COMMANDS and argv[0] not in ("--help", "-h", "--version"):
        sys.argv.insert(1, "run")

    try:
        app()
    except A3RigError as exc:
        # Details hold paths and config keys like `[server] config`; escape so rich does
        # not swallow them as markup.
        body = f"[bold]{escape(exc.title)}[/bold]"
        if exc.detail:
            body += f"\n\n{escape(exc.detail)}"
        if exc.fix:
            body += f"\n\n[cyan]fix:[/cyan] {escape(exc.fix)}"
        err_console.print(Panel(body, border_style="red", title="[red]error[/red]", title_align="left"))
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        console.print(
            f"\n[dim]stopped. Any game processes are still running - `{APP_NAME} stop` terminates them.[/dim]"
        )
        raise SystemExit(130) from None


if __name__ == "__main__":  # pragma: no cover
    main()
