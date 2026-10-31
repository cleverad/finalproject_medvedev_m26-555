import logging
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from valutatrade_hub.core import utils
from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.core.models import Rate
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.logging_config import PARSER_LOGGER
from valutatrade_hub.parser_service import updater as updater_module
from valutatrade_hub.parser_service.api_clients import (
    BaseApiClient,
    Observation,
    ResponseMeta,
)
from valutatrade_hub.parser_service.config import ParserConfig
from valutatrade_hub.parser_service.storage import RatesStorage
from valutatrade_hub.parser_service.updater import (
    RatesUpdater,
    UpdateResult,
    refresh_rates,
)

SOURCE_TIME = datetime(2025, 10, 10, 12, 0, 0, tzinfo=UTC)
NOW = datetime(2025, 10, 10, 15, 30, 0, tzinfo=UTC)
META = ResponseMeta(200, 10, None)


class FakeClient(BaseApiClient):
    """Клиент с заготовленными курсами или заготовленной ошибкой."""

    def __init__(self, source, alias, codes=(), error=None):
        super().__init__(ParserConfig())
        self.source = source
        self.alias = alias
        self.codes = codes
        self.error = error
        self.calls = 0

    def fetch_rates(self):
        self.calls += 1
        if self.error is not None:
            raise ApiRequestError(reason=self.error)
        return {
            f"{code}_USD": Observation(
                Rate(code, "USD", Decimal(value), SOURCE_TIME, self.source),
                code.lower(),
                META,
            )
            for code, value in self.codes
        }


def crypto(error=None):
    return FakeClient(
        "CoinGecko",
        "coingecko",
        (("BTC", "59337.21"), ("ETH", "3720"), ("SOL", "145.12")),
        error,
    )


def fiat(error=None):
    return FakeClient(
        "ExchangeRate-API",
        "exchangerate",
        (("EUR", "1.0786"), ("GBP", "1.236"), ("RUB", "0.01016")),
        error,
    )


@pytest.fixture
def storage(workdir, monkeypatch):
    monkeypatch.setattr(utils, "utc_now", lambda: NOW)
    return RatesStorage(DatabaseManager(), ParserConfig.load())


@pytest.fixture
def parser_log(caplog):
    caplog.set_level(logging.DEBUG, logger=PARSER_LOGGER)
    return caplog


def messages(caplog, level=None):
    return [
        r.getMessage() for r in caplog.records if level is None or r.levelname == level
    ]


class TestRunUpdate:
    def test_merges_all_sources_and_writes_both_files(self, storage, parser_log):
        result = RatesUpdater([crypto(), fiat()], storage).run_update()
        assert result == UpdateResult(6, NOW, ())
        assert result.ok
        snapshot = DatabaseManager().get_rates()
        assert sorted(snapshot.pairs) == [
            "BTC_USD",
            "ETH_USD",
            "EUR_USD",
            "GBP_USD",
            "RUB_USD",
            "SOL_USD",
        ]
        assert snapshot.last_refresh == NOW
        assert snapshot.pairs["EUR_USD"].updated_at == NOW
        assert storage.append_history(crypto().fetch_rates()) == 0

    def test_logs_each_step_as_in_the_sample(self, storage, parser_log):
        RatesUpdater([crypto(), fiat()], storage).run_update()
        assert messages(parser_log, "INFO") == [
            "Starting rates update...",
            "Fetching from CoinGecko... OK (3 rates)",
            "Fetching from ExchangeRate-API... OK (3 rates)",
            "Writing 6 rates to data/rates.json...",
        ]

    def test_one_failed_source_is_logged_and_the_rest_is_saved(
        self, storage, parser_log
    ):
        result = RatesUpdater([crypto("Network error."), fiat()], storage).run_update()
        assert result.updated == 3
        assert result.last_refresh == NOW
        assert result.errors == ("CoinGecko: Network error.",)
        assert not result.ok
        assert sorted(DatabaseManager().get_rates().pairs) == [
            "EUR_USD",
            "GBP_USD",
            "RUB_USD",
        ]
        assert messages(parser_log, "ERROR") == [
            "Failed to fetch from CoinGecko: Network error."
        ]
        assert (
            messages(parser_log, "INFO")[-1] == "Writing 3 rates to data/rates.json..."
        )

    def test_all_sources_failed_raises_and_writes_nothing(self, storage, parser_log):
        updater = RatesUpdater([crypto("down"), fiat("no key")], storage)
        with pytest.raises(ApiRequestError) as info:
            updater.run_update()
        assert "CoinGecko: down" in str(info.value)
        assert "ExchangeRate-API: no key" in str(info.value)
        assert DatabaseManager().get_rates() is None
        assert not (workdir_of(storage) / "exchange_rates.json").exists()

    def test_source_filter_asks_only_the_chosen_client(self, storage):
        coingecko, exchangerate = crypto(), fiat()
        result = RatesUpdater([coingecko, exchangerate], storage).run_update(
            sources=("exchangerate",)
        )
        assert (coingecko.calls, exchangerate.calls) == (0, 1)
        assert result.updated == 3

    def test_unknown_source_is_rejected_before_any_request(self, storage):
        coingecko = crypto()
        with pytest.raises(ValueError, match="binance"):
            RatesUpdater([coingecko], storage).run_update(sources=("binance",))
        assert coingecko.calls == 0

    def test_update_result_is_frozen(self):
        result = UpdateResult(0, NOW, ())
        with pytest.raises(AttributeError):
            result.updated = 1  # type: ignore[misc]


def workdir_of(storage: RatesStorage):
    return storage.history_file.parent


class TestRefreshRates:
    def test_builds_real_clients_and_runs_the_update(self, workdir, monkeypatch):
        built = []

        class Spy(RatesUpdater):
            def __init__(self, clients, storage):
                built.extend(type(c).__name__ for c in clients)
                super().__init__(clients, storage)

            def run_update(self, sources=None):
                return UpdateResult(0, NOW, ("spy",))

        monkeypatch.setattr(updater_module, "RatesUpdater", Spy)
        result = refresh_rates()
        assert built == ["CoinGeckoClient", "ExchangeRateApiClient"]
        assert result.errors == ("spy",)
