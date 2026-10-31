from decimal import Decimal

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
    def test_fiat_display_format(self):
        usd = FiatCurrency("US Dollar", "USD", "United States")
        assert (
            usd.get_display_info() == "[FIAT] USD — US Dollar (Issuing: United States)"
        )

    def test_crypto_display_format(self):
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

    def test_display_precision_by_kind(self):
        assert FiatCurrency.display_precision == 2
        assert CryptoCurrency.display_precision == 4

    def test_one_amount_step_for_all_currencies(self):
        step = currencies.AMOUNT_STEP
        assert step == Decimal("0.00000001")
        assert currencies.AMOUNT_DECIMALS == 8


class TestRegistry:
    def test_base_currency_is_usd(self):
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
            ("BTC", 0.00000001, "0.00000001"),
            ("BTC", 0.123, "0.1230"),
            ("BTC", Decimal("0.12345678"), "0.12345678"),
            ("BTC", 0, "0.0000"),
            ("EUR", Decimal(200), "200.00"),
        ],
    )
    def test_normalize_then_format(self, code, value, text):
        currency = get_currency(code)
        amount = currency.normalize_amount(value)
        assert amount == Decimal(str(value))
        assert amount.as_tuple().exponent == -currencies.AMOUNT_DECIMALS
        assert currency.format_amount(amount) == text

    @pytest.mark.parametrize(
        ("code", "value", "text"),
        [
            ("USD", 0.129, "0.12900000"),
            ("USD", 0.123456789, "0.12345678"),
            ("BTC", 1e-9, "0E-8"),
            ("BTC", 0.123456789, "0.12345678"),
            ("RUB", Decimal("1.000000019"), "1.00000001"),
        ],
    )
    def test_finer_than_step_is_cut_towards_zero(self, code, value, text):
        assert str(get_currency(code).normalize_amount(value)) == text

    def test_too_large_for_decimal(self):
        with pytest.raises(ValueError, match="слишком велика"):
            get_currency("BTC").normalize_amount(Decimal("1e30"))
        with pytest.raises(ValueError, match="слишком велика"):
            get_currency("BTC").round_amount(Decimal("1e30"))

    @pytest.mark.parametrize(
        ("code", "value", "text"),
        [
            ("USD", "3329", "3,329.00"),
            ("USD", "2965", "2,965.00"),
            ("USD", "150", "150.00"),
            ("USD", "1234567.891", "1,234,567.891"),
            ("BTC", "12345.6789", "12,345.6789"),
            ("BTC", "1000.00000001", "1,000.00000001"),
        ],
    )
    def test_format_value_with_thousands_separator(self, code, value, text):
        assert get_currency(code).format_value(Decimal(value)) == text

    def test_negative_amount_is_representable(self):
        assert get_currency("USD").normalize_amount(-1) == Decimal("-1.00")

    def test_positive_amount(self):
        assert get_currency("BTC").positive_amount(0.05) == Decimal("0.05000000")

    @pytest.mark.parametrize("value", [0, -1, Decimal("-0.01"), 1e-9])
    def test_positive_amount_rejects_zero_and_negative(self, value):
        with pytest.raises(ValueError, match="'amount' должен быть положительным"):
            get_currency("USD").positive_amount(value)

    def test_non_number_is_a_type_error(self):
        with pytest.raises(TypeError):
            get_currency("USD").normalize_amount("1")


class TestRounding:
    @pytest.mark.parametrize(
        ("code", "value", "text"),
        [
            ("USD", "2966.8605", "2966.86050000"),
            ("USD", "0.000000005", "1E-8"),
            ("USD", "0.000000004", "0E-8"),
            ("BTC", "0.123456789", "0.12345679"),
            ("BTC", "0.123456785", "0.12345679"),
        ],
    )
    def test_round_amount_to_the_nearest_step(self, code, value, text):
        assert str(get_currency(code).round_amount(Decimal(value))) == text
