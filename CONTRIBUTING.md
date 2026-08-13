# Contributing to a3rig

Maintainer notes. For using the tool, see the [README](README.md).

## Development

```console
$ py -3.12 -m venv .venv
$ .\.venv\Scripts\python -m pip install -e ".[dev]"
$ .\.venv\Scripts\python -m pytest
```

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs the same suite on every push to
`main` and on pull requests, against **3.11 and 3.12**. 3.11 is in the matrix because
`requires-python = ">=3.11"` is a promise, and day-to-day development happens on 3.12.

Tests cover the platform-independent logic: `launch.toml` parsing including `extends` and
cycles, mod-argument building, config merge precedence, VDF library parsing, preset
extraction, server-config detection and patching, and session/tail behaviour.

Module layout: `cli.py` (commands), `config.py` (merging), `hemtt.py` (launch.toml and mod
resolution), `paths.py` (Steam/Arma detection), `preflight.py` (checks and command-line
building), `servercfg.py`, `processes.py`, `tail.py`.

### Mod-list parity is the invariant

Client 2 and the dedicated server must receive byte-identical `-mod=` arguments to the
client HEMTT launches, or the three processes can disagree about which config wins. The
rules in `hemtt.build_mod_args` mirror HEMTT 1.19 exactly — DLC first in enum order, then
paths sorted and deduplicated, values wrapped in literal quotes, one argument per mod.
`tests/test_mods.py` pins each of those; treat a failure there as a real regression rather
than a stale expectation.

To re-check against HEMTT itself, run `hemtt launch --dry-run` (a hidden flag) in a project
and compare its `-mod=` arguments with `a3rig run --dry-run`.

## Releasing

Bump `__version__` in [src/a3rig/\_\_init\_\_.py](src/a3rig/__init__.py), then:

```console
$ git tag v0.2.0 && git push origin v0.2.0
```

[`.github/workflows/release.yml`](.github/workflows/release.yml) runs the tests, freezes a
standalone `a3rig.exe` with PyInstaller, zips it, publishes a GitHub release, and opens the
winget-pkgs version-bump PR. The workflow fails the build if the tag and `__version__`
disagree.

To build locally instead:

```console
$ .\.venv\Scripts\python -m pip install -e ".[dev,build]"
$ .\.venv\Scripts\python -m PyInstaller packaging/a3rig.spec --noconfirm
```

The spec builds one-*dir*, not one-file, and leaves UPX off. A one-file build unpacks
itself to `%TEMP%` on every run — slower to start, and far more likely to trip antivirus
heuristics on an unsigned binary.

## Publishing to winget

`DiGii.A3Rig` is already in
[winget-pkgs](https://github.com/microsoft/winget-pkgs/tree/master/manifests/d/DiGii/A3Rig),
so only version bumps are needed from here.

### Automated

The `winget` job runs [WinGet Releaser](https://github.com/vedantmgoyal9/winget-releaser)
after the GitHub release exists; it generates manifests with Komac and opens the PR.

It needs one repository secret, **`WINGET_TOKEN`**: a
[personal access token](https://github.com/settings/tokens), *Tokens (classic)*, scope
`public_repo`. Fine-grained tokens cannot fork a repository you do not own, and the
built-in `GITHUB_TOKEN` is scoped to this repository only, so neither works. Without the
secret the job logs a notice and skips, leaving the release itself green.

Two details worth preserving if you edit the workflow:

- `installers-regex` is anchored to `^a3rig-windows-x64\.zip$`. The release also carries
  `winget-manifests.zip`, so a loose `\.zip$` would match both — while the action's own
  default (`exe|msi|msix|appx`) matches neither.
- The submission is a job in the release workflow, not a workflow triggered by
  `on: release`. GitHub does not fire workflow events for releases created with
  `GITHUB_TOKEN`, so a release-triggered workflow would silently never run.

### By hand

No token needed — omitting `--token` starts a browser sign-in, which also keeps it out of
your shell history. Use this for a brand-new package, or if the automated job fails.

The release must exist first: winget's validation downloads the installer URL and checks it
against the `InstallerSha256`. Every release attaches `winget-manifests.zip` with the hash
already filled in, so:

```console
$ winget install Microsoft.WingetCreate
$ winget validate --manifest .\winget-manifests
$ wingetcreate submit --prtitle "New package: DiGii.A3Rig version 0.1.0" .\winget-manifests
```

`wingetcreate` forks winget-pkgs and opens the PR for you. A moderator reviews every
submission, so merges take anywhere from an hour to a few days.
