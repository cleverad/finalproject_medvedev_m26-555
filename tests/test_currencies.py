from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal

import pytest

from valutatrade_hub.core import currencies
from valutatrade_hub.core.currencies import (
    BASE_CURRENCY,
    CryptoCurrency,
    Currency,
    FiatCurrency,
    get_currency,
    supported_codes,
)
from valutatrade_hub.core.exceptions import CurrencyNotFoundError


class TestDisplayInfo:
    def test_fiat_format_from_the_task(self):
        usd = FiatCurrency("US Dollar", "USD", "United States")
        assert (
            usd.get_display_info() == "[FIAT] USD — US Dollar (Issuing: United States)"
        )

    def test_crypto_format_from_the_task(self):
        btc = CryptoCurrency("Bitcoin", "BTC", "SHA-256", 1.12e12)
        assert (
            btc.get_display_info()
            == "[CRYPTO] BTC — Bitcoin (Algo: SHA-256, MCAP: 1.12e12)"
        )


class TestInvariants:
    def test_currency_is_abstract(self):
        with pytest.raises(TypeError):
            Currency("US Dollar", "USD")

    @pytest.mark.parametrize("code", ["", "usd", "US D", "U", "ABCDEF", "US1", None])
    def test_bad_code_is_rejected(self, code):
        with pytest.raises(ValueError, match="Код валюты"):
            FiatCurrency("US Dollar", code, "United States")

    @pytest.mark.parametrize("name", ["", "   ", None])
    def test_empty_name_is_rejected(self, name):
        with pytest.raises(ValueError, match="Имя валюты"):
            CryptoCurrency(name, "BTC", "SHA-256", 1.12e12)

    def test_name_is_stripped(self):
        assert FiatCurrency("  Euro ", "EUR", "Eurozone").name == "Euro"

    def test_precision_by_kind(self):
        assert (FiatCurrency.precision, FiatCurrency.display_precision) == (2, 2)
        assert (CryptoCurrency.precision, CryptoCurrency.display_precision) == (8, 4)


class TestRegistry:
    def test_base_currency_is_usd_as_in_the_task(self):
        assert BASE_CURRENCY == "USD"
        assert isinstance(get_currency(BASE_CURRENCY), FiatCurrency)

    @pytest.mark.parametrize(
        ("code", "kind"),
        [
            ("USD", FiatCurrency),
            ("EUR", FiatCurrency),
            ("GBP", FiatCurrency),
            ("RUB", FiatCurrency),
            ("BTC", CryptoCurrency),
            ("ETH", CryptoCurrency),
            ("SOL", CryptoCurrency),
        ],
    )
    def test_known_codes(self, code, kind):
        currency = get_currency(code)
        assert isinstance(currency, kind)
        assert currency.code == code

    def test_same_object_each_time(self):
        assert get_currency("BTC") is get_currency("BTC")

    @pytest.mark.parametrize("code", ["ABC", "btc", "", " BTC"])
    def test_unknown_code(self, code):
        with pytest.raises(CurrencyNotFoundError) as info:
            get_currency(code)
        assert str(info.value) == f"Неизвестная валюта '{code}'"
        assert info.value.code == code

    def test_supported_codes_are_sorted_and_complete(self):
        codes = supported_codes()
        assert codes == tuple(sorted(codes))
        assert set(codes) == set(currencies._REGISTRY)
        assert {"USD", "BTC"} <= set(codes)


class TestAmounts:
    @pytest.mark.parametrize(
        ("code", "value", "text"),
        [
            ("USD", 150, "150.00"),
            ("USD", 0.5, "0.50"),
            ("BTC", 0.05, "0.0500"),
            ("BTC", 0.00000001, "0.0000"),
            ("EUR", Decimal(200), "200.00"),
        ],
    )
    def test_normalize_then_format(self, code, value, text):
        currency = get_currency(code)
        amount = currency.normalize_amount(value)
        assert amount == Decimal(str(value))
        assert amount.as_tuple().exponent == -currency.precision
        assert currency.format_amount(amount) == text

    @pytest.mark.parametrize(
        ("code", "value"), [("USD", 0.123), ("BTC", 1e-9), ("RUB", Decimal("1.001"))]
    )
    def test_too_precise_is_an_error_not_rounding(self, code, value):
        with pytest.raises(ValueError, match="знаков после запятой"):
            get_currency(code).normalize_amount(value)

    def test_negative_amount_is_representable(self):
        assert get_currency("USD").normalize_amount(-1) == Decimal("-1.00")

    def test_non_number_is_a_type_error(self):
        with pytest.raises(TypeError):
            get_currency("USD").normalize_amount("1")


class TestRounding:
    @pytest.mark.parametrize(
        ("code", "value", "rounding", "text"),
        [
            ("USD", "2966.8605", ROUND_HALF_UP, "2966.86"),
            ("USD", "0.005", ROUND_HALF_UP, "0.01"),
            ("USD", "0.001", ROUND_UP, "0.01"),
            ("USD", "0.009", ROUND_DOWN, "0.00"),
            ("BTC", "0.123456789", ROUND_HALF_UP, "0.12345679"),
        ],
    )
    def test_round_amount(self, code, value, rounding, text):
        assert str(get_currency(code).round_amount(Decimal(value), rounding)) == text

    def test_default_rounding_is_half_up(self):
        assert get_currency("USD").round_amount(Decimal("0.125")) == Decimal("0.13")

    def test_step(self):
        assert get_currency("USD").step == Decimal("0.01")
        assert get_currency("BTC").step == Decimal("0.00000001")
