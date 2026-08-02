"""Configuration: built-in defaults, the global file, the per-project override, CLI flags.

Precedence, lowest to highest::

    DEFAULTS
    %APPDATA%\\a3rig\\config.toml
    <project root>/.hemtt/a3rig.toml
    CLI flags
"""

from __future__ import annotations

import copy
import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import APP_NAME
from .errors import A3RigError

#: Built-in defaults. Every key the tool reads exists here, so lookups never need a
#: fallback and ``a3rig config`` can always print a complete document.
DEFAULTS: dict[str, Any] = {
    "paths": {
        "arma3": "",
        "arma3_server": "",
        "workshop": "",
        "hemtt": "hemtt",
    },
    "launch": {
        "config": "default",
        "delay_after_hemtt": 25,
        "delay_after_client": 5,
    },
    "server": {
        "enabled": True,
        "port": 2302,
        "dir": "",
        "config": "",
        "cfg": "",
        "profiles": "",
        "name": "Server",
        "parameters": ["-filePatching", "-world=empty", "-noSound"],
        "server_mods": [],
    },
    "client2": {
        "profile": "Dev2",
        "parameters": ["-window", "-noSplash", "-skipIntro", "-filePatching", "-noLauncher"],
    },
}

# Raw string: the comments contain Windows paths, and `\a` in a plain literal would
# become a BELL character, which TOML rejects even inside a comment.
DEFAULT_CONFIG_TEMPLATE = r"""# a3rig configuration
#
# Every key is optional - anything omitted or left empty falls back to the built-in
# default shown here. A per-project override may be placed in
# <project root>/.hemtt/a3rig.toml and is deep-merged over this file.
# CLI flags override both.
#
# PATHS: use forward slashes - "D:/arma3-test". This is TOML, so a backslash inside
# double quotes is an escape character and "D:\arma3-test" is a syntax error.

[paths]
# All auto-detected when left empty. Point these somewhere only if detection is wrong,
# or if you have an install outside Steam.
arma3        = ""       # the CLIENT: Arma 3 install dir, or arma3_x64.exe itself
arma3_server = ""       # the SERVER: its install dir, or arma3server_x64.exe itself
workshop     = ""       # <library>/steamapps/workshop/content/107410
hemtt        = "hemtt"  # hemtt executable, resolved on PATH by default

[launch]
config             = "default"  # launch.toml profile; a list chains profiles, e.g. ["default", "ace"]
delay_after_hemtt  = 25         # seconds to wait after HEMTT's client appears, before client 2
delay_after_client = 5          # seconds to wait after client 2, before the server

[server]
enabled = true
port    = 2302

# The simplest setup: point `dir` at the folder holding your server files and leave the
# three keys below empty. a3rig then looks for:
#
#   <dir>/server.cfg   (or config.cfg)   -> -config=
#   <dir>/basic.cfg    (or network.cfg)  -> -cfg=
#   <dir>/profiles                       -> -profiles=   (Arma creates it if missing)
#
dir = ""  # e.g. "D:/arma3-test"

# Only needed to override what `dir` found, or if you are not using `dir` at all.
# Each may also be a folder, in which case the file is looked up inside it.
config   = ""  # -config=   : server.cfg, must contain `BattlEye = 0;`
cfg      = ""  # -cfg=      : basic.cfg (network tuning)
profiles = ""  # -profiles= : writable dir; the server .rpt is written here

name       = "Server"
parameters = ["-filePatching", "-world=empty", "-noSound"]

# Extra -serverMod= entries. Local paths or workshop IDs; server-side only.
server_mods = []

[client2]
profile    = "Dev2"  # -name= : keeps client 2 off your normal Arma profile
parameters = ["-window", "-noSplash", "-skipIntro", "-filePatching", "-noLauncher"]
"""


def global_config_dir() -> Path:
    """``%APPDATA%\\a3rig`` on Windows, an XDG-ish equivalent elsewhere."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / APP_NAME
        return Path.home() / "AppData" / "Roaming" / APP_NAME
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / APP_NAME


def global_config_path() -> Path:
    return global_config_dir() / "config.toml"


def state_dir() -> Path:
    """``%LOCALAPPDATA%\\a3rig`` - session state, not configuration."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / APP_NAME
        return Path.home() / "AppData" / "Local" / APP_NAME
    base = os.environ.get("XDG_STATE_HOME")
    return (Path(base) if base else Path.home() / ".local" / "state") / APP_NAME


def project_config_path(project_root: Path) -> Path:
    return project_root / ".hemtt" / f"{APP_NAME}.toml"


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``overlay`` onto ``base``, returning a new dict.

    Nested tables merge key by key; every other value, lists included, is replaced
    outright — a user who lists ``parameters`` means exactly that list.
    """
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        existing = result.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            result[key] = deep_merge(existing, value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def ensure_global_config() -> tuple[Path, bool]:
    """Create the global config with commented defaults if it is missing.

    Returns the path and whether it was just created.
    """
    path = global_config_path()
    if path.exists():
        return path, False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")
    return path, True


def _toml_fix_hint(exc: Exception) -> str:
    """Turn a TOML parse error into advice, naming the Windows path trap by name."""
    if "Unescaped '\\'" in str(exc):
        return (
            "a Windows path in double quotes needs forward slashes or doubled "
            'backslashes: "C:/Program Files (x86)/Steam" or "C:\\\\Program Files (x86)\\\\Steam". '
            "Single quotes also work verbatim: 'C:\\Program Files (x86)\\Steam'"
        )
    return "fix the TOML syntax, or delete the file to regenerate the defaults"


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        # utf-8-sig, not tomllib.load: several Windows editors (Notepad included) save a
        # UTF-8 BOM, which tomllib rejects as an invalid statement on line 1.
        return tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except tomllib.TOMLDecodeError as exc:
        raise A3RigError(
            "config file could not be parsed",
            f"{path}: {exc}",
            _toml_fix_hint(exc),
        ) from exc
    except OSError as exc:
        raise A3RigError("config file could not be read", f"{path}: {exc}") from exc


class Config:
    """The merged configuration, plus a record of where each layer came from."""

    def __init__(self, data: dict[str, Any], sources: list[Path]) -> None:
        self.data = data
        self.sources = sources

    def section(self, name: str) -> dict[str, Any]:
        value = self.data.get(name, {})
        return value if isinstance(value, dict) else {}

    def get(self, section: str, key: str, default: Any = None) -> Any:
        return self.section(section).get(key, default)

    def path(self, section: str, key: str) -> Path | None:
        """A configured path, or ``None`` when unset."""
        raw = self.get(section, key)
        if raw is None or str(raw).strip() == "":
            return None
        return Path(str(raw)).expanduser()

    def apply_overrides(self, overrides: dict[str, dict[str, Any]]) -> "Config":
        """Layer CLI flags on top. ``None`` values are ignored, so unset flags do nothing."""
        cleaned = {
            section: {k: v for k, v in values.items() if v is not None}
            for section, values in overrides.items()
        }
        cleaned = {section: values for section, values in cleaned.items() if values}
        if not cleaned:
            return self
        return Config(deep_merge(self.data, cleaned), self.sources)


def load_config(project_root: Path | None = None, create_global: bool = True) -> Config:
    """Load defaults, then the global file, then the project override."""
    sources: list[Path] = []
    data = copy.deepcopy(DEFAULTS)

    if create_global:
        ensure_global_config()
    global_path = global_config_path()
    if global_path.is_file():
        data = deep_merge(data, _read_toml(global_path))
        sources.append(global_path)

    if project_root is not None:
        local_path = project_config_path(project_root)
        if local_path.is_file():
            data = deep_merge(data, _read_toml(local_path))
            sources.append(local_path)

    return Config(data, sources)


#: Filenames looked for inside `[server] dir`, in order of preference.
SERVER_CONFIG_NAMES = ("server.cfg", "config.cfg")
SERVER_BASIC_NAMES = ("basic.cfg", "network.cfg")
PROFILES_DIRNAME = "profiles"


@dataclass
class ServerPaths:
    """The three `-config=` / `-cfg=` / `-profiles=` paths, however they were specified."""

    config: Path | None = None
    cfg: Path | None = None
    profiles: Path | None = None
    base: Path | None = None
    #: `(config key, message)` for anything that could not be resolved.
    problems: list[tuple[str, str]] = field(default_factory=list)


def _find_in_dir(directory: Path, names: tuple[str, ...]) -> Path | None:
    for name in names:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def resolve_server_paths(config: Config) -> ServerPaths:
    """Work out the server's three paths from `[server]`.

    `dir` is the convenient form: one folder holding `server.cfg`, `basic.cfg` and a
    `profiles` folder. An explicit `config`/`cfg`/`profiles` always wins over it, and
    `config`/`cfg` may themselves name a folder to look inside.
    """
    base = config.path("server", "dir")
    resolved = ServerPaths(base=base)

    if base is not None and not base.is_dir():
        resolved.problems.append(("dir", f"{base} is not a directory"))
        base = None

    for key, names, attribute in (
        ("config", SERVER_CONFIG_NAMES, "config"),
        ("cfg", SERVER_BASIC_NAMES, "cfg"),
    ):
        explicit = config.path("server", key)
        if explicit is not None and explicit.is_dir():
            # The user pointed the key at a folder rather than the file inside it.
            found = _find_in_dir(explicit, names)
            if found is None:
                resolved.problems.append((key, f"no {' or '.join(names)} in {explicit}"))
            setattr(resolved, attribute, found)
        elif explicit is not None:
            setattr(resolved, attribute, explicit)
        elif base is not None:
            found = _find_in_dir(base, names)
            if found is None:
                resolved.problems.append((key, f"no {' or '.join(names)} in {base}"))
            setattr(resolved, attribute, found)
        else:
            resolved.problems.append((key, "not configured"))

    profiles = config.path("server", "profiles")
    if profiles is None and base is not None:
        profiles = base / PROFILES_DIRNAME
    if profiles is None:
        resolved.problems.append(("profiles", "not configured"))
    resolved.profiles = profiles

    return resolved


def launch_config_names(config: Config) -> list[str]:
    """``[launch] config`` normalised to a list; a bare string means a single profile."""
    raw = config.get("launch", "config", "default")
    if isinstance(raw, str):
        names = [raw]
    elif isinstance(raw, list):
        names = [str(item) for item in raw]
    else:
        raise A3RigError(
            "[launch] config must be a string or a list of strings",
            repr(raw),
            'e.g. config = "default"  or  config = ["default", "ace"]',
        )
    names = [name for name in names if name.strip()]
    return names or ["default"]
