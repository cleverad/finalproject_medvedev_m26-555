from pathlib import Path

import pytest

from valutatrade_hub.core import usecases, utils
from valutatrade_hub.core.models import RatesSnapshot
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.settings import SettingsLoader
from valutatrade_hub.parser_service.updater import UpdateResult

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


@pytest.fixture(autouse=True)
def offline_refresh(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Подменить обновление курсов: тесты не ходят в сеть.

    Обновление пишет снимок из EXCHANGE_RATES текущим временем и запоминает
    вызовы, чтобы тест мог проверить, было ли оно.
    """
    calls: list[object] = []

    def refresh(sources=None):
        calls.append(sources)
        now = utils.utc_now()
        snapshot = RatesSnapshot.stub(now)
        DatabaseManager().save_rates(snapshot)
        return UpdateResult(len(snapshot.pairs), now, ())

    monkeypatch.setattr(usecases, "refresh_rates", refresh)
    return calls
