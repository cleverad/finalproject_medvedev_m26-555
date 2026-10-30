from pathlib import Path

import pytest

from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.settings import SettingsLoader

PYPROJECT = """
[tool.valutatrade]
data_dir = "data"
"""


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Перейти во временную папку с pyproject.toml и сбросить синглтоны."""
    (tmp_path / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    SettingsLoader().reload()
    DatabaseManager().reset_cache()
    return tmp_path
