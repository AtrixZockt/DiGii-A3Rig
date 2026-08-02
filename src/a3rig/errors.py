"""Expected failure modes.

Anything raised as an :class:`A3RigError` is a situation the user can fix, so the CLI
prints it as a panel and exits non-zero instead of dumping a traceback.
"""

from __future__ import annotations


class A3RigError(Exception):
    """An expected failure with an actionable message.

    :param title: one line naming what failed.
    :param detail: the offending path/value/output, if any.
    :param fix: what the user should do about it.
    """

    def __init__(self, title: str, detail: str | None = None, fix: str | None = None) -> None:
        self.title = title
        self.detail = detail
        self.fix = fix
        super().__init__(title if detail is None else f"{title}: {detail}")
