"""Планировщик: обновлять курсы по таймеру в отдельном процессе.

Запуск: python -m valutatrade_hub.parser_service.scheduler [--interval N] [--runs K]
Интервал по умолчанию берётся из настройки scheduler_interval_seconds. В REPL
планировщик не встраивается: две точки записи rates.json в одном процессе
ни к чему, а отдельный процесс останавливается по Ctrl+C.
"""

import argparse
import logging
import sys
import time
from collections.abc import Callable, Sequence

from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.infra.settings import SettingsLoader
from valutatrade_hub.logging_config import (
    PARSER_LOGGER,
    parser_console,
    setup_logging,
)
from valutatrade_hub.parser_service.updater import UpdateResult, refresh_rates

__all__ = ["RatesScheduler", "main"]

logger = logging.getLogger(PARSER_LOGGER)

MSG_BAD_INTERVAL = "Интервал обновления должен быть положительным числом секунд"
MSG_BAD_RUNS = "Число запусков runs должно быть положительным"
HELP_INTERVAL = "интервал между обновлениями в секундах"
HELP_RUNS = "сколько обновлений выполнить, по умолчанию без ограничения"


class RatesScheduler:
    """Цикл «обновить, подождать интервал», пока не кончатся запуски или ввод."""

    def __init__(
        self,
        interval_seconds: int,
        refresh: Callable[[], UpdateResult],
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Запомнить интервал, функцию обновления и способ ждать.

        Raises:
            ValueError: если интервал не положительный.
        """
        if interval_seconds <= 0:
            raise ValueError(MSG_BAD_INTERVAL)
        self._interval = interval_seconds
        self._refresh = refresh
        self._sleep = sleep

    def run(self, runs: int | None = None) -> None:
        """Выполнять обновления с паузами, runs раз или до Ctrl+C.

        Сбой обновления пишется в журнал, цикл продолжается.

        Raises:
            ValueError: если runs задан и не положительный.
        """
        if runs is not None and runs <= 0:
            raise ValueError(MSG_BAD_RUNS)
        logger.info("Scheduler started: every %d s", self._interval)
        done = 0
        try:
            while runs is None or done < runs:
                if done:
                    self._sleep(self._interval)
                self._refresh_once()
                done += 1
        except KeyboardInterrupt:
            pass
        finally:
            logger.info("Scheduler stopped")

    def _refresh_once(self) -> None:
        try:
            result = self._refresh()
        except ApiRequestError as error:
            logger.error("Scheduled update failed: %s", error)
            return
        logger.info(
            "Scheduled update done: %d rates, errors: %d",
            result.updated,
            len(result.errors),
        )


def main(argv: Sequence[str] | None = None) -> None:
    """Разобрать аргументы, настроить журналы и запустить планировщик."""
    setup_logging()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--interval",
        type=int,
        default=SettingsLoader().get("scheduler_interval_seconds"),
        help=HELP_INTERVAL,
    )
    parser.add_argument("--runs", type=int, default=None, help=HELP_RUNS)
    arguments = parser.parse_args(argv)
    with parser_console():
        RatesScheduler(arguments.interval, refresh_rates).run(arguments.runs)


if __name__ == "__main__":
    main(sys.argv[1:])
