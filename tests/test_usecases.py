import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from valutatrade_hub.core import usecases, utils
from valutatrade_hub.core.exceptions import (
    ApiRequestError,
    CurrencyNotFoundError,
    InsufficientFundsError,
    InvalidPasswordError,
    RateNotAvailableError,
    UsernameTakenError,
    UserNotFoundError,
    WalletNotFoundError,
)
from valutatrade_hub.core.models import (
    BUY_ACTION,
    SELL_ACTION,
    STUB_SOURCE,
    RatesSnapshot,
)
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.logging_config import ACTIONS_LOGGER
from valutatrade_hub.infra.settings import SettingsLoader
from valutatrade_hub.parser_service.updater import UpdateResult


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

    def test_without_rates_json_only_the_base_can_be_valued(self, workdir):
        alice = usecases.register("alice", "1234")
        db = DatabaseManager()
        portfolio = db.get_portfolio(alice)
        portfolio.add_currency("BTC").deposit(1)
        db.save_portfolio(portfolio)
        with pytest.raises(RateNotAvailableError, match="BTC→USD"):
            usecases.show_portfolio(alice.user_id)

    def test_show_portfolio_never_refreshes(self, workdir, offline_refresh):
        alice = usecases.register("alice", "1234")
        usecases.show_portfolio(alice.user_id)
        assert offline_refresh == []

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

    def test_fresh_rate_does_not_refresh(self, rates, offline_refresh):
        usecases.get_rate("BTC", "USD")
        assert offline_refresh == []

    def test_stale_rate_is_refreshed(self, rates, monkeypatch, offline_refresh):
        ttl = SettingsLoader().get("rates_ttl_seconds")
        later = UPDATED_AT + timedelta(seconds=ttl + 1)
        monkeypatch.setattr(utils, "utc_now", lambda: later)
        rate = usecases.get_rate("BTC", "USD")
        assert offline_refresh == [None]
        assert rate.updated_at == later
        assert rate.source == STUB_SOURCE

    def test_missing_pair_is_refreshed_first(self, rates, offline_refresh):
        rate = usecases.get_rate("USD", "ETH")
        assert offline_refresh == [None]
        assert rate.value == 1 / Decimal(3720)

    def test_pair_missing_after_a_clean_refresh(self, rates, offline_refresh):
        with pytest.raises(RateNotAvailableError) as info:
            usecases.get_rate("USD", "GBP")
        assert str(info.value) == "Не удалось получить курс для USD→GBP"
        assert offline_refresh == [None]

    def test_failed_refresh_raises_api_error(self, rates, monkeypatch):
        def refresh(sources=None):
            raise ApiRequestError(reason="down")

        monkeypatch.setattr(usecases, "refresh_rates", refresh)
        with pytest.raises(ApiRequestError, match="down"):
            usecases.get_rate("USD", "GBP")

    def test_errors_of_a_partial_refresh_are_reported(self, rates, monkeypatch):
        def refresh(sources=None):
            return UpdateResult(3, UPDATED_AT, ("ExchangeRate-API: не задан ключ",))

        monkeypatch.setattr(usecases, "refresh_rates", refresh)
        with pytest.raises(ApiRequestError, match="не задан ключ"):
            usecases.get_rate("GBP", "USD")

    @pytest.mark.parametrize(("from_code", "to_code"), [("ABC", "USD"), ("USD", "btc")])
    def test_unknown_code(self, rates, from_code, to_code):
        with pytest.raises(CurrencyNotFoundError):
            usecases.get_rate(from_code, to_code)

    def test_missing_rates_json_is_refreshed_first(self, workdir, offline_refresh):
        rate = usecases.get_rate("EUR", "USD")
        assert offline_refresh == [None]
        assert rate.value == Decimal("1.0786")
        assert (workdir / "data" / "rates.json").exists()


def stored_wallets(workdir, user_id=1):
    """Прочитать кошельки пользователя прямо из portfolios.json."""
    records = json.loads((workdir / "data" / "portfolios.json").read_text())
    return next(r["wallets"] for r in records if r["user_id"] == user_id)


class TestBuy:
    def test_debits_usd_and_credits_currency(self, rates, workdir):
        alice = usecases.register("alice", "1234")
        result = usecases.buy(alice.user_id, "BTC", 0.05)
        assert result.action == BUY_ACTION
        assert result.username == "alice"
        assert result.currency.code == "BTC"
        assert result.amount == Decimal("0.05")
        assert result.rate.value == Decimal("59300.0")
        assert (result.before, result.after) == (0, Decimal("0.05"))
        assert result.base.code == "USD"
        assert (result.base_before, result.base_after) == (10000, Decimal(7035))
        assert result.value == Decimal("2965.00")
        assert stored_wallets(workdir) == {
            "USD": {"balance": 7035.0},
            "BTC": {"balance": 0.05},
        }

    def test_second_purchase_accumulates(self, rates, workdir):
        alice = usecases.register("alice", "1234")
        usecases.buy(alice.user_id, "BTC", 0.05)
        result = usecases.buy(alice.user_id, "BTC", 0.01)
        assert (result.before, result.after) == (Decimal("0.05"), Decimal("0.06"))
        assert result.base_after == Decimal(6442)

    def test_cost_is_exact_to_the_amount_step(self, rates):
        alice = usecases.register("alice", "1234")
        result = usecases.buy(alice.user_id, "BTC", Decimal("0.00000001"))
        assert result.value == Decimal("0.00059300")
        assert result.base_after == Decimal("9999.99940700")

    def test_trade_worth_less_than_a_step_is_rejected(self, workdir):
        alice = usecases.register("alice", "1234")
        message = usecases.MSG_TRADE_TOO_SMALL.format(step="0.00000001", code="USD")
        with pytest.raises(ValueError, match=message):
            usecases.buy(alice.user_id, "RUB", Decimal("0.00000001"))
        assert stored_wallets(workdir) == {"USD": {"balance": 10000.0}}

    def test_insufficient_usd_changes_nothing(self, rates, workdir):
        alice = usecases.register("alice", "1234")
        with pytest.raises(InsufficientFundsError) as info:
            usecases.buy(alice.user_id, "BTC", 1)
        assert str(info.value) == (
            "Недостаточно средств: доступно 10000.00 USD, требуется 59300.00 USD"
        )
        assert stored_wallets(workdir) == {"USD": {"balance": 10000.0}}

    @pytest.mark.parametrize("amount", [0, -0.01, "0.05", float("nan")])
    def test_bad_amount(self, rates, amount):
        alice = usecases.register("alice", "1234")
        with pytest.raises((ValueError, TypeError)):
            usecases.buy(alice.user_id, "BTC", amount)

    def test_amount_is_cut_to_the_currency_step(self, rates):
        alice = usecases.register("alice", "1234")
        result = usecases.buy(alice.user_id, "BTC", 0.123456789)
        assert result.amount == Decimal("0.12345678")
        assert result.after == Decimal("0.12345678")

    def test_amount_below_the_step_is_not_positive(self, rates):
        alice = usecases.register("alice", "1234")
        with pytest.raises(ValueError, match="положительным"):
            usecases.buy(alice.user_id, "EUR", 1e-9)

    def test_base_currency_is_not_tradable(self, rates):
        alice = usecases.register("alice", "1234")
        message = usecases.MSG_BASE_CURRENCY_TRADE.format(code="USD")
        with pytest.raises(ValueError, match=message):
            usecases.buy(alice.user_id, "USD", 100)

    def test_unknown_currency(self, rates):
        alice = usecases.register("alice", "1234")
        with pytest.raises(CurrencyNotFoundError):
            usecases.buy(alice.user_id, "ABC", 1)

    def test_rate_not_available(self, rates):
        alice = usecases.register("alice", "1234")
        with pytest.raises(RateNotAvailableError) as info:
            usecases.buy(alice.user_id, "GBP", 1)
        assert str(info.value) == "Не удалось получить курс для GBP→USD"

    def test_stale_rate_is_refreshed_before_the_trade(
        self, rates, monkeypatch, offline_refresh
    ):
        alice = usecases.register("alice", "1234")
        ttl = SettingsLoader().get("rates_ttl_seconds")
        later = UPDATED_AT + timedelta(seconds=ttl + 1)
        monkeypatch.setattr(utils, "utc_now", lambda: later)
        result = usecases.buy(alice.user_id, "BTC", 0.05)
        assert offline_refresh == [None]
        assert result.rate.value == Decimal("59337.21")

    def test_failed_refresh_blocks_the_trade(self, rates, workdir, monkeypatch):
        alice = usecases.register("alice", "1234")
        later = UPDATED_AT + timedelta(days=1)
        monkeypatch.setattr(utils, "utc_now", lambda: later)

        def refresh(sources=None):
            raise ApiRequestError(reason="down")

        monkeypatch.setattr(usecases, "refresh_rates", refresh)
        with pytest.raises(ApiRequestError):
            usecases.buy(alice.user_id, "BTC", 0.05)
        assert stored_wallets(workdir) == {"USD": {"balance": 10000.0}}

    def test_unknown_user(self, rates):
        with pytest.raises(UserNotFoundError):
            usecases.buy(42, "BTC", 0.05)


class TestSell:
    def test_debits_currency_and_credits_usd(self, rates, workdir):
        alice = usecases.register("alice", "1234")
        usecases.buy(alice.user_id, "BTC", 0.05)
        result = usecases.sell(alice.user_id, "BTC", 0.01)
        assert result.action == SELL_ACTION
        assert result.username == "alice"
        assert result.currency.code == "BTC"
        assert result.amount == Decimal("0.01")
        assert result.rate.value == Decimal("59300.0")
        assert (result.before, result.after) == (Decimal("0.05"), Decimal("0.04"))
        assert result.base.code == "USD"
        assert (result.base_before, result.base_after) == (Decimal(7035), Decimal(7628))
        assert result.value == Decimal("593.00")
        assert stored_wallets(workdir) == {
            "USD": {"balance": 7628.0},
            "BTC": {"balance": 0.04},
        }

    def test_selling_everything_leaves_exact_zero_and_keeps_the_wallet(
        self, rates, workdir
    ):
        alice = usecases.register("alice", "1234")
        usecases.buy(alice.user_id, "BTC", 0.05)
        result = usecases.sell(alice.user_id, "BTC", 0.05)
        assert result.after == 0
        assert result.base_after == Decimal(10000)
        assert stored_wallets(workdir) == {
            "USD": {"balance": 10000.0},
            "BTC": {"balance": 0.0},
        }

    def test_round_trip_at_one_rate_conserves_value(self, rates):
        alice = usecases.register("alice", "1234")
        bought = usecases.buy(alice.user_id, "BTC", Decimal("0.12345678"))
        sold = usecases.sell(alice.user_id, "BTC", Decimal("0.12345678"))
        assert sold.value == bought.value
        assert sold.base_after == Decimal(10000)

    def test_insufficient_currency_changes_nothing(self, rates, workdir):
        alice = usecases.register("alice", "1234")
        usecases.buy(alice.user_id, "BTC", 0.04)
        with pytest.raises(InsufficientFundsError) as info:
            usecases.sell(alice.user_id, "BTC", 0.05)
        assert str(info.value) == (
            "Недостаточно средств: доступно 0.0400 BTC, требуется 0.0500 BTC"
        )
        assert stored_wallets(workdir) == {
            "USD": {"balance": 7628.0},
            "BTC": {"balance": 0.04},
        }

    def test_missing_wallet(self, rates, workdir):
        alice = usecases.register("alice", "1234")
        with pytest.raises(WalletNotFoundError) as info:
            usecases.sell(alice.user_id, "BTC", 0.01)
        assert str(info.value) == (
            "У вас нет кошелька 'BTC'. Добавьте валюту: она создаётся "
            "автоматически при первой покупке."
        )
        assert stored_wallets(workdir) == {"USD": {"balance": 10000.0}}

    def test_proceeds_less_than_a_step_are_rejected(self, workdir):
        alice = usecases.register("alice", "1234")
        usecases.buy(alice.user_id, "RUB", 1)
        message = usecases.MSG_TRADE_TOO_SMALL.format(step="0.00000001", code="USD")
        with pytest.raises(ValueError, match=message):
            usecases.sell(alice.user_id, "RUB", Decimal("0.00000001"))

    @pytest.mark.parametrize("amount", [0, -0.01, "0.05", float("nan")])
    def test_bad_amount(self, rates, amount):
        alice = usecases.register("alice", "1234")
        usecases.buy(alice.user_id, "BTC", 0.05)
        with pytest.raises((ValueError, TypeError)):
            usecases.sell(alice.user_id, "BTC", amount)

    def test_amount_is_cut_to_the_currency_step(self, rates):
        alice = usecases.register("alice", "1234")
        usecases.buy(alice.user_id, "BTC", 0.05)
        result = usecases.sell(alice.user_id, "BTC", 0.012345678901)
        assert result.amount == Decimal("0.01234567")

    def test_base_currency_is_not_tradable(self, rates):
        alice = usecases.register("alice", "1234")
        message = usecases.MSG_BASE_CURRENCY_TRADE.format(code="USD")
        with pytest.raises(ValueError, match=message):
            usecases.sell(alice.user_id, "USD", 100)

    def test_unknown_currency(self, rates):
        alice = usecases.register("alice", "1234")
        with pytest.raises(CurrencyNotFoundError):
            usecases.sell(alice.user_id, "ABC", 1)

    def test_failed_refresh_blocks_the_trade(self, rates, workdir, monkeypatch):
        alice = usecases.register("alice", "1234")
        usecases.buy(alice.user_id, "BTC", 0.05)
        ttl = SettingsLoader().get("rates_ttl_seconds")
        later = UPDATED_AT + timedelta(seconds=ttl + 1)
        monkeypatch.setattr(utils, "utc_now", lambda: later)

        def refresh(sources=None):
            raise ApiRequestError(reason="down")

        monkeypatch.setattr(usecases, "refresh_rates", refresh)
        with pytest.raises(ApiRequestError):
            usecases.sell(alice.user_id, "BTC", 0.01)
        assert stored_wallets(workdir)["BTC"] == {"balance": 0.05}

    def test_unknown_user(self, rates):
        with pytest.raises(UserNotFoundError):
            usecases.sell(42, "BTC", 0.05)


class TestActionsLog:
    @pytest.fixture
    def actions(self, caplog):
        caplog.set_level(logging.INFO, logger=ACTIONS_LOGGER)
        return caplog

    def test_buy_writes_a_record(self, rates, actions):
        alice = usecases.register("alice", "1234")
        usecases.buy(alice.user_id, "BTC", 0.05)
        (record,) = actions.records
        assert record.getMessage() == (
            "BUY user='alice' currency='BTC' amount=0.0500 rate=59300.00 base='USD' "
            "result=OK"
        )

    def test_sell_writes_a_record(self, rates, actions):
        alice = usecases.register("alice", "1234")
        usecases.buy(alice.user_id, "BTC", 0.05)
        usecases.sell(alice.user_id, "BTC", 0.01)
        assert actions.records[-1].getMessage() == (
            "SELL user='alice' currency='BTC' amount=0.0100 rate=59300.00 base='USD' "
            "result=OK"
        )

    def test_failed_trade_writes_an_error_and_raises(self, rates, actions):
        alice = usecases.register("alice", "1234")
        with pytest.raises(InsufficientFundsError):
            usecases.buy(alice.user_id, "BTC", 1)
        (record,) = actions.records
        assert record.levelname == "ERROR"
        assert record.getMessage().startswith(
            "BUY user=1 currency='BTC' amount=1 result=ERROR "
            "error_type=InsufficientFundsError error_message='Недостаточно средств"
        )


class TestUpdateRates:
    def test_all_sources_by_default(self, workdir, offline_refresh):
        result = usecases.update_rates()
        assert offline_refresh == [None]
        assert result.ok

    def test_one_source_by_alias(self, workdir, offline_refresh):
        usecases.update_rates("coingecko")
        assert offline_refresh == [("coingecko",)]


class TestShowRates:
    def test_empty_cache(self, workdir):
        with pytest.raises(ValueError, match="Локальный кеш курсов пуст"):
            usecases.show_rates()

    def test_all_pairs_sorted_by_key(self, rates):
        shown = usecases.show_rates()
        assert list(shown.pairs) == ["BTC_USD", "EUR_USD"]
        assert shown.pairs["BTC_USD"].value == Decimal("59300.0")
        assert shown.last_refresh == RatesSnapshot.from_dict(RATES_JSON).last_refresh

    def test_does_not_check_ttl_or_refresh(self, rates, monkeypatch, offline_refresh):
        monkeypatch.setattr(utils, "utc_now", lambda: UPDATED_AT + timedelta(days=9))
        assert list(usecases.show_rates().pairs) == ["BTC_USD", "EUR_USD"]
        assert offline_refresh == []

    def test_single_currency(self, rates):
        shown = usecases.show_rates(currency="EUR")
        assert list(shown.pairs) == ["EUR_USD"]

    @pytest.mark.parametrize("code", ["ABC", "GBP", "USD"])
    def test_currency_missing_from_cache(self, rates, code):
        with pytest.raises(ValueError, match=f"Курс для '{code}' не найден в кеше."):
            usecases.show_rates(currency=code)

    def test_top_lists_most_expensive_crypto_only(self, workdir, offline_refresh):
        usecases.update_rates()
        shown = usecases.show_rates(top=2)
        assert list(shown.pairs) == ["BTC_USD", "ETH_USD"]
        assert usecases.show_rates(top=1).pairs.keys() == {"BTC_USD"}
        assert list(usecases.show_rates(top=10).pairs) == ["BTC_USD", "ETH_USD"]

    @pytest.mark.parametrize("top", [0, -1])
    def test_top_must_be_positive(self, rates, top):
        with pytest.raises(ValueError, match="'top'"):
            usecases.show_rates(top=top)

    def test_other_base_is_derived_by_cross_rate(self, rates):
        shown = usecases.show_rates(base_currency="EUR")
        assert list(shown.pairs) == ["BTC_EUR", "USD_EUR"]
        assert shown.pairs["BTC_EUR"].value == Decimal("59300.0") / Decimal("1.07")
        assert shown.pairs["USD_EUR"].value == 1 / Decimal("1.07")
        assert shown.pairs["BTC_EUR"].source == "CoinGecko+ExchangeRate-API"

    def test_unknown_base(self, rates):
        with pytest.raises(CurrencyNotFoundError):
            usecases.show_rates(base_currency="ABC")

    def test_base_without_a_rate_in_cache(self, rates):
        with pytest.raises(RateNotAvailableError):
            usecases.show_rates(base_currency="GBP")
