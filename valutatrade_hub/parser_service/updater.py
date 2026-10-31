"""Обновление курсов: опрос клиентов, слияние, запись, журнал каждого шага.

Сбой одного источника записывается в журнал и не мешает остальным. Если не
ответил ни один, обновление бросает ApiRequestError и ничего не пишет.
"""

import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime

from valutatrade_hub.core import utils
from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.logging_config import PARSER_LOGGER
from valutatrade_hub.parser_service.api_clients import (
    BaseApiClient,
    CoinGeckoClient,
    ExchangeRateApiClient,
    Observation,
)
from valutatrade_hub.parser_service.config import ParserConfig
from valutatrade_hub.parser_service.storage import RatesStorage

__all__ = ["RatesUpdater", "UpdateResult", "refresh_rates"]

logger = logging.getLogger(PARSER_LOGGER)

ERRORS_SEPARATOR = "; "
ALIASES_SEPARATOR = ", "

MSG_UNKNOWN_SOURCE = "Неизвестный источник '{name}'. Доступны: {aliases}"
MSG_ALL_FAILED = "ни один источник не ответил ({errors})"
MSG_SOURCE_FAILED = "{source}: {reason}"


@dataclass(frozen=True)
class UpdateResult:
    """Итог обновления: сколько курсов записано, когда, какие источники упали."""

    updated: int
    last_refresh: datetime
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """Все источники ответили."""
        return not self.errors

    def describe_errors(self) -> str:
        """Перечислить ошибки источников одной строкой для сообщения."""
        return ERRORS_SEPARATOR.join(self.errors)


class RatesUpdater:
    """Точка входа парсера: опросить клиентов и сохранить то, что получилось."""

    def __init__(self, clients: Sequence[BaseApiClient], storage: RatesStorage) -> None:
        self._clients = tuple(clients)
        self._storage = storage

    def run_update(self, sources: Collection[str] | None = None) -> UpdateResult:
        """Опросить выбранные источники, слить ответы, записать оба файла.

        Raises:
            ValueError: если среди sources есть неизвестное имя.
            ApiRequestError: если не ответил ни один источник.
        """
        clients = self._select(sources)
        logger.info("Starting rates update...")
        observed: dict[str, Observation] = {}
        errors: list[str] = []
        for client in clients:
            try:
                fetched = client.fetch_rates()
            except ApiRequestError as error:
                errors.append(
                    MSG_SOURCE_FAILED.format(source=client.source, reason=error.reason)
                )
                logger.error("Failed to fetch from %s: %s", client.source, error.reason)
                logger.debug("Failure details for %s", client.source, exc_info=error)
                continue
            observed.update(fetched)
            logger.info(
                "Fetching from %s... OK (%d rates)", client.source, len(fetched)
            )
        if not observed:
            raise ApiRequestError(
                reason=MSG_ALL_FAILED.format(errors=ERRORS_SEPARATOR.join(errors))
            )
        now = utils.utc_now()
        logger.info(
            "Writing %d rates to %s...",
            len(observed),
            utils.shown_path(self._storage.rates_file),
        )
        self._storage.append_history(observed)
        self._storage.save_snapshot(observed, now)
        return UpdateResult(len(observed), now, tuple(errors))

    def _select(self, sources: Collection[str] | None) -> tuple[BaseApiClient, ...]:
        """Вернуть клиентов по псевдонимам источников или всех, если фильтра нет.

        Raises:
            ValueError: если псевдоним не найден.
        """
        if sources is None:
            return self._clients
        by_alias = {client.alias: client for client in self._clients}
        for name in sources:
            if name not in by_alias:
                aliases = ALIASES_SEPARATOR.join(by_alias)
                raise ValueError(MSG_UNKNOWN_SOURCE.format(name=name, aliases=aliases))
        return tuple(by_alias[name] for name in sources)


def refresh_rates(sources: Collection[str] | None = None) -> UpdateResult:
    """Собрать парсер по настройкам и выполнить обновление.

    Единственная дверь из ядра в парсер.

    Raises:
        ValueError: если среди sources есть неизвестное имя.
        ApiRequestError: если не ответил ни один источник.
    """
    config = ParserConfig.load()
    storage = RatesStorage(DatabaseManager(), config)
    clients = (CoinGeckoClient(config), ExchangeRateApiClient(config))
    return RatesUpdater(clients, storage).run_update(sources)
