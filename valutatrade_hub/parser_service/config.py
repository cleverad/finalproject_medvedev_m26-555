"""Настройки парсера курсов: источники, списки валют, ключ API, пути к файлам.

Списки валют берутся из реестра currencies.py, пути из SettingsLoader, ключ
ExchangeRate-API из переменной окружения. В коде ключа нет.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

from valutatrade_hub.core.currencies import (
    BASE_CURRENCY,
    CryptoCurrency,
    Currency,
    FiatCurrency,
    get_currency,
    supported_codes,
)
from valutatrade_hub.infra.settings import SettingsLoader

__all__ = ["ParserConfig"]

API_KEY_ENV = "EXCHANGERATE_API_KEY"
COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"
EXCHANGERATE_API_URL = "https://v6.exchangerate-api.com/v6"
REQUEST_TIMEOUT_SECONDS = 10

# CoinGecko принимает идентификаторы монет, а не тикеры.
CRYPTO_ID_MAP = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}

MSG_NO_CRYPTO_ID = "Для криптовалют {codes} не задан идентификатор CoinGecko"
CODES_SEPARATOR = ", "


def registry_codes(kind: type[Currency]) -> tuple[str, ...]:
    """Вернуть коды валют данного вида из реестра, без базовой валюты."""
    return tuple(
        code
        for code in supported_codes()
        if code != BASE_CURRENCY and isinstance(get_currency(code), kind)
    )


@dataclass(frozen=True)
class ParserConfig:
    """Параметры обновления курсов."""

    exchangerate_api_key: str | None = None
    coingecko_url: str = COINGECKO_URL
    exchangerate_api_url: str = EXCHANGERATE_API_URL
    base_currency: str = BASE_CURRENCY
    fiat_currencies: tuple[str, ...] = field(
        default_factory=lambda: registry_codes(FiatCurrency)
    )
    crypto_currencies: tuple[str, ...] = field(
        default_factory=lambda: registry_codes(CryptoCurrency)
    )
    crypto_id_map: dict[str, str] = field(default_factory=lambda: dict(CRYPTO_ID_MAP))
    request_timeout: int = REQUEST_TIMEOUT_SECONDS
    rates_file: Path | None = None
    history_file: Path | None = None

    def __post_init__(self) -> None:
        """Проверить, что у каждой криптовалюты есть идентификатор CoinGecko.

        Raises:
            ValueError: если какой-то код не найден в crypto_id_map.
        """
        missing = [
            code for code in self.crypto_currencies if code not in self.crypto_id_map
        ]
        if missing:
            raise ValueError(
                MSG_NO_CRYPTO_ID.format(codes=CODES_SEPARATOR.join(missing))
            )

    @classmethod
    def load(cls) -> Self:
        """Собрать настройки из окружения и SettingsLoader.

        Ключ читается в момент вызова, а не при импорте, поэтому .env, который
        SettingsLoader подгружает в окружение, успевает подействовать.
        """
        settings = SettingsLoader()
        return cls(
            exchangerate_api_key=os.environ.get(API_KEY_ENV) or None,
            rates_file=settings.get("rates_file"),
            history_file=settings.get("history_file"),
        )
