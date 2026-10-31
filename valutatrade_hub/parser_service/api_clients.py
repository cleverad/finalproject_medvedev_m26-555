"""Клиенты внешних API курсов: CoinGecko для крипты, ExchangeRate-API для фиата.

Оба клиента отдают замеры в одном виде: словарь по ключу пары X_USD, в каждом
замере курс Rate со временем источника и метаданные ответа для истории.
Любой сбой сети, ответа или его формы превращается в ApiRequestError.
"""

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from http import HTTPStatus
from typing import Any

import requests

from valutatrade_hub.core import utils
from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.core.models import Rate
from valutatrade_hub.infra.logging_config import PARSER_LOGGER
from valutatrade_hub.parser_service.config import API_KEY_ENV, ParserConfig

__all__ = [
    "BaseApiClient",
    "CoinGeckoClient",
    "ExchangeRateApiClient",
    "Observation",
    "ResponseMeta",
]

logger = logging.getLogger(PARSER_LOGGER)

COINGECKO_SOURCE = "CoinGecko"
EXCHANGERATE_SOURCE = "ExchangeRate-API"

ETAG_HEADER = "ETag"
MS_IN_SECOND = 1000
KEY_PLACEHOLDER = "<key>"

MSG_NETWORK = "{source}: сбой сети ({error})"
MSG_HTTP = "{source}: HTTP {status} {phrase}"
MSG_NOT_JSON = "{source}: ответ не JSON"
MSG_BAD_SHAPE = "{source}: неожиданная форма ответа ({detail})"
MSG_NO_RATE = "{source}: нет курса для {raw_id}"
MSG_NO_KEY = f"{EXCHANGERATE_SOURCE}: не задан {API_KEY_ENV}"
MSG_API_ERROR = "{source}: {explanation} ({error_type})"

# Пояснения к error-type в ответах ExchangeRate-API v6.
EXCHANGERATE_ERRORS = {
    "invalid-key": "неверный ключ API",
    "inactive-account": "учётная запись не подтверждена",
    "quota-reached": "исчерпан лимит запросов",
    "unsupported-code": "неизвестная валюта в запросе",
    "malformed-request": "неверно составлен запрос",
}
UNKNOWN_ERROR_EXPLANATION = "ошибка API"


@dataclass(frozen=True)
class ResponseMeta:
    """Метаданные одного HTTP-ответа для записи истории."""

    status_code: int
    request_ms: int
    etag: str | None


@dataclass(frozen=True)
class Observation:
    """Один замер курса: сам курс, идентификатор у источника и данные ответа."""

    rate: Rate
    raw_id: str
    response: ResponseMeta


class BaseApiClient(ABC):
    """Общее для клиентов: GET с таймаутом, разбор ошибок, журнал без ключа."""

    source: str

    def __init__(self, config: ParserConfig) -> None:
        self._config = config

    @abstractmethod
    def fetch_rates(self) -> dict[str, Observation]:
        """Запросить курсы к базовой валюте.

        Raises:
            ApiRequestError: при сбое сети, плохом статусе или неожиданном ответе.
        """

    def _get_json(
        self,
        url: str,
        params: dict[str, str] | None = None,
        shown_url: str | None = None,
    ) -> tuple[Any, ResponseMeta]:
        """Выполнить GET и вернуть тело ответа как JSON вместе с метаданными.

        В журнал попадает shown_url, если он задан: так ключ из адреса не
        оказывается в логах.

        Raises:
            ApiRequestError: при сбое сети, статусе кроме 200 или теле не JSON.
        """
        started = time.monotonic()
        try:
            response = requests.get(
                url, params=params, timeout=self._config.request_timeout
            )
        except requests.RequestException as error:
            reason = MSG_NETWORK.format(source=self.source, error=type(error).__name__)
            raise ApiRequestError(reason=reason) from error
        request_ms = round((time.monotonic() - started) * MS_IN_SECOND)
        logger.debug(
            "GET %s -> %s за %s мс", shown_url or url, response.status_code, request_ms
        )
        payload = self._payload(response)
        if response.status_code != HTTPStatus.OK:
            raise ApiRequestError(reason=self._describe_failure(response, payload))
        meta = ResponseMeta(
            response.status_code, request_ms, response.headers.get(ETAG_HEADER)
        )
        return payload, meta

    def _payload(self, response: requests.Response) -> Any:
        """Вернуть JSON ответа или None, если тело не разбирается."""
        try:
            return response.json()
        except ValueError:
            return None

    def _describe_failure(self, response: requests.Response, payload: Any) -> str:
        """Описать ответ со статусом кроме 200."""
        phrase = HTTPStatus(response.status_code).phrase
        return MSG_HTTP.format(
            source=self.source, status=response.status_code, phrase=phrase
        )

    def _require_json(self, payload: Any) -> dict[str, Any]:
        """Проверить, что тело ответа есть и это объект.

        Raises:
            ApiRequestError: если тела нет или оно не объект JSON.
        """
        if payload is None:
            raise ApiRequestError(reason=MSG_NOT_JSON.format(source=self.source))
        if not isinstance(payload, dict):
            detail = type(payload).__name__
            raise ApiRequestError(
                reason=MSG_BAD_SHAPE.format(source=self.source, detail=detail)
            )
        return payload

    def _number(self, value: object, raw_id: str) -> Decimal:
        """Принять курс как int или float, но не bool и не строку.

        Raises:
            ApiRequestError: если значения нет или оно не число.
        """
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ApiRequestError(
                reason=MSG_NO_RATE.format(source=self.source, raw_id=raw_id)
            )
        return utils.to_decimal(value)

    def _moment(self, unix_time: object) -> datetime:
        """Перевести unix-время источника в UTC, без него взять текущее время."""
        if isinstance(unix_time, bool) or not isinstance(unix_time, int | float):
            return utils.utc_now()
        return datetime.fromtimestamp(unix_time, UTC)


class CoinGeckoClient(BaseApiClient):
    """Курсы криптовалют к базовой валюте с CoinGecko, без ключа."""

    source = COINGECKO_SOURCE

    def fetch_rates(self) -> dict[str, Observation]:
        config = self._config
        params = {
            "ids": ",".join(config.crypto_id_map[c] for c in config.crypto_currencies),
            "vs_currencies": config.base_currency.lower(),
            "include_last_updated_at": "true",
        }
        payload, meta = self._get_json(config.coingecko_url, params)
        coins = self._require_json(payload)
        observed = {}
        for code in config.crypto_currencies:
            raw_id = config.crypto_id_map[code]
            coin = coins.get(raw_id)
            if not isinstance(coin, dict):
                raise ApiRequestError(
                    reason=MSG_NO_RATE.format(source=self.source, raw_id=raw_id)
                )
            value = self._number(coin.get(config.base_currency.lower()), raw_id)
            moment = self._moment(coin.get("last_updated_at"))
            rate = Rate(code, config.base_currency, value, moment, self.source)
            observed[rate.key] = Observation(rate, raw_id, meta)
        return observed


class ExchangeRateApiClient(BaseApiClient):
    """Курсы фиатных валют к базовой с ExchangeRate-API v6, по ключу."""

    source = EXCHANGERATE_SOURCE

    def fetch_rates(self) -> dict[str, Observation]:
        config = self._config
        key = config.exchangerate_api_key
        if not key:
            raise ApiRequestError(reason=MSG_NO_KEY)
        url = f"{config.exchangerate_api_url}/{key}/latest/{config.base_currency}"
        shown_url = url.replace(key, KEY_PLACEHOLDER)
        payload, meta = self._get_json(url, shown_url=shown_url)
        body = self._require_json(payload)
        if body.get("result") != "success":
            raise ApiRequestError(reason=self._explain_error(body))
        quotes = body.get("conversion_rates")
        if not isinstance(quotes, dict):
            raise ApiRequestError(
                reason=MSG_BAD_SHAPE.format(
                    source=self.source, detail="нет conversion_rates"
                )
            )
        moment = self._moment(body.get("time_last_update_unix"))
        observed = {}
        for code in config.fiat_currencies:
            # Источник отдаёт USD -> X, в снимке хранится X -> USD.
            value = Decimal(1) / self._number(quotes.get(code), code)
            rate = Rate(code, config.base_currency, value, moment, self.source)
            observed[rate.key] = Observation(rate, code, meta)
        return observed

    def _describe_failure(self, response: requests.Response, payload: Any) -> str:
        if isinstance(payload, dict) and "error-type" in payload:
            return self._explain_error(payload)
        return super()._describe_failure(response, payload)

    def _explain_error(self, body: dict[str, Any]) -> str:
        """Перевести error-type из ответа v6 в понятную причину."""
        error_type = str(body.get("error-type"))
        explanation = EXCHANGERATE_ERRORS.get(error_type, UNKNOWN_ERROR_EXPLANATION)
        return MSG_API_ERROR.format(
            source=self.source, explanation=explanation, error_type=error_type
        )
