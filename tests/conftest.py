from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES


def write_project(
    root: Path,
    *,
    launch_toml: str | None = None,
    project_toml: str = 'name = "Test Mod"\nprefix = "test"\nmainprefix = "z"\n',
    meta_cpp: str | None = None,
) -> Path:
    """Create a minimal HEMTT project on disk and return its root."""
    (root / ".hemtt").mkdir(parents=True, exist_ok=True)
    (root / ".hemtt" / "project.toml").write_text(project_toml, encoding="utf-8")
    if launch_toml is not None:
        (root / ".hemtt" / "launch.toml").write_text(launch_toml, encoding="utf-8")
    if meta_cpp is not None:
        (root / "meta.cpp").write_text(meta_cpp, encoding="utf-8")
    return root


@pytest.fixture
def project_factory(tmp_path: Path):
    def _make(name: str = "proj", **kwargs) -> Path:
        return write_project(tmp_path / name, **kwargs)

    return _make
