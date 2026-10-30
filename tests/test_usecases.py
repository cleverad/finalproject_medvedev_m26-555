import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from valutatrade_hub.core import usecases, utils
from valutatrade_hub.core.exceptions import (
    CurrencyNotFoundError,
    InvalidPasswordError,
    RateNotAvailableError,
    UsernameTakenError,
    UserNotFoundError,
)
from valutatrade_hub.core.models import STUB_SOURCE, RatesSnapshot
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.settings import SettingsLoader


class TestRegister:
    def test_creates_user_with_incrementing_ids(self, workdir):
        alice = usecases.register("alice", "1234")
        bob = usecases.register("bob", "abcd")
        assert (alice.user_id, bob.user_id) == (1, 2)
        stored = json.loads((workdir / "data" / "users.json").read_text())
        assert [u["username"] for u in stored] == ["alice", "bob"]
        assert stored[0]["hashed_password"] != "1234"
        assert alice.verify_password("1234")

    def test_username_is_stripped(self, workdir):
        assert usecases.register("  alice ", "1234").username == "alice"

    def test_creates_portfolio_with_start_balance_in_usd(self, workdir):
        alice = usecases.register("alice", "1234")
        stored = json.loads((workdir / "data" / "portfolios.json").read_text())
        start = float(SettingsLoader().get("start_balance_usd"))
        assert stored == [{"user_id": 1, "wallets": {"USD": {"balance": start}}}]
        portfolio = DatabaseManager().get_portfolio(alice)
        assert portfolio.user is alice
        assert portfolio.get_wallet("USD").balance == Decimal(str(start))

    def test_portfolio_is_written_before_the_user(self, workdir, monkeypatch):
        def fail(self, users):
            raise OSError("disk full")

        monkeypatch.setattr(DatabaseManager, "save_users", fail)
        with pytest.raises(OSError):
            usecases.register("alice", "1234")
        assert (workdir / "data" / "portfolios.json").exists()
        assert not (workdir / "data" / "users.json").exists()

    def test_duplicate_username(self, workdir):
        usecases.register("alice", "1234")
        with pytest.raises(UsernameTakenError, match="'alice' уже занято"):
            usecases.register("alice", "5678")

    @pytest.mark.parametrize(
        ("username", "password", "message"),
        [
            ("", "1234", "не может быть пустым"),
            ("alice", "123", "не короче 4 символов"),
        ],
    )
    def test_invalid_input_writes_nothing(self, workdir, username, password, message):
        with pytest.raises(ValueError, match=message):
            usecases.register(username, password)
        assert not (workdir / "data" / "users.json").exists()
        assert not (workdir / "data" / "portfolios.json").exists()


class TestLogin:
    def test_returns_user(self, workdir):
        usecases.register("alice", "1234")
        assert usecases.login("alice", "1234").username == "alice"

    def test_edge_spaces_are_ignored_on_both_sides(self, workdir):
        usecases.register(" alice ", " 1234 ")
        assert usecases.login("alice", "1234").username == "alice"
        assert usecases.login(" alice ", " 1234 ").username == "alice"

    def test_empty_username(self, workdir):
        with pytest.raises(ValueError, match="не может быть пустым"):
            usecases.login("  ", "1234")

    def test_unknown_user(self, workdir):
        with pytest.raises(UserNotFoundError, match="'alice' не найден"):
            usecases.login("alice", "1234")

    def test_wrong_password(self, workdir):
        usecases.register("alice", "1234")
        with pytest.raises(InvalidPasswordError, match="Неверный пароль"):
            usecases.login("alice", "4321")


UPDATED_AT = datetime(2025, 10, 10, 12, 0, 0, tzinfo=UTC)
RATES_JSON = {
    "pairs": {
        "BTC_USD": {
            "rate": 59300.0,
            "updated_at": "2025-10-10T12:00:00Z",
            "source": "CoinGecko",
        },
        "EUR_USD": {
            "rate": 1.07,
            "updated_at": "2025-10-10T12:00:00Z",
            "source": "ExchangeRate-API",
        },
    },
    "last_refresh": "2025-10-10T12:00:01Z",
}


class TestShowPortfolio:
    def test_fresh_user_has_only_the_usd_wallet(self, workdir):
        alice = usecases.register("alice", "1234")
        valuation = usecases.show_portfolio(alice.user_id)
        start = SettingsLoader().get("start_balance_usd")
        assert list(valuation.values) == ["USD"]
        assert valuation.total == start
        assert valuation.portfolio.user.username == "alice"

    def test_values_from_rates_json(self, workdir):
        alice = usecases.register("alice", "1234")
        db = DatabaseManager()
        portfolio = db.get_portfolio(alice)
        portfolio.add_currency("BTC").deposit(0.05)
        portfolio.add_currency("EUR").deposit(200)
        db.save_portfolio(portfolio)
        db.save_rates(RatesSnapshot.from_dict(RATES_JSON))
        valuation = usecases.show_portfolio(alice.user_id, "USD")
        assert valuation.values["BTC"] == Decimal("2965.00")
        assert valuation.values["EUR"] == Decimal("214.00")
        assert valuation.total == sum(valuation.values.values())

    def test_stub_rates_until_rates_json_exists(self, workdir):
        alice = usecases.register("alice", "1234")
        db = DatabaseManager()
        portfolio = db.get_portfolio(alice)
        portfolio.add_currency("BTC").deposit(1)
        db.save_portfolio(portfolio)
        valuation = usecases.show_portfolio(alice.user_id)
        assert valuation.values["BTC"] == Decimal("59337.21")

    def test_unknown_base_message(self, workdir):
        alice = usecases.register("alice", "1234")
        with pytest.raises(ValueError, match="Неизвестная базовая валюта 'ABC'"):
            usecases.show_portfolio(alice.user_id, "ABC")

    def test_unknown_user(self, workdir):
        with pytest.raises(UserNotFoundError):
            usecases.show_portfolio(42)

    def test_missing_portfolio_record_is_treated_as_empty(self, workdir):
        alice = usecases.register("alice", "1234")
        (workdir / "data" / "portfolios.json").write_text("[]")
        DatabaseManager().reset_cache()
        valuation = usecases.show_portfolio(alice.user_id)
        assert valuation.values == {}
        assert valuation.total == 0


@pytest.fixture
def rates(workdir, monkeypatch):
    """Сохранить снимок RATES_JSON и остановить часы на моменте его обновления."""
    DatabaseManager().save_rates(RatesSnapshot.from_dict(RATES_JSON))
    monkeypatch.setattr(utils, "utc_now", lambda: UPDATED_AT)


class TestGetRate:
    def test_direct_rate_from_rates_json(self, rates):
        rate = usecases.get_rate("BTC", "USD")
        assert rate.value == Decimal("59300.0")
        assert rate.updated_at == UPDATED_AT
        assert rate.source == "CoinGecko"

    def test_inverse_and_cross_are_derived(self, rates):
        inverse = usecases.get_rate("USD", "BTC")
        assert inverse.value == 1 / Decimal("59300.0")
        cross = usecases.get_rate("BTC", "EUR")
        assert cross.source == "CoinGecko+ExchangeRate-API"

    def test_same_currency(self, rates):
        assert usecases.get_rate("USD", "USD").value == 1

    def test_rate_at_the_ttl_edge_is_fresh(self, rates, monkeypatch):
        ttl = SettingsLoader().get("rates_ttl_seconds")
        edge = UPDATED_AT + timedelta(seconds=ttl)
        monkeypatch.setattr(utils, "utc_now", lambda: edge)
        assert usecases.get_rate("BTC", "USD").updated_at == UPDATED_AT

    def test_stale_rate_is_not_available(self, rates, monkeypatch):
        ttl = SettingsLoader().get("rates_ttl_seconds")
        later = UPDATED_AT + timedelta(seconds=ttl + 1)
        monkeypatch.setattr(utils, "utc_now", lambda: later)
        with pytest.raises(RateNotAvailableError, match="BTC→USD"):
            usecases.get_rate("BTC", "USD")

    def test_missing_pair(self, rates):
        with pytest.raises(RateNotAvailableError) as info:
            usecases.get_rate("USD", "ETH")
        assert str(info.value) == "Не удалось получить курс для USD→ETH"

    @pytest.mark.parametrize(("from_code", "to_code"), [("ABC", "USD"), ("USD", "btc")])
    def test_unknown_code(self, rates, from_code, to_code):
        with pytest.raises(CurrencyNotFoundError):
            usecases.get_rate(from_code, to_code)

    def test_stub_until_rates_json_exists(self, workdir):
        rate = usecases.get_rate("EUR", "USD")
        assert rate.value == Decimal("1.0786")
        assert rate.source == STUB_SOURCE
