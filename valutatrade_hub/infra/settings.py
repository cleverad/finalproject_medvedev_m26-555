"""Конфигурация приложения: синглтон SettingsLoader.

Значения берутся из секции [tool.valutatrade] файла pyproject.toml в текущей
папке поверх умолчаний DEFAULTS. Заодно переменные из .env попадают в окружение.
"""

import os
import tomllib
from pathlib import Path
from typing import Any

__all__ = ["DEFAULTS", "SettingsLoader"]

PYPROJECT_FILE = "pyproject.toml"
PYPROJECT_TOOL_TABLE = "tool"
TOOL_SECTION = "valutatrade"
DOTENV_FILE = ".env"
DOTENV_COMMENT = "#"
DOTENV_EXPORT_PREFIX = "export "
DOTENV_SEPARATOR = "="
DOTENV_QUOTES = "'\""

DEFAULTS: dict[str, Any] = {
    "data_dir": "data",
    "users_file": "users.json",
    "portfolios_file": "portfolios.json",
    "rates_file": "rates.json",
    "history_file": "exchange_rates.json",
    "log_dir": "logs",
    "log_level": "INFO",
    "log_max_bytes": 1_000_000,
    "log_backup_count": 3,
    "rates_ttl_seconds": 300,
    "default_base_currency": "USD",
    "start_balance_usd": 10000,
    "scheduler_interval_seconds": 300,
}
DATA_FILE_KEYS = ("users_file", "portfolios_file", "rates_file", "history_file")

_MISSING = object()


class SettingsLoader:
    """Единственный экземпляр настроек приложения.

    Синглтон через __new__: короче метакласса и читается без знания метаклассов.
    """

    _instance: "SettingsLoader | None" = None

    def __new__(cls) -> "SettingsLoader":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.reload()
        return cls._instance

    def reload(self) -> None:
        """Перечитать pyproject.toml и .env из текущей папки."""
        values = dict(DEFAULTS)
        values.update(_read_tool_section(Path.cwd() / PYPROJECT_FILE))
        data_dir = Path(values["data_dir"]).resolve()
        values["data_dir"] = data_dir
        for key in DATA_FILE_KEYS:
            values[key] = data_dir / values[key]
        values["log_dir"] = Path(values["log_dir"]).resolve()
        self._values = values
        _load_dotenv(Path.cwd() / DOTENV_FILE)

    def get(self, key: str, default: Any = _MISSING) -> Any:
        """Вернуть значение настройки.

        Raises:
            KeyError: если ключа нет и значение по умолчанию не передано.
        """
        if key in self._values:
            return self._values[key]
        if default is _MISSING:
            raise KeyError(key)
        return default


def _read_tool_section(pyproject: Path) -> dict[str, Any]:
    """Прочитать секцию [tool.valutatrade], без файла или секции дать пустой словарь."""
    if not pyproject.is_file():
        return {}
    with pyproject.open("rb") as stream:
        document = tomllib.load(stream)
    return document.get(PYPROJECT_TOOL_TABLE, {}).get(TOOL_SECTION, {})


def _load_dotenv(dotenv: Path) -> None:
    """Добавить переменные из .env в окружение, не трогая уже заданные."""
    if not dotenv.is_file():
        return
    for raw_line in dotenv.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip().removeprefix(DOTENV_EXPORT_PREFIX).strip()
        if not line or line.startswith(DOTENV_COMMENT) or DOTENV_SEPARATOR not in line:
            continue
        name, value = line.split(DOTENV_SEPARATOR, 1)
        os.environ.setdefault(name.strip(), value.strip().strip(DOTENV_QUOTES))
