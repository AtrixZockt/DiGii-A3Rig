"""Inspection of the dedicated server's ``-config`` file.

Four settings decide whether a local two-client rig actually works, and every one of them
fails *silently* — the server starts fine and the problem only shows up as a client being
bounced, or file patching quietly not applying. So they are checked before anything is
launched.

Note the spelling of ``BattlEye``: eight letters, no second ``e``. Arma's config parser is
case-insensitive but not spelling-insensitive, so the common ``battleEye`` typo is ignored
outright and the server runs with BattlEye *enabled*. That trap is detected separately.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .errors import A3RigError

_LINE_COMMENT_RE = re.compile(r"//.*$")
#: The `battleEye` misspelling (nine letters), which Arma does not recognise.
_BATTLEYE_TYPO_RE = re.compile(r"\bbattleeye\s*=\s*(\d+)\s*;", re.IGNORECASE)


def _setting_re(key: str) -> re.Pattern[str]:
    """Match ``key = <number>;`` case-insensitively, as Arma's parser does."""
    return re.compile(rf"\b{key}\s*=\s*(\d+)\s*;", re.IGNORECASE)


@dataclass(frozen=True)
class Setting:
    """One server.cfg setting a3rig cares about, and why."""

    key: str
    wanted: int
    #: Canonical spelling written when patching.
    canonical: str
    #: What goes wrong when it is not `wanted`.
    consequence: str

    @property
    def pattern(self) -> re.Pattern[str]:
        return _setting_re(self.key)

    @property
    def assignment(self) -> str:
        return f"{self.canonical} = {self.wanted};"


BATTLEYE = Setting(
    "battleye",
    0,
    "BattlEye",
    "the server runs with BattlEye enabled, which interferes with a dev build",
)
LOOPBACK = Setting(
    "loopback",
    1,
    "loopback",
    "the server authenticates clients through Steam, so a second client on the same "
    "Steam account is kicked with `player with same SteamID already in game`",
)
KICK_DUPLICATE = Setting(
    "kickDuplicate",
    0,
    "kickDuplicate",
    "a second client sharing Arma's legacy player ID is kicked",
)
ALLOWED_FILE_PATCHING = Setting(
    "allowedFilePatching",
    2,
    "allowedFilePatching",
    "clients cannot use -filePatching against this server",
)

#: Everything scanned, in report order.
SETTINGS = (BATTLEYE, LOOPBACK, KICK_DUPLICATE, ALLOWED_FILE_PATCHING)


@dataclass
class Finding:
    """The state of one setting in the file."""

    setting: Setting
    line: int | None
    value: int | None

    @property
    def ok(self) -> bool:
        return self.value == self.setting.wanted

    @property
    def problem(self) -> str | None:
        """Why this setting is wrong, or ``None`` if it is fine."""
        if self.ok:
            return None
        if self.value is None:
            return f"no `{self.setting.canonical}` setting - {self.setting.consequence}"
        return (
            f"`{self.setting.canonical} = {self.value};` on line {self.line} - "
            f"{self.setting.consequence}"
        )

    def fix_hint(self, path: Path) -> str:
        if self.value is None:
            return f"add `{self.setting.assignment}` to {path}"
        return f"change line {self.line} of {path} to `{self.setting.assignment}`"


@dataclass
class ServerConfigScan:
    """What the ``-config`` file says about the settings a local rig depends on."""

    path: Path
    findings: dict[str, Finding]
    #: Line holding a `battleEye` misspelling, if any.
    typo_line: int | None = None

    def finding(self, setting: Setting) -> Finding:
        return self.findings[setting.key]

    # -- BattlEye keeps dedicated accessors: it is the one the spec calls out ----------

    @property
    def battleye_value(self) -> int | None:
        return self.finding(BATTLEYE).value

    @property
    def battleye_line(self) -> int | None:
        return self.finding(BATTLEYE).line

    @property
    def battleye_disabled(self) -> bool:
        return self.finding(BATTLEYE).ok

    @property
    def problem(self) -> str | None:
        """Why the server would start with BattlEye on, or ``None`` if it would not."""
        finding = self.finding(BATTLEYE)
        if finding.ok:
            return None
        if finding.value is None and self.typo_line is not None:
            return (
                f"line {self.typo_line} spells it `battleEye`, which Arma does not "
                "recognise - BattlEye stays enabled"
            )
        return finding.problem

    @property
    def fix_hint(self) -> str:
        finding = self.finding(BATTLEYE)
        if finding.value is None and self.typo_line is not None:
            return (
                f"change line {self.typo_line} of {self.path} to `{BATTLEYE.assignment}` "
                "(one `e`, capital B and E), or run with --fix-server-config"
            )
        return f"{finding.fix_hint(self.path)}, or run with --fix-server-config"

    @property
    def file_patching_note(self) -> str | None:
        """Non-fatal: clients need ``allowedFilePatching = 2;`` for -filePatching in MP."""
        finding = self.finding(ALLOWED_FILE_PATCHING)
        if finding.ok:
            return None
        if finding.value is None:
            return (
                "no `allowedFilePatching` setting - clients cannot use -filePatching "
                f"against this server; add `{ALLOWED_FILE_PATCHING.assignment}` to {self.path}"
            )
        return (
            f"`allowedFilePatching = {finding.value};` blocks -filePatching for clients; "
            f"set it to 2 in {self.path}"
        )

    @property
    def broken(self) -> list[Finding]:
        """Every setting that is not at its wanted value."""
        return [f for f in self.findings.values() if not f.ok]


def _strip_comment(line: str) -> str:
    return _LINE_COMMENT_RE.sub("", line)


def scan_server_config(path: Path) -> ServerConfigScan:
    """Read the server config and report the state of every setting a local rig needs."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise A3RigError(
            "server config could not be read",
            f"{path}: {exc}",
            "check [server] config in the a3rig config",
        ) from exc

    findings = {setting.key: Finding(setting, None, None) for setting in SETTINGS}
    typo_line: int | None = None

    for number, line in enumerate(text.splitlines(), start=1):
        code = _strip_comment(line)
        for setting in SETTINGS:
            finding = findings[setting.key]
            if finding.value is not None:
                continue  # first occurrence wins, as Arma's parser does
            match = setting.pattern.search(code)
            if match:
                finding.line = number
                finding.value = int(match.group(1))
        if typo_line is None and _BATTLEYE_TYPO_RE.search(code):
            typo_line = number

    return ServerConfigScan(path, findings, typo_line)


def patch_server_config(
    path: Path,
    settings: list[Setting] | None = None,
    scan: ServerConfigScan | None = None,
) -> list[str]:
    """Set the given settings to their wanted values, backing the file up first.

    Returns a description of each change. Callers must confirm with the user first.
    """
    scan = scan or scan_server_config(path)
    targets = settings if settings is not None else [f.setting for f in scan.broken]
    targets = [s for s in targets if not scan.finding(s).ok]
    if not targets:
        return []

    text = path.read_text(encoding="utf-8", errors="replace")
    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)
    newline = "\r\n" if "\r\n" in text else "\n"
    changes: list[str] = []

    for setting in targets:
        finding = scan.finding(setting)
        if finding.value is not None:
            lines = text.splitlines(keepends=True)
            index = (finding.line or 1) - 1
            lines[index] = setting.pattern.sub(setting.assignment, lines[index], count=1)
            text = "".join(lines)
            changes.append(f"line {finding.line}: {setting.assignment}")
        elif setting is BATTLEYE and scan.typo_line is not None:
            # Correct the misspelled key in place rather than leaving a dead line behind.
            lines = text.splitlines(keepends=True)
            index = scan.typo_line - 1
            lines[index] = _BATTLEYE_TYPO_RE.sub(setting.assignment, lines[index], count=1)
            text = "".join(lines)
            changes.append(f"line {scan.typo_line}: corrected misspelled key -> {setting.assignment}")
        else:
            separator = "" if text.endswith(("\n", "\r")) else newline
            text += f"{separator}{setting.assignment}{newline}"
            changes.append(f"appended {setting.assignment}")

    path.write_text(text, encoding="utf-8")
    changes.append(f"backup: {backup.name}")
    return changes


def patch_battleye(path: Path, scan: ServerConfigScan | None = None) -> str:
    """Disable BattlEye only. Kept for callers that just want the one setting."""
    scan = scan or scan_server_config(path)
    if scan.battleye_disabled:
        return "already disabled, nothing to do"
    changes = patch_server_config(path, [BATTLEYE], scan)
    return "; ".join(changes)
