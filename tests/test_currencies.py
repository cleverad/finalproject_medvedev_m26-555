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
