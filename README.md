# a3rig

Start a local Arma 3 mod test environment — HEMTT dev build, two clients and a dedicated
server — with one command, from inside any HEMTT project.

```console
$ cd E:\GitHub\MyMod
$ a3rig
```

Windows-first. Runs entirely at user level; no admin rights needed.

---

## What it does

In this order, and only this order:

1. **Finds the project** by walking up from the current directory to `.hemtt/project.toml`.
2. **Pre-flight checks** — `hemtt` on PATH, `launch.toml` parses, Arma 3 client and server
   executables resolve, server config paths exist, BattlEye is off. Prints a summary of
   everything it resolved *before* starting anything.
3. **`hemtt launch <config>`** in the project root, output streaming live. This builds the
   dev version and starts client 1.
4. **Verifies `.hemttout/dev`** exists. Hard error if it doesn't — client 2 and the server
   both need it.
5. **Starts client 2** after `delay_after_hemtt` seconds, on its own Arma profile.
6. **Starts the dedicated server** after `delay_after_client` seconds.
7. **Prints `127.0.0.1:<port>`** and tails the server `.rpt` until Ctrl+C.

Clients launch to the **main menu** — you connect manually. No `-connect`, no `-password`.

Every process is started detached, so closing the terminal does not kill Arma. Ctrl+C
during the tail stops the tail only; use `a3rig stop` to terminate the games.

## Install

```console
$ winget install DiGii.A3Rig
```

Self-contained — no Python needed. Open a new terminal afterwards and `a3rig` is on PATH.

[HEMTT](https://hemtt.dev) needs to be on PATH too — `winget install BrettMayson.HEMTT`.

<details>
<summary><b>Installing from source instead</b> — only needed to work on a3rig itself</summary>

<br>

One command, from the repository root:

```console
$ powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

Then **open a new terminal** — `a3rig` works from any project directory, including in a
VS Code window that was already running.

**Why the installer touches your PowerShell profile.**
`pipx ensurepath` only edits the persisted user PATH, and a process reads that once, at
launch. VS Code hands each integrated terminal the environment it captured when *it*
started, so a terminal opened in a long-running VS Code window never sees a PATH entry
added afterwards — and neither does a second window, since every window is a child of the
one main process. `Developer: Reload Window` does not help either.

So the installer also adds a small delimited block to `$PROFILE.CurrentUserAllHosts`:

```powershell
# >>> a3rig >>>
$a3rigBin = 'C:\Users\you\.local\bin'
if ((Test-Path $a3rigBin) -and (($env:PATH -split ';') -notcontains $a3rigBin)) {
    $env:PATH = "$env:PATH;$a3rigBin"
}
# <<< a3rig <<<
```

A profile runs per session regardless of what the parent process handed down, so a new
terminal is enough. Re-running the installer rewrites the block rather than adding a
second copy, and deleting the block cleanly undoes it.

This covers PowerShell. A `cmd.exe` terminal inside an already-running VS Code still needs
VS Code fully restarted — and "fully" means every window closed, so the main process
actually exits.

`install.ps1` finds a system Python 3.11+, bootstraps pipx if it is missing, puts it on
your user PATH, and installs `a3rig` in editable mode. It is safe to re-run, and it
deliberately ignores an activated virtualenv: `py` and `python` both resolve to the venv's
interpreter when one is active, which is what makes the manual steps below fail with
`No module named pipx`.

**Manual install.** Run these from a terminal with **no virtualenv active**:

```console
$ py -m pip install --user pipx
$ py -m pipx ensurepath
```

Restart the terminal, then from the repository root:

```console
$ pipx install --editable .
```

Or with uv:

```console
$ winget install --id=astral-sh.uv
$ uv tool install --editable .
```

A source install needs Python 3.11+. The winget package does not.

</details>

## Commands

| Command | What it does |
|---|---|
| `a3rig` / `a3rig run` | Build and start the rig. |
| `a3rig doctor` | Run every check and print a pass/fail report. **Start here when something is wrong.** |
| `a3rig stop` | Terminate everything this tool started. |
| `a3rig config` | Print the effective merged configuration. |
| `a3rig --version` | Version. |

### `a3rig run`

| Flag | |
|---|---|
| `--clients N` | `2` (default) both clients, `1` only HEMTT's, `0` build only. |
| `--no-server` | Do not start the dedicated server. |
| `--no-hemtt` | Skip the build — runs `hemtt launch --quick`, needs an existing `.hemttout/dev`. |
| `-c`, `--launch-config <name>` | `launch.toml` profile. Repeat to chain: `-c default -c ace`. |
| `--no-tail` | Do not tail the server `.rpt`. |
| `--dry-run` | Resolve everything and print the exact command lines. Starts nothing. |
| `--fix-server-config` | Offer to correct the `server.cfg` settings a local rig needs. |
| `-v`, `--verbose` | Extra detail. |

### `a3rig stop`

Reads `%LOCALAPPDATA%\a3rig\session.json`, written at launch, and terminates those PIDs.
Each is verified by PID *and* process start time first, so a recycled PID belonging to
some unrelated program is never touched.

`--all` additionally sweeps every `arma3_x64.exe` / `arma3server_x64.exe` on the machine,
after confirmation (`-y` skips the prompt).

### `a3rig config`

`--path` prints the config file path. `--edit` opens it in `$EDITOR` (notepad on Windows).
With no flags it prints the effective configuration after all merging.

## Configuration

Global config at `%APPDATA%\a3rig\config.toml`, created with commented defaults on first
run. An optional per-project override at `<project root>/.hemtt/a3rig.toml` is deep-merged
over it, and CLI flags override both.

```toml
[paths]
# All auto-detected when left empty. Set these only if detection is wrong, or if you
# have an install outside Steam.
arma3        = ""       # the CLIENT: install dir, or arma3_x64.exe itself
arma3_server = ""       # the SERVER: its install dir, or arma3server_x64.exe itself
workshop     = ""       # <library>/steamapps/workshop/content/107410
hemtt        = "hemtt"

[launch]
config             = "default"  # a list chains profiles: ["default", "ace"]
delay_after_hemtt  = 25         # seconds before client 2 starts
delay_after_client = 5          # seconds before the server starts

[server]
enabled = true
port    = 2302

# Point `dir` at the folder holding your server files and leave the next three empty.
dir = "D:/arma3-test"

config   = ""  # -config=   ; defaults to <dir>/server.cfg  (or config.cfg)
cfg      = ""  # -cfg=      ; defaults to <dir>/basic.cfg   (or network.cfg)
profiles = ""  # -profiles= ; defaults to <dir>/profiles

name        = "Server"
parameters  = ["-filePatching", "-world=empty", "-noSound"]
server_mods = []                        # extra -serverMod= entries

[client2]
profile    = "Dev2"                     # -name= ; keeps client 2 off your normal profile
parameters = ["-window", "-noSplash", "-skipIntro", "-filePatching", "-noLauncher"]
```

> **Windows paths in TOML.** Use forward slashes — `"D:/arma3-test"`. A backslash inside
> double quotes is an escape character, so `"D:\arma3-test"` is a syntax error. Single
> quotes take the string verbatim if you prefer backslashes: `'D:\arma3-test'`.

### Pointing at the server files

The server needs `-config=`, `-cfg=` and `-profiles=`, and there is no sensible default
for any of them. The short way is one key:

```toml
[server]
dir = "D:/arma3-test"
```

`a3rig` then looks for `server.cfg` (or `config.cfg`), `basic.cfg` (or `network.cfg`) and
a `profiles` folder inside it. Anything set explicitly wins over `dir`, so you can mix —
and `config`/`cfg` may themselves name a folder, which is searched the same way. The
`profiles` folder does not have to exist yet; Arma creates it.

`a3rig doctor` shows exactly which file each key resolved to.

Client 2 and the server **inherit the resolved mod list** from `launch.toml`; the
`parameters` arrays are additional.

## Server settings a local rig needs

Four `server.cfg` settings decide whether a two-client local rig works. Every one of them
fails *silently* — the server starts normally, and you only find out when a client is
bounced or file patching quietly does nothing. So `a3rig` reads the `-config` file before
launching anything.

| setting | must be | if it isn't |
|---|---|---|
| `BattlEye` | `0` | **fails** — no reliable command-line switch exists for this |
| `loopback` | `1` | **fails a 2-client run** — client 2 is kicked on Steam ID |
| `kickDuplicate` | `0` | warns — Arma's legacy duplicate-ID kick |
| `allowedFilePatching` | `2` | warns — clients cannot use `-filePatching` |

`--fix-server-config` corrects them after confirmation, leaving a `.bak`.
(`--fix-battleye` still works as an alias.)

### `loopback` and "Same Steam user already in game"

Two clients on one PC share one Steam account, and a server with `loopback = 0` registers
with Steam and authenticates clients through it. Steam will not authorise two concurrent
sessions for one account, so the second client is kicked:

```
Client: Dev2 - SteamID:76561198145986243 - Kicked off - player with same SteamID already in game.
```

`loopback = 1` enforces LAN mode, which skips that authentication. The server then stays
out of the public browser — which is what a local rig wants — and Direct Connect to
`127.0.0.1` still works.

`kickDuplicate` is **not** the lever here, and setting it to `0` will not help: it governs
Arma's own legacy player ID, while this kick is keyed on the Steam ID. Note that this
message is a Steam-side restriction; if `loopback = 1` is not enough on your setup, the
remaining option is a second Steam account that owns Arma 3.

### BattlEye

The server is always launched as `arma3server_x64.exe` directly, never through the BattlEye
launcher; clients as `arma3_x64.exe` with `-noLauncher`, never `arma3launcher.exe`.

Watch the spelling: the key is `BattlEye`, eight letters. `battleEye` is a common typo that
Arma's config parser ignores outright, leaving BattlEye enabled — `a3rig` reports that case
separately rather than accepting it as an alias.

## Mod list parity with HEMTT

Client 2 and the server must load mods in the *identical* order to the client HEMTT starts,
or the three processes can disagree about which config wins. `a3rig` therefore reproduces
HEMTT 1.19's resolution exactly:

- **One `-mod=` argument per mod**, not a semicolon-joined string.
- The value is **wrapped in literal quotes** — `-mod="C:\Program Files (x86)\..."` — which
  is what HEMTT passes and matters because essentially every workshop path contains spaces
  and parentheses.
- **DLCs first**, in HEMTT's own enum order, then mod paths **sorted and deduplicated**.
- The **dev build is sorted in with the rest** — not pinned first. On a typical setup
  (`E:\GitHub\...` versus `C:\Program Files (x86)\...`) it sorts last and therefore
  overrides the workshop mods, which is what you want.
- Resolution order per entry: skip the id matching this project's own `meta.cpp`
  `publishedid`; then an exact `[launch.pointers]` match from `%APPDATA%\hemtt\config.toml`;
  then the `pointer:@mod` prefix form; then `<workshop>/107410/<id>`.
- `presets` are expanded from the Arma Launcher `.html` exports in `.hemtt/presets/`.
- HEMTT's implicit `-skipIntro -noSplash -showScriptErrors -debug` and its `-filePatching`
  default are applied to client 2 too.

An unresolvable workshop id is a warning naming the id and its trailing comment
(`3372876344  # Ace View`), not an error — the run continues without it.

Use `a3rig run --dry-run` to see the exact arguments.

### Supported `launch.toml` keys

`extends`, `workshop`, `presets`, `dlc`, `optionals`, `mission`, `dev_mission`,
`parameters`, `executable`, `file_patching`, `binarize`, `rapify`, `instances`. Profiles may
live in `.hemtt/launch.toml` or under `[hemtt.launch]` in `.hemtt/project.toml`; both are
read, with `launch.toml` winning. `extends` is resolved recursively — arrays concatenate,
scalars override — and a cycle is reported by name rather than hanging.

`-c` accepts HEMTT's modifiers too: `-c default -c +ws` for a CDLC, `-c @adt` for a profile
from HEMTT's global config.

## Development

```console
$ py -3.12 -m venv .venv
$ .\.venv\Scripts\python -m pip install -e ".[dev]"
$ .\.venv\Scripts\python -m pytest
```

Tests cover the platform-independent logic: `launch.toml` parsing including `extends` and
cycles, mod-argument building, config merge precedence, VDF library parsing, preset
extraction, BattlEye detection and patching, and session/tail behaviour.

Module layout: `cli.py` (commands), `config.py` (merging), `hemtt.py` (launch.toml and mod
resolution), `paths.py` (Steam/Arma detection), `preflight.py` (checks and command-line
building), `servercfg.py`, `processes.py`, `tail.py`.

## Releasing

Bump `__version__` in [src/a3rig/\_\_init\_\_.py](src/a3rig/__init__.py), then:

```console
$ git tag v0.2.0 && git push origin v0.2.0
```

[`.github/workflows/release.yml`](.github/workflows/release.yml) then runs the tests,
freezes a standalone `a3rig.exe` with PyInstaller, zips it, publishes a GitHub release,
and attaches `winget-manifests.zip` with the SHA256 already filled in. The workflow fails
the build if the tag and `__version__` disagree.

To build locally instead:

```console
$ .\.venv\Scripts\python -m pip install -e ".[dev,build]"
$ .\.venv\Scripts\python -m PyInstaller packaging/a3rig.spec --noconfirm
```

### Publishing to winget

The release must be published **first** — winget's validation downloads the installer URL
and checks it against the `InstallerSha256` in the manifest.

**First release.** Use Microsoft's own tool; it forks winget-pkgs and opens the PR for you:

```console
$ winget install Microsoft.WingetCreate
```

Download `winget-manifests.zip` from the GitHub release and extract it, then:

```console
$ winget validate --manifest .\winget-manifests
$ wingetcreate submit --prtitle "New package: DiGii.A3Rig version 0.1.0" --token <PAT> .\winget-manifests
```

The token is a GitHub [personal access token](https://github.com/settings/tokens) with the
`public_repo` scope — it only needs to fork a public repo and push a branch.

After that an automated pipeline validates the manifest and installs the package in a
sandbox, then a maintainer reviews it. Once merged, `winget install DiGii.A3Rig` works for
everyone.

**Later releases** — once `DiGii.A3Rig` exists in winget-pkgs, add
[WinGet Releaser](https://github.com/vedantmgoyal9/winget-releaser) to the workflow to
open the version-bump PR automatically. It needs a personal access token with `public_repo`
stored as a repository secret; that is why it is not wired up already.

The package is `zip` + nested `portable`, the same shape HEMTT uses: winget unpacks the
archive and puts `a3rig.exe` on PATH through its own shim directory.
