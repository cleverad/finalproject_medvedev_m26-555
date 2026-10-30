import json
import re
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from valutatrade_hub.core import utils
from valutatrade_hub.core.exceptions import (
    CurrencyNotFoundError,
    InsufficientFundsError,
    RateNotAvailableError,
)
from valutatrade_hub.core.models import (
    EXCHANGE_RATES,
    Portfolio,
    Rate,
    RatesSnapshot,
    User,
    Wallet,
)

REGISTERED_AT = datetime(2025, 10, 9, 12, 0, 0, tzinfo=UTC)
UPDATED_AT = datetime(2025, 10, 10, 12, 0, 0, tzinfo=UTC)
RATES_JSON = {
    "pairs": {
        "EUR_USD": {
            "rate": 1.0786,
            "updated_at": "2025-10-10T12:00:00Z",
            "source": "ExchangeRate-API",
        },
        "BTC_USD": {
            "rate": 59337.21,
            "updated_at": "2025-10-10T12:00:00Z",
            "source": "CoinGecko",
        },
    },
    "last_refresh": "2025-10-10T12:00:01Z",
}


def make_user(password: str = "1234", **overrides) -> User:
    salt = overrides.pop("salt", "x5T9!")
    fields = {
        "user_id": 1,
        "username": "alice",
        "hashed_password": utils.hash_password(password, salt),
        "salt": salt,
        "registration_date": REGISTERED_AT,
    }
    fields.update(overrides)
    return User(**fields)


class TestUser:
    def test_create_hashes_the_password(self):
        user = User.create(7, "alice", " 1234 ", REGISTERED_AT)
        assert user.user_id == 7
        assert user.hashed_password != "1234"
        assert user.verify_password("1234")
        assert user.hashed_password == utils.hash_password("1234", user.salt)

    def test_create_uses_a_fresh_salt_each_time(self):
        first = User.create(1, "alice", "1234", REGISTERED_AT)
        second = User.create(2, "bob", "1234", REGISTERED_AT)
        assert first.salt != second.salt
        assert first.hashed_password != second.hashed_password

    def test_create_rejects_short_password(self):
        with pytest.raises(ValueError, match="не короче 4 символов"):
            User.create(1, "alice", "123", REGISTERED_AT)

    def test_getters(self):
        user = make_user()
        assert user.user_id == 1
        assert user.username == "alice"
        assert user.salt == "x5T9!"
        assert user.registration_date == REGISTERED_AT
        assert user.hashed_password == utils.hash_password("1234", "x5T9!")

    def test_attributes_are_private(self):
        user = make_user()
        assert set(vars(user)) == {
            "_user_id",
            "_username",
            "_hashed_password",
            "_salt",
            "_registration_date",
        }

    def test_verify_password(self):
        user = make_user("1234")
        assert user.verify_password("1234")
        assert not user.verify_password("4321")

    def test_change_password_rehashes(self):
        user = make_user("1234")
        old_hash = user.hashed_password
        user.change_password("abcd")
        assert user.hashed_password != old_hash
        assert user.verify_password("abcd")
        assert not user.verify_password("1234")
        assert user.salt == "x5T9!"

    def test_edge_spaces_do_not_lock_the_user_out(self):
        user = make_user("1234")
        assert user.verify_password(" 1234 ")
        user.change_password(" abcd ")
        assert user.verify_password("abcd")

    def test_change_password_rejects_short(self):
        user = make_user("1234")
        with pytest.raises(ValueError, match="не короче 4 символов"):
            user.change_password("123")
        assert user.verify_password("1234")

    def test_username_setter_validates(self):
        user = make_user()
        user.username = "  bob "
        assert user.username == "bob"
        with pytest.raises(ValueError, match="не может быть пустым"):
            user.username = "   "
        assert user.username == "bob"

    def test_constructor_rejects_naive_registration_date(self):
        with pytest.raises(ValueError, match=re.escape(utils.MSG_NAIVE_DATETIME)):
            make_user(registration_date=datetime(2025, 10, 9, 12, 0, 0))

    def test_constructor_validates_username(self):
        with pytest.raises(ValueError, match="не может быть пустым"):
            make_user(username="")

    def test_read_only_attributes(self):
        user = make_user()
        for name in ("user_id", "hashed_password", "salt", "registration_date"):
            with pytest.raises(AttributeError):
                setattr(user, name, "x")

    def test_user_info_has_no_secrets(self):
        info = make_user().get_user_info()
        assert info == {
            "user_id": 1,
            "username": "alice",
            "registration_date": "2025-10-09T12:00:00Z",
        }

    def test_to_dict_matches_users_json_layout(self):
        user = make_user()
        assert user.to_dict() == {
            "user_id": 1,
            "username": "alice",
            "hashed_password": user.hashed_password,
            "salt": "x5T9!",
            "registration_date": "2025-10-09T12:00:00Z",
        }

    def test_round_trip(self):
        user = make_user()
        copy = User.from_dict(user.to_dict())
        assert copy.to_dict() == user.to_dict()
        assert copy.verify_password("1234")

    def test_from_dict_accepts_naive_date_from_task_example(self):
        data = make_user().to_dict() | {"registration_date": "2025-10-09T12:00:00"}
        assert User.from_dict(data).registration_date == REGISTERED_AT

    def test_from_dict_rejects_missing_fields(self):
        with pytest.raises(KeyError):
            User.from_dict({"user_id": 1, "username": "alice"})


class TestWallet:
    def test_defaults_to_zero_with_display_precision(self):
        assert Wallet("USD").get_balance_info() == "USD: 0.00"
        assert Wallet("BTC").get_balance_info() == "BTC: 0.0000"

    def test_balance_is_decimal_at_storage_precision(self):
        wallet = Wallet("USD", 1200.0)
        assert wallet.balance == Decimal(1200)
        assert isinstance(wallet.balance, Decimal)
        assert str(wallet.balance) == "1200.00"
        assert str(Wallet("BTC", 0.05).balance) == "0.05000000"

    def test_deposit_and_withdraw(self):
        wallet = Wallet("BTC")
        wallet.deposit(0.05)
        wallet.withdraw(0.01)
        assert wallet.balance == Decimal("0.04")
        assert wallet.get_balance_info() == "BTC: 0.0400"

    def test_three_sales_leave_exactly_zero(self):
        wallet = Wallet("BTC", 0.3)
        for _ in range(3):
            wallet.withdraw(0.1)
        assert wallet.balance == 0

    def test_insufficient_funds_message_from_the_task(self):
        wallet = Wallet("BTC", 0.04)
        with pytest.raises(InsufficientFundsError) as info:
            wallet.withdraw(0.05)
        assert str(info.value) == (
            "Недостаточно средств: доступно 0.0400 BTC, требуется 0.0500 BTC"
        )
        assert wallet.balance == Decimal("0.04")

    def test_withdraw_everything_is_allowed(self):
        wallet = Wallet("USD", 10)
        wallet.withdraw(10)
        assert wallet.balance == 0

    @pytest.mark.parametrize("amount", [0, -1, -0.01])
    def test_non_positive_amount_message_from_the_task(self, amount):
        wallet = Wallet("USD", 10)
        for operation in (wallet.deposit, wallet.withdraw):
            with pytest.raises(ValueError, match="положительным числом"):
                operation(amount)
        assert wallet.balance == 10

    @pytest.mark.parametrize("amount", [True, "1", None])
    def test_amount_must_be_a_number(self, amount):
        with pytest.raises(TypeError):
            Wallet("USD", 10).deposit(amount)

    def test_too_precise_amount_is_rejected(self):
        wallet = Wallet("USD", 10)
        with pytest.raises(ValueError, match="2 знаков"):
            wallet.deposit(0.001)
        assert wallet.balance == 10

    def test_setter_rejects_negative_and_bad_types(self):
        wallet = Wallet("USD", 10)
        with pytest.raises(ValueError, match="отрицательным"):
            wallet.balance = -1
        for value in (True, "10", None, float("nan"), float("inf")):
            with pytest.raises((TypeError, ValueError)):
                wallet.balance = value
        assert wallet.balance == 10

    @pytest.mark.parametrize("value", [1, 1.0, Decimal(1)])
    def test_setter_accepts_int_float_and_decimal(self, value):
        wallet = Wallet("USD")
        wallet.balance = value
        assert wallet.balance == Decimal("1.00")

    def test_unknown_currency(self):
        with pytest.raises(CurrencyNotFoundError, match="'ABC'"):
            Wallet("ABC")

    def test_currency_code_is_read_only(self):
        wallet = Wallet("USD")
        with pytest.raises(AttributeError):
            wallet.currency_code = "EUR"
        assert set(vars(wallet)) == {"_currency", "_balance"}

    def test_to_dict_matches_portfolios_json_layout(self):
        assert Wallet("USD", 1500).to_dict() == {"balance": 1500.0}
        assert json.dumps(Wallet("USD", 1500).to_dict()) == '{"balance": 1500.0}'
        assert Wallet("BTC", 0.05).to_dict() == {"balance": 0.05}

    @pytest.mark.parametrize("value", [0.05, 1500, 0.00000001, 123456.78])
    def test_round_trip_through_json_is_exact(self, value):
        wallet = Wallet("BTC", value)
        data = json.loads(json.dumps(wallet.to_dict()))
        assert Wallet.from_dict("BTC", data).balance == wallet.balance

    def test_from_dict_rejects_missing_balance(self):
        with pytest.raises(KeyError):
            Wallet.from_dict("USD", {})


def btc_usd(value: str = "59337.21", **overrides) -> Rate:
    fields = {
        "from_code": "BTC",
        "to_code": "USD",
        "value": Decimal(value),
        "updated_at": UPDATED_AT,
        "source": "CoinGecko",
    }
    fields.update(overrides)
    return Rate(**fields)


class TestRate:
    def test_inverse_is_computed_from_the_exact_value(self):
        inverse = btc_usd().inverse()
        assert (inverse.from_code, inverse.to_code) == ("USD", "BTC")
        assert utils.format_rate(inverse.value) == "0.00001685"
        assert utils.format_rate(inverse.inverse().value) == "59337.21"
        assert inverse.updated_at == UPDATED_AT
        assert inverse.source == "CoinGecko"

    def test_cross_rate_takes_the_older_time_and_both_sources(self):
        eur = Rate(
            "EUR",
            "USD",
            Decimal("1.0786"),
            UPDATED_AT - timedelta(minutes=5),
            "ExchangeRate-API",
        )
        cross = btc_usd().cross(eur)
        assert (cross.from_code, cross.to_code) == ("BTC", "EUR")
        assert cross.value == Decimal("59337.21") / Decimal("1.0786")
        assert cross.updated_at == eur.updated_at
        assert cross.source == "CoinGecko+ExchangeRate-API"

    def test_cross_with_same_source_names_it_once(self):
        eth = btc_usd("3720", from_code="ETH")
        assert btc_usd().cross(eth).source == "CoinGecko"

    def test_cross_requires_a_common_currency(self):
        with pytest.raises(ValueError, match="общую валюту"):
            btc_usd().cross(btc_usd(to_code="EUR"))

    def test_key(self):
        assert btc_usd().key == "BTC_USD"

    @pytest.mark.parametrize(
        ("age_seconds", "fresh"), [(0, True), (299, True), (300, True), (301, False)]
    )
    def test_is_fresh_by_ttl(self, age_seconds, fresh):
        now = UPDATED_AT + timedelta(seconds=age_seconds)
        assert btc_usd().is_fresh(300, now) is fresh

    def test_future_rate_counts_as_fresh(self):
        assert btc_usd().is_fresh(300, UPDATED_AT - timedelta(days=1))

    def test_is_fresh_rejects_naive_now(self):
        with pytest.raises(ValueError, match=re.escape(utils.MSG_NAIVE_DATETIME)):
            btc_usd().is_fresh(300, datetime(2025, 10, 10, 12, 0))

    @pytest.mark.parametrize("value", ["0", "-1", "NaN", "Infinity"])
    def test_rate_must_be_positive_and_finite(self, value):
        with pytest.raises(ValueError, match="положительным"):
            btc_usd(value)

    def test_updated_at_is_normalized_to_utc(self):
        moscow = datetime(2025, 10, 10, 15, 0, tzinfo=timezone(timedelta(hours=3)))
        assert btc_usd(updated_at=moscow).updated_at == UPDATED_AT
        with pytest.raises(ValueError, match=re.escape(utils.MSG_NAIVE_DATETIME)):
            btc_usd(updated_at=datetime(2025, 10, 10, 12, 0))

    def test_rate_is_immutable_and_compared_by_value(self):
        rate = btc_usd()
        with pytest.raises(AttributeError):
            rate.value = Decimal(1)
        assert rate == btc_usd()


class TestRatesSnapshot:
    def test_round_trip_with_the_task_example(self):
        snapshot = RatesSnapshot.from_dict(RATES_JSON)
        assert snapshot.last_refresh == UPDATED_AT + timedelta(seconds=1)
        assert snapshot.pairs["BTC_USD"] == btc_usd()
        assert snapshot.to_dict() == RATES_JSON

    def test_empty_snapshot(self):
        snapshot = RatesSnapshot.from_dict({})
        assert snapshot.pairs == {}
        assert snapshot.last_refresh is None
        assert snapshot.to_dict() == {"pairs": {}, "last_refresh": None}

    @pytest.mark.parametrize("key", ["BTCUSD", "_USD", "BTC_"])
    def test_bad_pair_key(self, key):
        data = {"pairs": {key: RATES_JSON["pairs"]["BTC_USD"]}}
        with pytest.raises(ValueError, match="BTC_USD"):
            RatesSnapshot.from_dict(data)

    def test_missing_field_is_a_key_error(self):
        with pytest.raises(KeyError):
            RatesSnapshot.from_dict({"pairs": {"BTC_USD": {"rate": 1}}})

    @pytest.mark.parametrize(
        ("from_code", "to_code", "text", "source"),
        [
            ("BTC", "USD", "59337.21", "CoinGecko"),
            ("USD", "BTC", "0.00001685", "CoinGecko"),
            ("BTC", "EUR", "55013.17448544", "CoinGecko+ExchangeRate-API"),
            ("EUR", "BTC", "0.00001818", "ExchangeRate-API+CoinGecko"),
        ],
    )
    def test_get_rate_direct_inverse_and_cross(self, from_code, to_code, text, source):
        rate = RatesSnapshot.from_dict(RATES_JSON).get_rate(from_code, to_code)
        assert (rate.from_code, rate.to_code) == (from_code, to_code)
        assert utils.format_rate(rate.value) == text
        assert rate.source == source

    def test_get_rate_identity(self, monkeypatch):
        monkeypatch.setattr(utils, "utc_now", lambda: UPDATED_AT)
        rate = RatesSnapshot({}).get_rate("USD", "USD")
        assert (rate.value, rate.updated_at, rate.source) == (1, UPDATED_AT, "identity")
        assert RatesSnapshot({}).get_rate("BTC", "BTC").value == 1

    @pytest.mark.parametrize(
        ("from_code", "to_code"), [("GBP", "USD"), ("USD", "GBP"), ("BTC", "GBP")]
    )
    def test_missing_rate_names_the_requested_pair(self, from_code, to_code):
        snapshot = RatesSnapshot.from_dict(RATES_JSON)
        with pytest.raises(RateNotAvailableError) as info:
            snapshot.get_rate(from_code, to_code)
        assert str(info.value) == f"Не удалось получить курс для {from_code}→{to_code}"

    def test_stub_uses_the_task_rates(self):
        snapshot = RatesSnapshot.stub(UPDATED_AT)
        assert set(snapshot.pairs) == set(EXCHANGE_RATES)
        rate = snapshot.get_rate("BTC", "USD")
        assert (rate.value, rate.updated_at, rate.source) == (
            Decimal("59337.21"),
            UPDATED_AT,
            "stub",
        )
        assert snapshot.last_refresh == UPDATED_AT


PORTFOLIO_JSON = {
    "user_id": 1,
    "wallets": {
        "USD": {"balance": 1500.0},
        "BTC": {"balance": 0.05},
        "EUR": {"balance": 200.0},
    },
}


class TestPortfolio:
    def test_empty_by_default(self):
        portfolio = Portfolio(1)
        assert portfolio.user_id == 1
        assert portfolio.user is None
        assert portfolio.wallets == {}
        assert portfolio.get_wallet("USD") is None

    def test_add_currency_creates_once(self):
        portfolio = Portfolio(1)
        wallet = portfolio.add_currency("BTC")
        assert wallet.get_balance_info() == "BTC: 0.0000"
        wallet.deposit(0.05)
        assert portfolio.add_currency("BTC") is wallet
        assert portfolio.get_wallet("BTC") is wallet
        assert list(portfolio.wallets) == ["BTC"]

    def test_add_unknown_currency(self):
        with pytest.raises(CurrencyNotFoundError, match="'ABC'"):
            Portfolio(1).add_currency("ABC")

    def test_wallets_is_a_copy(self):
        portfolio = Portfolio(1)
        portfolio.add_currency("USD")
        copy = portfolio.wallets
        copy.clear()
        copy["EUR"] = Wallet("EUR")
        assert list(portfolio.wallets) == ["USD"]

    def test_user_is_read_only(self):
        user = make_user()
        portfolio = Portfolio(1, user=user)
        assert portfolio.user is user
        with pytest.raises(AttributeError):
            portfolio.user = make_user(user_id=2)
        with pytest.raises(AttributeError):
            portfolio.user_id = 2

    def test_user_must_match_user_id(self):
        with pytest.raises(ValueError, match="не принадлежит"):
            Portfolio(2, user=make_user(user_id=1))

    def test_wallet_key_must_match_its_currency(self):
        with pytest.raises(ValueError, match="под ключом EUR хранит валюту USD"):
            Portfolio(1, {"EUR": Wallet("USD")})

    def test_attributes_are_private(self):
        assert set(vars(Portfolio(1))) == {"_user_id", "_user", "_wallets"}

    def test_values_and_total_in_usd(self):
        portfolio = Portfolio.from_dict(PORTFOLIO_JSON)
        rates = RatesSnapshot.from_dict(RATES_JSON)
        values = portfolio.get_values("USD", rates)
        assert values == {
            "USD": Decimal("1500.00"),
            "BTC": Decimal("2966.86"),
            "EUR": Decimal("215.72"),
        }
        total = portfolio.get_total_value("USD", rates)
        assert total == Decimal("4682.58")
        assert total == sum(values.values())

    def test_values_in_another_base(self):
        portfolio = Portfolio.from_dict(PORTFOLIO_JSON)
        rates = RatesSnapshot.from_dict(RATES_JSON)
        values = portfolio.get_values("EUR", rates)
        assert values["EUR"] == Decimal("200.00")
        assert values["USD"] == Decimal("1390.69")
        assert values["BTC"] == Decimal("2750.66")

    def test_total_of_empty_portfolio_is_zero_at_base_precision(self):
        total = Portfolio(1).get_total_value("USD", RatesSnapshot({}))
        assert total == 0
        assert str(total) == "0.00"

    def test_unknown_base_currency(self):
        with pytest.raises(CurrencyNotFoundError, match="'ABC'"):
            Portfolio(1).get_total_value("ABC", RatesSnapshot({}))

    def test_missing_rate_is_an_error_not_zero(self):
        portfolio = Portfolio(1)
        portfolio.add_currency("GBP").deposit(10)
        with pytest.raises(RateNotAvailableError, match="GBP→USD"):
            portfolio.get_total_value("USD", RatesSnapshot.from_dict(RATES_JSON))

    def test_stub_rates_by_default(self, monkeypatch):
        monkeypatch.setattr(utils, "utc_now", lambda: UPDATED_AT)
        portfolio = Portfolio(1)
        portfolio.add_currency("BTC").deposit(1)
        assert portfolio.get_total_value() == Decimal(str(EXCHANGE_RATES["BTC_USD"]))

    def test_round_trip_with_the_task_example(self):
        portfolio = Portfolio.from_dict(PORTFOLIO_JSON)
        assert portfolio.to_dict() == PORTFOLIO_JSON
        assert json.loads(json.dumps(portfolio.to_dict())) == PORTFOLIO_JSON

    def test_from_dict_accepts_wallet_example_with_inner_code(self):
        data = {
            "user_id": 1,
            "wallets": {"BTC": {"currency_code": "BTC", "balance": 0.05}},
        }
        assert Portfolio.from_dict(data).get_wallet("BTC").balance == Decimal("0.05")

    def test_from_dict_rejects_inner_code_mismatch(self):
        data = {
            "user_id": 1,
            "wallets": {"BTC": {"currency_code": "ETH", "balance": 1}},
        }
        with pytest.raises(ValueError, match="под ключом BTC хранит валюту ETH"):
            Portfolio.from_dict(data)

    def test_from_dict_rejects_missing_fields(self):
        with pytest.raises(KeyError):
            Portfolio.from_dict({"user_id": 1})
