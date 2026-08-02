"""Steam / Arma 3 discovery, plus the small VDF parser it needs.

Everything Windows-specific (``winreg``) is guarded so this module imports and its pure
logic stays testable on other platforms.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import ARMA3_APP_ID
from .errors import A3RigError

IS_WINDOWS = sys.platform == "win32"

CLIENT_EXE = "arma3_x64.exe"
SERVER_EXE = "arma3server_x64.exe"

#: Steam application id for the standalone Arma 3 dedicated server.
ARMA3_SERVER_APP_ID = "233780"


# --------------------------------------------------------------------------------------
# VDF
# --------------------------------------------------------------------------------------

_VDF_TOKEN = re.compile(
    r"""
    "((?:[^"\\]|\\.)*)"     # quoted string, backslash escapes
    | (\{|\})               # block delimiters
    | //[^\n]*              # line comment
    | \s+                   # whitespace
    | (\S+)                 # bare token (Valve allows unquoted keys)
    """,
    re.VERBOSE,
)

_VDF_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}


def _unescape(raw: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch == "\\" and i + 1 < len(raw):
            nxt = raw[i + 1]
            out.append(_VDF_ESCAPES.get(nxt, nxt))
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def parse_vdf(text: str) -> dict:
    """Parse Valve KeyValues text into nested dicts.

    Duplicate keys keep the last value, matching Steam's own behaviour.
    """
    tokens: list[str | None] = []
    for match in _VDF_TOKEN.finditer(text):
        quoted, delim, bare = match.group(1), match.group(2), match.group(3)
        if quoted is not None:
            tokens.append(_unescape(quoted))
        elif delim is not None:
            tokens.append(delim)
        elif bare is not None:
            tokens.append(bare)

    pos = 0

    def parse_block() -> dict:
        nonlocal pos
        block: dict = {}
        while pos < len(tokens):
            token = tokens[pos]
            if token == "}":
                pos += 1
                return block
            pos += 1
            if pos >= len(tokens):
                break  # trailing key with no value
            value = tokens[pos]
            if value == "{":
                pos += 1
                block[token] = parse_block()
            else:
                pos += 1
                block[token] = value
        return block

    root: dict = {}
    while pos < len(tokens):
        key = tokens[pos]
        if key in ("{", "}"):
            pos += 1
            continue
        pos += 1
        if pos >= len(tokens):
            break
        value = tokens[pos]
        if value == "{":
            pos += 1
            root[key] = parse_block()
        else:
            pos += 1
            root[key] = value
    return root


@dataclass
class SteamLibrary:
    """One Steam library folder and the app ids it holds."""

    path: Path
    apps: set[str] = field(default_factory=set)

    @property
    def common(self) -> Path:
        return self.path / "steamapps" / "common"

    @property
    def workshop(self) -> Path:
        return self.path / "steamapps" / "workshop" / "content" / ARMA3_APP_ID


def parse_library_folders(text: str) -> list[SteamLibrary]:
    """Extract library folders from a ``libraryfolders.vdf`` document.

    Handles both the modern schema (each entry a block with ``path``/``apps``) and the
    pre-2021 schema (each entry just a path string).
    """
    data = parse_vdf(text)
    root = data.get("libraryfolders") or data.get("LibraryFolders") or {}
    libraries: list[SteamLibrary] = []
    for key, value in root.items():
        if not key.isdigit():
            continue
        if isinstance(value, dict):
            raw_path = value.get("path")
            if not raw_path:
                continue
            apps = value.get("apps")
            app_ids = set(apps.keys()) if isinstance(apps, dict) else set()
            libraries.append(SteamLibrary(Path(raw_path), app_ids))
        elif isinstance(value, str):
            libraries.append(SteamLibrary(Path(value)))
    return libraries


# --------------------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------------------


def _read_registry(hive: str, subkey: str, value: str) -> str | None:
    if not IS_WINDOWS:
        return None
    import winreg

    hives = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}
    try:
        with winreg.OpenKey(hives[hive], subkey) as key:
            data, _ = winreg.QueryValueEx(key, value)
    except OSError:
        return None
    return str(data) if data else None


def find_steam_root() -> Path | None:
    """Locate the Steam installation."""
    for hive, subkey, value in (
        ("HKCU", r"Software\Valve\Steam", "SteamPath"),
        ("HKLM", r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        ("HKLM", r"SOFTWARE\Valve\Steam", "InstallPath"),
    ):
        raw = _read_registry(hive, subkey, value)
        if raw:
            path = Path(raw)
            if path.is_dir():
                return path
    for candidate in (
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Steam",
        Path.home() / ".steam" / "steam",
    ):
        if candidate.is_dir():
            return candidate
    return None


def find_steam_libraries(steam_root: Path | None = None) -> list[SteamLibrary]:
    """All Steam library folders, the Steam root itself included."""
    steam_root = steam_root or find_steam_root()
    if steam_root is None:
        return []
    vdf = steam_root / "steamapps" / "libraryfolders.vdf"
    libraries: list[SteamLibrary] = []
    if vdf.is_file():
        try:
            libraries = parse_library_folders(vdf.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            libraries = []
    known = {lib.path.resolve() for lib in libraries if lib.path.exists()}
    if steam_root.resolve() not in known:
        libraries.insert(0, SteamLibrary(steam_root))
    return [lib for lib in libraries if lib.path.is_dir()]


# --------------------------------------------------------------------------------------
# Arma 3
# --------------------------------------------------------------------------------------


@dataclass
class ArmaPaths:
    """Everything resolved about the local Arma 3 installation."""

    client_dir: Path
    client_exe: Path
    server_exe: Path | None
    workshop: Path | None
    steam_root: Path | None
    libraries: list[SteamLibrary] = field(default_factory=list)
    #: True when the dedicated server binary is the one bundled with the client.
    server_is_bundled: bool = False
    notes: list[str] = field(default_factory=list)


def _dir_from_registry(subkey: str) -> Path | None:
    raw = _read_registry("HKLM", rf"SOFTWARE\WOW6432Node\bohemia interactive\{subkey}", "main")
    if raw is None:
        raw = _read_registry("HKLM", rf"SOFTWARE\bohemia interactive\{subkey}", "main")
    if raw:
        path = Path(raw)
        if path.is_dir():
            return path
    return None


def find_arma_client_dir(libraries: list[SteamLibrary]) -> Path | None:
    from_registry = _dir_from_registry("arma 3")
    if from_registry and (from_registry / CLIENT_EXE).is_file():
        return from_registry
    for library in libraries:
        candidate = library.common / "Arma 3"
        if (candidate / CLIENT_EXE).is_file():
            return candidate
    return from_registry


def find_arma_server_exe(libraries: list[SteamLibrary], client_dir: Path | None) -> tuple[Path | None, bool]:
    """Locate ``arma3server_x64.exe``.

    Returns the executable and whether it came from the client install rather than the
    standalone dedicated-server app.
    """
    from_registry = _dir_from_registry("arma 3 server")
    if from_registry and (from_registry / SERVER_EXE).is_file():
        return from_registry / SERVER_EXE, False
    for library in libraries:
        candidate = library.common / "Arma 3 Server" / SERVER_EXE
        if candidate.is_file():
            return candidate, False
    if client_dir is not None and (client_dir / SERVER_EXE).is_file():
        return client_dir / SERVER_EXE, True
    return None, False


def find_workshop_dir(libraries: list[SteamLibrary], client_dir: Path | None) -> tuple[Path | None, list[str]]:
    """Locate the Arma 3 workshop content folder.

    HEMTT only ever looks in ``<arma3>/../../workshop/content/107410``, so that is checked
    first; other libraries are a fallback and are reported, because a mod list resolved
    from a different library would not match what HEMTT gave client 1.
    """
    notes: list[str] = []
    hemtt_choice: Path | None = None
    if client_dir is not None:
        hemtt_choice = client_dir.parent.parent / "workshop" / "content" / ARMA3_APP_ID
        if hemtt_choice.is_dir():
            return hemtt_choice, notes
    for library in libraries:
        if library.workshop.is_dir():
            if hemtt_choice is not None:
                notes.append(
                    f"workshop content resolved to {library.workshop}, but HEMTT looks in "
                    f"{hemtt_choice} - mod paths may differ from client 1"
                )
            return library.workshop, notes
    return None, notes


def detect_arma_paths(
    client_override: Path | None = None,
    server_override: Path | None = None,
    workshop_override: Path | None = None,
) -> ArmaPaths:
    """Resolve the Arma 3 install, honouring config overrides first.

    Overrides may point at either the install directory or the executable itself.
    """
    steam_root = find_steam_root()
    libraries = find_steam_libraries(steam_root)
    notes: list[str] = []

    if client_override is not None:
        client_dir = client_override if client_override.is_dir() else client_override.parent
    else:
        client_dir = find_arma_client_dir(libraries)

    if client_dir is None:
        raise A3RigError(
            "Arma 3 installation not found",
            "searched the registry (HKLM\\SOFTWARE\\WOW6432Node\\bohemia interactive\\arma 3) "
            f"and {len(libraries)} Steam librar{'y' if len(libraries) == 1 else 'ies'}",
            'set [paths] arma3 = "D:/.../steamapps/common/Arma 3" in the a3rig config '
            "(a3rig config --path)",
        )

    client_exe = client_dir / CLIENT_EXE
    if client_override is not None and client_override.is_file():
        client_exe = client_override
    if not client_exe.is_file():
        raise A3RigError(
            f"{CLIENT_EXE} not found",
            str(client_exe),
            "check [paths] arma3 in the a3rig config, or verify the Arma 3 install",
        )

    if server_override is not None:
        server_exe: Path | None = (
            server_override if server_override.is_file() else server_override / SERVER_EXE
        )
        server_is_bundled = False
        if server_exe is not None and not server_exe.is_file():
            raise A3RigError(
                f"{SERVER_EXE} not found",
                f"{server_exe} (from [paths] arma3_server)",
                "point [paths] arma3_server at the server install directory or executable",
            )
    else:
        server_exe, server_is_bundled = find_arma_server_exe(libraries, client_dir)
        if server_is_bundled:
            notes.append(
                "using the arma3server_x64.exe bundled with the client - the standalone "
                "Arma 3 Dedicated Server app (233780) is not installed"
            )

    if workshop_override is not None:
        workshop: Path | None = workshop_override
        if not workshop.is_dir():
            raise A3RigError(
                "workshop directory not found",
                f"{workshop} (from [paths] workshop)",
                "point [paths] workshop at <library>/steamapps/workshop/content/107410",
            )
    else:
        workshop, workshop_notes = find_workshop_dir(libraries, client_dir)
        notes.extend(workshop_notes)

    return ArmaPaths(
        client_dir=client_dir,
        client_exe=client_exe,
        server_exe=server_exe,
        workshop=workshop,
        steam_root=steam_root,
        libraries=libraries,
        server_is_bundled=server_is_bundled,
        notes=notes,
    )
