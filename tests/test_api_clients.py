import logging
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import requests

from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.logging_config import PARSER_LOGGER
from valutatrade_hub.parser_service import api_clients
from valutatrade_hub.parser_service.api_clients import (
    BaseApiClient,
    CoinGeckoClient,
    ExchangeRateApiClient,
    Observation,
)
from valutatrade_hub.parser_service.config import ParserConfig

KEY = "0123456789abcdef01234567"
UPDATED_UNIX = 1760097600
UPDATED_AT = datetime(2025, 10, 10, 12, 0, 0, tzinfo=UTC)

COINGECKO_PAYLOAD = {
    "bitcoin": {"usd": 59337.21, "last_updated_at": UPDATED_UNIX},
    "ethereum": {"usd": 3720, "last_updated_at": UPDATED_UNIX},
    "solana": {"usd": 145.12, "last_updated_at": UPDATED_UNIX},
}
EXCHANGERATE_PAYLOAD = {
    "result": "success",
    "base_code": "USD",
    "time_last_update_unix": UPDATED_UNIX,
    "conversion_rates": {"USD": 1, "EUR": 0.927, "GBP": 0.809, "RUB": 98.45},
}


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeGet:
    """Подмена requests.get: запоминает вызов и отдаёт заготовленный ответ."""

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def __call__(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        if self.error is not None:
            raise self.error
        return self.response


@pytest.fixture
def fake_get(monkeypatch):
    def install(response=None, error=None):
        fake = FakeGet(response, error)
        monkeypatch.setattr(api_clients.requests, "get", fake)
        return fake

    return install


@pytest.fixture
def config():
    return ParserConfig(exchangerate_api_key=KEY)


class TestBaseApiClient:
    def test_is_abstract(self, config):
        with pytest.raises(TypeError):
            BaseApiClient(config)  # type: ignore[abstract]


class TestCoinGeckoClient:
    def test_requests_all_ids_against_the_base(self, config, fake_get):
        fake = fake_get(FakeResponse(payload=COINGECKO_PAYLOAD))
        CoinGeckoClient(config).fetch_rates()
        (call,) = fake.calls
        assert call["url"] == config.coingecko_url
        assert call["params"] == {
            "ids": "bitcoin,ethereum,solana",
            "vs_currencies": "usd",
            "include_last_updated_at": "true",
        }
        assert call["timeout"] == config.request_timeout

    def test_returns_rates_to_the_base_with_source_time(self, config, fake_get):
        fake_get(FakeResponse(payload=COINGECKO_PAYLOAD, headers={"ETag": 'W/"abc"'}))
        observed = CoinGeckoClient(config).fetch_rates()
        assert list(observed) == ["BTC_USD", "ETH_USD", "SOL_USD"]
        btc = observed["BTC_USD"]
        assert isinstance(btc, Observation)
        assert btc.rate.from_code == "BTC"
        assert btc.rate.to_code == "USD"
        assert btc.rate.value == Decimal("59337.21")
        assert btc.rate.updated_at == UPDATED_AT
        assert btc.rate.source == "CoinGecko"
        assert btc.raw_id == "bitcoin"
        assert btc.response.status_code == 200
        assert btc.response.etag == 'W/"abc"'
        assert btc.response.request_ms >= 0

    def test_integer_price_is_accepted(self, config, fake_get):
        fake_get(FakeResponse(payload=COINGECKO_PAYLOAD))
        observed = CoinGeckoClient(config).fetch_rates()
        assert observed["ETH_USD"].rate.value == Decimal(3720)

    def test_missing_time_falls_back_to_now(self, config, fake_get, monkeypatch):
        now = datetime(2026, 1, 1, tzinfo=UTC)
        monkeypatch.setattr(api_clients.utils, "utc_now", lambda: now)
        payload = {**COINGECKO_PAYLOAD, "bitcoin": {"usd": 60000}}
        fake_get(FakeResponse(payload=payload))
        observed = CoinGeckoClient(config).fetch_rates()
        assert observed["BTC_USD"].rate.updated_at == now

    @pytest.mark.parametrize(
        "payload",
        [
            {**COINGECKO_PAYLOAD, "solana": {}},
            {k: v for k, v in COINGECKO_PAYLOAD.items() if k != "solana"},
            {**COINGECKO_PAYLOAD, "solana": {"usd": True}},
            {**COINGECKO_PAYLOAD, "solana": {"usd": "145"}},
            [],
        ],
    )
    def test_incomplete_answer_is_an_api_error(self, config, fake_get, payload):
        fake_get(FakeResponse(payload=payload))
        with pytest.raises(ApiRequestError, match="No rate|response shape"):
            CoinGeckoClient(config).fetch_rates()

    def test_bad_status_is_an_api_error(self, config, fake_get):
        fake_get(FakeResponse(status_code=429, payload={}))
        with pytest.raises(ApiRequestError, match="429"):
            CoinGeckoClient(config).fetch_rates()

    def test_network_failure_is_an_api_error(self, config, fake_get):
        fake_get(error=requests.ConnectionError("boom"))
        with pytest.raises(ApiRequestError, match="ConnectionError"):
            CoinGeckoClient(config).fetch_rates()

    def test_not_json_is_an_api_error(self, config, fake_get):
        fake_get(FakeResponse(payload=None, text="<html>"))
        with pytest.raises(ApiRequestError, match="JSON"):
            CoinGeckoClient(config).fetch_rates()


class TestExchangeRateApiClient:
    def test_without_a_key_fails_before_any_request(self, fake_get):
        fake = fake_get(FakeResponse(payload=EXCHANGERATE_PAYLOAD))
        with pytest.raises(ApiRequestError, match="EXCHANGERATE_API_KEY"):
            ExchangeRateApiClient(ParserConfig()).fetch_rates()
        assert fake.calls == []

    def test_requests_latest_rates_for_the_base(self, config, fake_get):
        fake = fake_get(FakeResponse(payload=EXCHANGERATE_PAYLOAD))
        ExchangeRateApiClient(config).fetch_rates()
        (call,) = fake.calls
        assert call["url"] == f"{config.exchangerate_api_url}/{KEY}/latest/USD"
        assert call["params"] is None
        assert call["timeout"] == config.request_timeout

    def test_returns_inverted_rates_to_the_base(self, config, fake_get):
        fake_get(FakeResponse(payload=EXCHANGERATE_PAYLOAD))
        observed = ExchangeRateApiClient(config).fetch_rates()
        assert list(observed) == ["EUR_USD", "GBP_USD", "RUB_USD"]
        eur = observed["EUR_USD"]
        assert eur.rate.from_code == "EUR"
        assert eur.rate.value == Decimal(1) / Decimal("0.927")
        assert eur.rate.updated_at == UPDATED_AT
        assert eur.rate.source == "ExchangeRate-API"
        assert eur.raw_id == "EUR"

    @pytest.mark.parametrize(
        ("status", "error_type", "expected"),
        [
            (403, "invalid-key", "Invalid API key"),
            (429, "quota-reached", "quota"),
            (404, "unsupported-code", "unsupported-code"),
        ],
    )
    def test_api_error_types_are_explained(
        self, config, fake_get, status, error_type, expected
    ):
        payload = {"result": "error", "error-type": error_type}
        fake_get(FakeResponse(status_code=status, payload=payload))
        with pytest.raises(ApiRequestError, match=expected):
            ExchangeRateApiClient(config).fetch_rates()

    def test_error_result_with_ok_status_is_still_an_error(self, config, fake_get):
        payload = {"result": "error", "error-type": "quota-reached"}
        fake_get(FakeResponse(payload=payload))
        with pytest.raises(ApiRequestError, match="quota"):
            ExchangeRateApiClient(config).fetch_rates()

    @pytest.mark.parametrize(
        "payload",
        [
            {**EXCHANGERATE_PAYLOAD, "conversion_rates": {"EUR": 0.927}},
            {**EXCHANGERATE_PAYLOAD, "conversion_rates": {"EUR": "x"}},
            {"result": "success"},
        ],
    )
    def test_incomplete_answer_is_an_api_error(self, config, fake_get, payload):
        fake_get(FakeResponse(payload=payload))
        with pytest.raises(ApiRequestError, match="No rate|response shape"):
            ExchangeRateApiClient(config).fetch_rates()

    def test_key_never_leaks_into_errors_or_logs(self, config, fake_get, caplog):
        caplog.set_level(logging.DEBUG, logger=PARSER_LOGGER)
        fake_get(error=requests.ConnectionError(f"https://x/{KEY}/latest/USD down"))
        with pytest.raises(ApiRequestError) as info:
            ExchangeRateApiClient(config).fetch_rates()
        assert KEY not in str(info.value)
        fake_get(FakeResponse(status_code=403, payload={"error-type": "invalid-key"}))
        with pytest.raises(ApiRequestError) as info:
            ExchangeRateApiClient(config).fetch_rates()
        assert KEY not in str(info.value)
        fake_get(FakeResponse(payload=EXCHANGERATE_PAYLOAD))
        ExchangeRateApiClient(config).fetch_rates()
        assert caplog.records
        assert all(KEY not in record.getMessage() for record in caplog.records)

    def test_requests_are_logged_at_debug_without_the_key(
        self, config, fake_get, caplog
    ):
        caplog.set_level(logging.DEBUG, logger=PARSER_LOGGER)
        fake_get(FakeResponse(payload=EXCHANGERATE_PAYLOAD))
        ExchangeRateApiClient(config).fetch_rates()
        (record,) = caplog.records
        assert record.levelname == "DEBUG"
        assert "latest/USD" in record.getMessage()
        assert "200" in record.getMessage()
