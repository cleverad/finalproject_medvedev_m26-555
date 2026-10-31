import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from valutatrade_hub.core.models import Rate, RatesSnapshot
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.parser_service.api_clients import Observation, ResponseMeta
from valutatrade_hub.parser_service.config import ParserConfig
from valutatrade_hub.parser_service.storage import RatesStorage

SOURCE_TIME = datetime(2025, 10, 10, 12, 0, 0, tzinfo=UTC)
FETCH_TIME = datetime(2025, 10, 10, 15, 30, 0, tzinfo=UTC)
META = ResponseMeta(200, 124, 'W/"abc123"')


def observe(code: str, value: str, moment=SOURCE_TIME, source="CoinGecko", raw_id=None):
    rate = Rate(code, "USD", Decimal(value), moment, source)
    return Observation(rate, raw_id or code.lower(), META)


def make_storage(workdir) -> RatesStorage:
    return RatesStorage(DatabaseManager(), ParserConfig.load())


class TestSnapshot:
    def test_empty_until_written(self, workdir):
        snapshot = make_storage(workdir).load_snapshot()
        assert snapshot.pairs == {}
        assert snapshot.last_refresh is None

    def test_save_writes_rates_json_with_fetch_time(self, workdir):
        storage = make_storage(workdir)
        observed = {"BTC_USD": observe("BTC", "59337.21", raw_id="bitcoin")}
        snapshot = storage.save_snapshot(observed, FETCH_TIME)
        assert snapshot.last_refresh == FETCH_TIME
        stored = json.loads((workdir / "data" / "rates.json").read_text())
        assert stored == {
            "pairs": {
                "BTC_USD": {
                    "rate": 59337.21,
                    "updated_at": "2025-10-10T15:30:00Z",
                    "source": "CoinGecko",
                }
            },
            "last_refresh": "2025-10-10T15:30:00Z",
        }
        assert DatabaseManager().get_rates() == snapshot

    def test_pairs_of_a_failed_source_keep_their_old_values(self, workdir):
        storage = make_storage(workdir)
        earlier = FETCH_TIME - timedelta(hours=1)
        storage.save_snapshot(
            {
                "BTC_USD": observe("BTC", "59000"),
                "EUR_USD": observe("EUR", "1.07", source="ExchangeRate-API"),
            },
            earlier,
        )
        snapshot = storage.save_snapshot(
            {"BTC_USD": observe("BTC", "59337.21")}, FETCH_TIME
        )
        assert snapshot.pairs["BTC_USD"].value == Decimal("59337.21")
        assert snapshot.pairs["BTC_USD"].updated_at == FETCH_TIME
        assert snapshot.pairs["EUR_USD"].value == Decimal("1.07")
        assert snapshot.pairs["EUR_USD"].updated_at == earlier
        assert snapshot.last_refresh == FETCH_TIME

    def test_older_update_does_not_overwrite_a_newer_pair(self, workdir):
        storage = make_storage(workdir)
        storage.save_snapshot({"BTC_USD": observe("BTC", "59337.21")}, FETCH_TIME)
        earlier = FETCH_TIME - timedelta(minutes=1)
        snapshot = storage.save_snapshot({"BTC_USD": observe("BTC", "1")}, earlier)
        assert snapshot.pairs["BTC_USD"].value == Decimal("59337.21")
        assert snapshot.last_refresh == FETCH_TIME

    def test_save_returns_what_core_will_read(self, workdir):
        storage = make_storage(workdir)
        snapshot = storage.save_snapshot(
            {"BTC_USD": observe("BTC", "59337.21")}, FETCH_TIME
        )
        assert isinstance(snapshot, RatesSnapshot)
        assert storage.load_snapshot() == snapshot


class TestHistory:
    def test_appends_records_in_the_history_schema(self, workdir):
        storage = make_storage(workdir)
        added = storage.append_history(
            {"BTC_USD": observe("BTC", "59337.21", raw_id="bitcoin")}
        )
        assert added == 1
        records = json.loads((workdir / "data" / "exchange_rates.json").read_text())
        assert records == [
            {
                "id": "BTC_USD_2025-10-10T12:00:00Z",
                "from_currency": "BTC",
                "to_currency": "USD",
                "rate": 59337.21,
                "timestamp": "2025-10-10T12:00:00Z",
                "source": "CoinGecko",
                "meta": {
                    "raw_id": "bitcoin",
                    "request_ms": 124,
                    "status_code": 200,
                    "etag": 'W/"abc123"',
                },
            }
        ]

    def test_same_source_time_is_not_recorded_twice(self, workdir):
        storage = make_storage(workdir)
        observed = {"EUR_USD": observe("EUR", "1.07", source="ExchangeRate-API")}
        assert storage.append_history(observed) == 1
        assert storage.append_history(observed) == 0
        later = {"EUR_USD": observe("EUR", "1.08", SOURCE_TIME + timedelta(days=1))}
        assert storage.append_history(later) == 1
        records = json.loads((workdir / "data" / "exchange_rates.json").read_text())
        assert [r["id"] for r in records] == [
            "EUR_USD_2025-10-10T12:00:00Z",
            "EUR_USD_2025-10-11T12:00:00Z",
        ]

    def test_history_survives_a_restart(self, workdir):
        make_storage(workdir).append_history({"BTC_USD": observe("BTC", "1")})
        DatabaseManager().reset_cache()
        assert (
            make_storage(workdir).append_history({"BTC_USD": observe("BTC", "1")}) == 0
        )
