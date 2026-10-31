"""Настройка журналов: строковый формат, файлы с ротацией, уровень из настроек.

Журнал действий пользователя пишется в logs/actions.log с уровнем из настроек,
журнал парсера курсов в logs/parser.log с уровнем DEBUG и подробным форматом.
Папка, размер файла и число копий берутся из SettingsLoader.
"""

import logging
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from valutatrade_hub.infra.settings import SettingsLoader

__all__ = [
    "ACTIONS_LOGGER",
    "PARSER_LOGGER",
    "parser_console",
    "parser_log_path",
    "setup_logging",
]

ACTIONS_LOGGER = "valutatrade.actions"
ACTIONS_LOG_FILE = "actions.log"
PARSER_LOGGER = "valutatrade.parser"
PARSER_LOG_FILE = "parser.log"
HTTP_LIBRARY_LOGGER = "urllib3"

# Пример записи: INFO 2025-10-09T12:05:22 BUY user='alice' ... result=OK
ACTIONS_FORMAT = "%(levelname)s %(asctime)s %(message)s"
# Пример записи: 2025-10-10T15:30:00 INFO valutatrade.parser Fetching from CoinGecko...
PARSER_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
# Пример строки в консоли: INFO: Fetching from CoinGecko... OK (3 rates)
CONSOLE_FORMAT = "%(levelname)s: %(message)s"
LOG_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"
LOG_ENCODING = "utf-8"


def parser_log_path() -> Path:
    """Вернуть путь к журналу парсера по текущим настройкам."""
    return SettingsLoader().get("log_dir") / PARSER_LOG_FILE


def setup_logging() -> None:
    """Направить журналы действий и парсера в файлы с ротацией.

    Повторный вызов заменяет обработчики, поэтому смена рабочей папки
    в тестах не плодит файлы.
    """
    settings = SettingsLoader()
    log_dir = settings.get("log_dir")
    log_dir.mkdir(parents=True, exist_ok=True)
    _to_rotating_file(
        ACTIONS_LOGGER,
        log_dir / ACTIONS_LOG_FILE,
        ACTIONS_FORMAT,
        settings.get("log_level"),
    )
    _to_rotating_file(PARSER_LOGGER, parser_log_path(), PARSER_FORMAT, logging.DEBUG)
    # Отладочные записи HTTP-библиотеки содержат адрес запроса вместе с ключом API.
    logging.getLogger(HTTP_LIBRARY_LOGGER).setLevel(logging.INFO)


def _to_rotating_file(
    name: str, path: Path, line_format: str, level: int | str
) -> None:
    """Заменить обработчики логгера одним файлом с ротацией из настроек."""
    settings = SettingsLoader()
    logger = logging.getLogger(name)
    logger.setLevel(level)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    handler = RotatingFileHandler(
        path,
        maxBytes=settings.get("log_max_bytes"),
        backupCount=settings.get("log_backup_count"),
        encoding=LOG_ENCODING,
    )
    handler.setFormatter(_utc_formatter(line_format))
    logger.addHandler(handler)


def _utc_formatter(line_format: str) -> logging.Formatter:
    """Собрать форматтер с временем в UTC, как и всё время в приложении."""
    formatter = logging.Formatter(line_format, LOG_TIME_FORMAT)
    formatter.converter = time.gmtime
    return formatter


@contextmanager
def parser_console() -> Iterator[None]:
    """Показывать записи парсера уровня INFO в консоли, пока открыт контекст.

    Файл получает записи всегда, консоль только во время команды update-rates,
    поэтому get-rate обновляет кеш молча.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter(CONSOLE_FORMAT))
    logger = logging.getLogger(PARSER_LOGGER)
    logger.addHandler(handler)
    try:
        yield
    finally:
        logger.removeHandler(handler)
