"""HEMTT project discovery, launch-profile resolution and mod-list building.

The goal throughout is parity with `hemtt launch`: client 2 and the dedicated server must
end up with byte-identical `-mod=` arguments to the client HEMTT starts, otherwise the
three processes can disagree about which config wins. Behaviour here mirrors
``bin/src/commands/launch/launcher.rs`` in HEMTT 1.19.1.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import A3RigError

#: Parameters HEMTT puts in front of the profile's own, unconditionally.
HEMTT_IMPLICIT_PARAMETERS = ["-skipIntro", "-noSplash", "-showScriptErrors", "-debug"]

#: Fields that concatenate when profiles are merged. Everything else overrides.
ARRAY_FIELDS = ("workshop", "dlc", "presets", "optionals", "parameters")

#: DLC identifiers accepted in ``dlc = [...]``, mapped to the ``-mod=`` folder HEMTT emits.
#: The dict order is HEMTT's enum declaration order, which is also its sort order.
DLC_ALIASES: dict[str, tuple[int, str]] = {}
for _index, (_mod, _aliases) in enumerate(
    [
        ("contact", ["1021790", "contact"]),
        ("gm", ["1042220", "gm", "global mobilization", "global mobilization - cold war germany"]),
        ("vn", ["1227700", "vn", "sog", "prairie fire", "s.o.g. prairie fire"]),
        ("csla", ["1294440", "csla", "iron curtain", "csla iron curtain"]),
        ("ws", ["1681170", "ws", "western sahara"]),
        ("spe", ["1175380", "spe", "spearhead", "spearhead 1944"]),
        ("rf", ["2647760", "rf", "reaction forces"]),
        ("ef", ["2647830", "ef", "expeditionary forces"]),
    ]
):
    for _alias in _aliases:
        DLC_ALIASES[_alias] = (_index, _mod)

_PRESET_MOD_RE = re.compile(r'href="https?://steamcommunity\.com/sharedfiles/filedetails/\?id=(\d+)"')
_PRESET_DLC_RE = re.compile(r'href="https?://store\.steampowered\.com/app/(\d+)"')
_PUBLISHED_ID_RE = re.compile(r"publishedid\s*=\s*(\d+)\s*;", re.IGNORECASE)
#: A quoted list entry followed by a trailing comment, used only to enrich warnings.
_ENTRY_COMMENT_RE = re.compile(r'"([^"]+)"\s*,?\s*#\s*(.+?)\s*$', re.MULTILINE)


def resolve_dlc(name: str) -> tuple[int, str] | None:
    """Map a DLC name, short code or app id to ``(sort key, -mod folder)``."""
    key = name.strip().lower()
    if key.startswith("creator dlc: "):
        key = key[len("creator dlc: ") :]
    return DLC_ALIASES.get(key)


# --------------------------------------------------------------------------------------
# Project
# --------------------------------------------------------------------------------------


@dataclass
class Project:
    """A HEMTT project rooted at the folder containing ``.hemtt/project.toml``."""

    root: Path
    name: str
    prefix: str
    mainprefix: str
    published_id: str | None

    @property
    def dev_build(self) -> Path:
        return self.root / ".hemttout" / "dev"

    @property
    def launch_toml(self) -> Path:
        return self.root / ".hemtt" / "launch.toml"

    @property
    def project_toml(self) -> Path:
        return self.root / ".hemtt" / "project.toml"


def find_project_root(start: Path | None = None) -> Path:
    """Walk up from ``start`` looking for ``.hemtt/project.toml``."""
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".hemtt" / "project.toml").is_file():
            return candidate
    raise A3RigError(
        "not inside a HEMTT project",
        f"no .hemtt/project.toml found in {current} or any parent directory",
        "cd into a mod project, or run `hemtt new` to create one",
    )


def load_project(root: Path | None = None) -> Project:
    root = root or find_project_root()
    data = _read_toml(root / ".hemtt" / "project.toml")
    published_id = None
    meta = root / "meta.cpp"
    if meta.is_file():
        match = _PUBLISHED_ID_RE.search(meta.read_text(encoding="utf-8", errors="replace"))
        if match:
            published_id = match.group(1)
    return Project(
        root=root,
        name=str(data.get("name", root.name)),
        prefix=str(data.get("prefix", "")),
        mainprefix=str(data.get("mainprefix", "z")),
        published_id=published_id,
    )


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        # utf-8-sig so a BOM written by a Windows editor does not fail the parse.
        return tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except tomllib.TOMLDecodeError as exc:
        raise A3RigError("TOML file could not be parsed", f"{path}: {exc}", "fix the syntax") from exc
    except OSError as exc:
        raise A3RigError("file could not be read", f"{path}: {exc}") from exc


def find_hemtt_executable(configured: str = "hemtt") -> Path:
    """Resolve the hemtt binary from the config value or PATH."""
    candidate = Path(configured).expanduser()
    if candidate.is_file():
        return candidate
    found = shutil.which(configured)
    if found:
        return Path(found)
    raise A3RigError(
        "hemtt not found",
        f"{configured!r} is not on PATH and is not a file",
        "install HEMTT (https://hemtt.dev) or set [paths] hemtt in the a3rig config",
    )


# --------------------------------------------------------------------------------------
# Launch profiles
# --------------------------------------------------------------------------------------


@dataclass
class LaunchProfile:
    """A resolved launch profile. Mirrors HEMTT's ``LaunchOptions``."""

    workshop: list[str] = field(default_factory=list)
    dlc: list[str] = field(default_factory=list)
    presets: list[str] = field(default_factory=list)
    optionals: list[str] = field(default_factory=list)
    parameters: list[str] = field(default_factory=list)
    mission: str | None = None
    executable: str | None = None
    file_patching: bool | None = None
    binarize: bool | None = None
    rapify: bool | None = None
    instances: int | None = None

    @property
    def uses_file_patching(self) -> bool:
        return True if self.file_patching is None else self.file_patching

    @property
    def uses_binarize(self) -> bool:
        return False if self.binarize is None else self.binarize

    @property
    def uses_rapify(self) -> bool:
        return True if self.rapify is None else self.rapify

    @property
    def client_executable(self) -> str:
        return self.executable or "arma3_x64"


def _as_str_list(value: Any, key: str, profile: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    raise A3RigError(
        f"launch profile {profile!r}: `{key}` must be a list",
        repr(value),
        f"write it as {key} = [ ... ]",
    )


def profile_from_table(table: dict[str, Any], name: str) -> LaunchProfile:
    """Build a profile from one raw launch.toml table (``extends`` already resolved)."""
    return LaunchProfile(
        workshop=_as_str_list(table.get("workshop"), "workshop", name),
        dlc=_as_str_list(table.get("dlc"), "dlc", name),
        presets=_as_str_list(table.get("presets"), "presets", name),
        optionals=_as_str_list(table.get("optionals"), "optionals", name),
        parameters=_as_str_list(table.get("parameters"), "parameters", name),
        mission=table.get("mission") or table.get("dev_mission"),
        executable=table.get("executable"),
        file_patching=table.get("file_patching"),
        binarize=table.get("binarize"),
        rapify=table.get("rapify"),
        instances=table.get("instances"),
    )


def overlay(base: LaunchProfile, other: LaunchProfile) -> LaunchProfile:
    """HEMTT's ``LaunchOptions::overlay``: arrays concatenate, scalars override."""
    merged = LaunchProfile(
        workshop=[*base.workshop, *other.workshop],
        dlc=[*base.dlc, *other.dlc],
        presets=[*base.presets, *other.presets],
        optionals=[*base.optionals, *other.optionals],
        parameters=[*base.parameters, *other.parameters],
    )
    for scalar in ("mission", "executable", "file_patching", "binarize", "rapify", "instances"):
        value = getattr(other, scalar)
        setattr(merged, scalar, getattr(base, scalar) if value is None else value)
    return merged


def load_profile_tables(project: Project) -> dict[str, dict[str, Any]]:
    """Raw launch tables from ``.hemtt/launch.toml``, then ``[hemtt.launch]``.

    Both locations are supported; launch.toml wins for profiles defined in both.
    """
    tables: dict[str, dict[str, Any]] = {}
    project_data = _read_toml(project.project_toml)
    from_project = project_data.get("hemtt", {}).get("launch", {})
    if isinstance(from_project, dict):
        tables.update({k: v for k, v in from_project.items() if isinstance(v, dict)})
    if project.launch_toml.is_file():
        from_launch = _read_toml(project.launch_toml)
        tables.update({k: v for k, v in from_launch.items() if isinstance(v, dict)})
    return tables


def resolve_extends(
    name: str,
    tables: dict[str, dict[str, Any]],
    _chain: tuple[str, ...] = (),
) -> LaunchProfile:
    """Resolve one named profile, following ``extends`` up the chain.

    HEMTT does not guard against a cycle here, so we do — an infinite loop would be a
    much worse error message than naming the loop.
    """
    if name in _chain:
        loop = " -> ".join([*_chain, name])
        raise A3RigError(
            "launch profile `extends` forms a cycle",
            loop,
            "break the loop by removing one of the `extends` keys",
        )
    table = tables.get(name)
    if table is None:
        raise A3RigError(
            f"launch profile {name!r} not found",
            f"available: {', '.join(sorted(tables)) or '(none)'}",
            "check --launch-config, or the [launch] config key in the a3rig config",
        )
    profile = profile_from_table(table, name)
    parent_name = table.get("extends")
    if parent_name:
        parent = resolve_extends(str(parent_name), tables, (*_chain, name))
        profile = overlay(parent, profile)
    return profile


def resolve_launch_profile(
    names: list[str],
    tables: dict[str, dict[str, Any]],
    global_profiles: dict[str, dict[str, Any]] | None = None,
) -> LaunchProfile:
    """Resolve a chain of profile names the way ``hemtt launch a b c`` does.

    Names are overlaid left to right. ``+code`` adds a CDLC, ``@name`` pulls a profile
    from HEMTT's global config. When every name is a ``+``/``@`` modifier, the project's
    ``default`` profile is used as the base.
    """
    global_profiles = global_profiles or {}
    if not names:
        names = ["default"]

    only_modifiers = all(name.startswith(("+", "@")) for name in names)
    if only_modifiers and "default" in tables:
        result = resolve_extends("default", tables)
    else:
        result = LaunchProfile()

    for name in names:
        if name.startswith("@"):
            key = name[1:]
            table = global_profiles.get(key)
            if table is None:
                raise A3RigError(
                    f"global launch profile {name!r} not found",
                    f"available in HEMTT's global config: {', '.join(sorted(global_profiles)) or '(none)'}",
                    f"add [launch.profiles.{key}] to {hemtt_global_config_path()}",
                )
            result = overlay(result, profile_from_table(table, name))
        elif name.startswith("+"):
            code = name[1:]
            if code == "ace_arsenal":
                result = overlay(result, LaunchProfile(mission="ace_arsenal"))
                continue
            if resolve_dlc(code) is None:
                raise A3RigError(
                    f"unknown CDLC {name!r}",
                    f"known codes: {', '.join(sorted({m for _, m in DLC_ALIASES.values()}))}",
                    "use one of the codes above, e.g. +ws",
                )
            result = overlay(result, LaunchProfile(dlc=[code]))
        else:
            result = overlay(result, resolve_extends(name, tables))
    return result


def read_entry_comments(*paths: Path) -> dict[str, str]:
    """Map quoted list entries to their trailing ``#`` comment.

    ``tomllib`` discards comments, and tomlkit only exposes per-item comments through
    private attributes, so the raw text is scanned instead. The result is used purely to
    make "workshop mod not found" warnings readable, so a coarse file-wide map is enough.
    """
    comments: dict[str, str] = {}
    for path in paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for entry, comment in _ENTRY_COMMENT_RE.findall(text):
            comments.setdefault(entry, comment.strip())
    return comments


def read_presets(project: Project, profile: LaunchProfile) -> tuple[list[str], list[str], list[str]]:
    """Expand ``presets`` into workshop ids and DLCs. Returns ``(ids, dlcs, warnings)``."""
    ids: list[str] = []
    dlcs: list[str] = []
    warnings: list[str] = []
    preset_dir = project.root / ".hemtt" / "presets"
    for name in profile.presets:
        html_path = preset_dir / f"{name}.html"
        if not html_path.is_file():
            warnings.append(f"preset {name!r} not found at {html_path}")
            continue
        html = html_path.read_text(encoding="utf-8", errors="replace")
        for mod_id in _PRESET_MOD_RE.findall(html):
            if mod_id not in ids:
                ids.append(mod_id)
        for app_id in _PRESET_DLC_RE.findall(html):
            if resolve_dlc(app_id) is None:
                warnings.append(f"preset {name!r} requires unrecognised DLC app {app_id}")
                continue
            if app_id not in dlcs:
                dlcs.append(app_id)
    return ids, dlcs, warnings


# --------------------------------------------------------------------------------------
# HEMTT's own global config (pointers and global profiles)
# --------------------------------------------------------------------------------------


def hemtt_global_config_path() -> Path:
    """``%APPDATA%\\hemtt\\config.toml`` on Windows, XDG equivalents elsewhere."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "hemtt" / "config.toml"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "hemtt" / "config.toml"
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / "hemtt" / "config.toml"


@dataclass
class HemttGlobalConfig:
    """The parts of HEMTT's global config that change how mods resolve."""

    pointers: dict[str, Path] = field(default_factory=dict)
    profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    path: Path | None = None

    @classmethod
    def load(cls, path: Path | None = None) -> "HemttGlobalConfig":
        path = path or hemtt_global_config_path()
        if not path.is_file():
            return cls()
        data = _read_toml(path)
        launch = data.get("launch", {})
        if not isinstance(launch, dict):
            return cls(path=path)
        raw_pointers = launch.get("pointers", {})
        pointers = {
            str(key): Path(str(value))
            for key, value in (raw_pointers.items() if isinstance(raw_pointers, dict) else [])
        }
        raw_profiles = launch.get("profiles", {})
        profiles = {
            str(key): value
            for key, value in (raw_profiles.items() if isinstance(raw_profiles, dict) else [])
            if isinstance(value, dict)
        }
        return cls(pointers=pointers, profiles=profiles, path=path)


# --------------------------------------------------------------------------------------
# Mod resolution
# --------------------------------------------------------------------------------------


@dataclass
class ResolvedMod:
    """One entry from ``workshop`` after resolution."""

    entry: str
    path: Path | None
    source: str  # "workshop" | "pointer" | "pointer-prefix" | "dev" | "self"
    comment: str | None = None
    problem: str | None = None

    @property
    def ok(self) -> bool:
        return self.path is not None and self.problem is None

    def describe(self) -> str:
        label = self.entry if not self.comment else f"{self.entry}  # {self.comment}"
        return label


def resolve_mods(
    entries: list[str],
    workshop_root: Path | None,
    pointers: dict[str, Path],
    published_id: str | None = None,
    comments: dict[str, str] | None = None,
) -> list[ResolvedMod]:
    """Resolve workshop entries to absolute paths, in HEMTT's precedence order.

    Order per ``launcher.rs``: skip the project's own published id, then an exact pointer
    match, then a ``pointer:@mod`` prefix, then the workshop folder.
    """
    comments = comments or {}
    resolved: list[ResolvedMod] = []
    for entry in entries:
        comment = comments.get(entry)

        if published_id is not None and entry == published_id:
            resolved.append(
                ResolvedMod(entry, None, "self", comment, "same as this project's meta.cpp publishedid")
            )
            continue

        pointer_path = pointers.get(entry)
        if pointer_path is not None:
            problem = None if pointer_path.exists() else f"pointer target missing: {pointer_path}"
            resolved.append(
                ResolvedMod(entry, pointer_path if problem is None else None, "pointer", comment, problem)
            )
            continue

        if ":" in entry:
            pointer_name, _, sub_path = entry.partition(":")
            if len(pointer_name) > 1:  # >1 char so drive letters are not mistaken for pointers
                base = pointers.get(pointer_name)
                if base is None:
                    resolved.append(
                        ResolvedMod(entry, None, "pointer-prefix", comment, f"no pointer named {pointer_name!r}")
                    )
                else:
                    target = base / sub_path
                    problem = None if target.exists() else f"pointer target missing: {target}"
                    resolved.append(
                        ResolvedMod(
                            entry, target if problem is None else None, "pointer-prefix", comment, problem
                        )
                    )
                continue

        if workshop_root is None:
            resolved.append(ResolvedMod(entry, None, "workshop", comment, "workshop directory not found"))
            continue
        target = workshop_root / entry
        problem = None if target.is_dir() else f"not subscribed / not downloaded ({target})"
        resolved.append(ResolvedMod(entry, target if problem is None else None, "workshop", comment, problem))
    return resolved


def mod_argument(value: str | Path, flag: str = "-mod") -> str:
    """Format one ``-mod=``/``-serverMod=`` argument the way HEMTT does.

    The value is wrapped in literal quotes, so the argument reads ``-mod="C:\\path"``.
    That is byte-for-byte what HEMTT passes to Arma, and it matters here: essentially
    every workshop path contains spaces and parentheses (``C:\\Program Files (x86)\\...``),
    and this is the form already proven to work for the client HEMTT launches.
    """
    return f'{flag}="{value}"'


def build_mod_args(dlc: list[str], mod_paths: list[Path]) -> list[str]:
    """Build the ``-mod=`` arguments exactly as HEMTT orders them.

    DLCs first in HEMTT's enum order, then mod paths sorted and deduplicated as strings.
    One argument per mod — HEMTT does not join them with semicolons.
    """
    args: list[str] = []
    seen_dlc: set[str] = set()
    ranked: list[tuple[int, str]] = []
    for name in dlc:
        found = resolve_dlc(name)
        if found is None or found[1] in seen_dlc:
            continue
        seen_dlc.add(found[1])
        ranked.append(found)
    for _, folder in sorted(ranked):
        args.append(mod_argument(folder))

    for path in sorted({str(p) for p in mod_paths}):
        args.append(mod_argument(path))
    return args


def dedupe(values: list[str]) -> list[str]:
    """Drop repeated arguments, keeping the first occurrence."""
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out
