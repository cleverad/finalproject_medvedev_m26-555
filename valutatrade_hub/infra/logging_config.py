"""Настройка журналов: строковый формат, файл с ротацией, уровень из настроек.

Журнал действий пользователя пишется в logs/actions.log. Папка и размер файла,
число копий и уровень берутся из SettingsLoader.
"""

import logging
import time
from logging.handlers import RotatingFileHandler

from valutatrade_hub.infra.settings import SettingsLoader

__all__ = ["ACTIONS_LOGGER", "setup_logging"]

ACTIONS_LOGGER = "valutatrade.actions"
ACTIONS_LOG_FILE = "actions.log"

# Пример записи: INFO 2025-10-09T12:05:22 BUY user='alice' ... result=OK
LOG_FORMAT = "%(levelname)s %(asctime)s %(message)s"
LOG_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S"
LOG_ENCODING = "utf-8"


def setup_logging() -> None:
    """Направить журнал действий в файл с ротацией.

    Повторный вызов заменяет обработчики, поэтому смена рабочей папки
    в тестах не плодит файлы.
    """
    settings = SettingsLoader()
    log_dir = settings.get("log_dir")
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(ACTIONS_LOGGER)
    logger.setLevel(settings.get("log_level"))
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    handler = RotatingFileHandler(
        log_dir / ACTIONS_LOG_FILE,
        maxBytes=settings.get("log_max_bytes"),
        backupCount=settings.get("log_backup_count"),
        encoding=LOG_ENCODING,
    )
    handler.setFormatter(_utc_formatter())
    logger.addHandler(handler)


def _utc_formatter() -> logging.Formatter:
    """Собрать форматтер с временем в UTC, как и всё время в приложении."""
    formatter = logging.Formatter(LOG_FORMAT, LOG_TIME_FORMAT)
    formatter.converter = time.gmtime
    return formatter
