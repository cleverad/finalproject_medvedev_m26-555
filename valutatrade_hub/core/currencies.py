"""Валюты: абстрактный Currency, наследники Fiat и Crypto, реестр по коду.

Суммы во всех валютах хранятся с общим шагом AMOUNT_STEP, а показываются с точностью
вида валюты: у фиата два знака, у крипты четыре. Реестр принимает только коды
в верхнем регистре, приведение к нему делает CLI.
"""

import re
from abc import ABC, abstractmethod
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation

from valutatrade_hub.core import utils
from valutatrade_hub.core.exceptions import CurrencyNotFoundError

__all__ = [
    "AMOUNT_DECIMALS",
    "AMOUNT_STEP",
    "BASE_CURRENCY",
    "CryptoCurrency",
    "Currency",
    "FiatCurrency",
    "get_currency",
    "supported_codes",
]

BASE_CURRENCY = "USD"
CODE_PATTERN = re.compile(r"[A-Z]{2,5}")

MSG_BAD_CODE = (
    "Код валюты должен быть из 2–5 заглавных латинских букв, получен {code!r}"
)
MSG_EMPTY_NAME = "Имя валюты не может быть пустым"
MSG_AMOUNT_TOO_LARGE = "Сумма слишком велика для {code}"

# Общий шаг учёта для всех валют, как на биржах. Точность показа у видов валют
# своя и на хранение не влияет. Стоимость сделки считается без промежуточных округлений.
AMOUNT_DECIMALS = 8
AMOUNT_STEP = Decimal(1).scaleb(-AMOUNT_DECIMALS)


class Currency(ABC):
    """Валюта: код, имя и число знаков, с которым суммы в ней показываются."""

    display_precision: int

    def __init__(self, name: str, code: str) -> None:
        """Проверить инварианты и запомнить код и имя.

        Raises:
            ValueError: если код не из 2–5 заглавных букв или имя пустое.
        """
        if not isinstance(code, str) or CODE_PATTERN.fullmatch(code) is None:
            raise ValueError(MSG_BAD_CODE.format(code=code))
        if not isinstance(name, str) or not name.strip():
            raise ValueError(MSG_EMPTY_NAME)
        self.name = name.strip()
        self.code = code

    @abstractmethod
    def get_display_info(self) -> str:
        """Вернуть строку для вывода и логов."""

    def normalize_amount(self, value: object) -> Decimal:
        """Вернуть сумму как Decimal с шагом учёта AMOUNT_STEP.

        Знаки мельче шага отбрасываются к нулю, поэтому сумма никогда не
        становится больше введённой.

        Raises:
            TypeError: если значение не число.
            ValueError: если значение не конечное или не помещается в Decimal.
        """
        return self._quantize(utils.to_decimal(value), ROUND_DOWN)

    def positive_amount(self, value: object) -> Decimal:
        """Вернуть сумму операции: как normalize_amount, но строго больше нуля.

        Raises:
            TypeError: если значение не число.
            ValueError: если сумма не положительная, в том числе мельче шага учёта.
        """
        amount = self.normalize_amount(value)
        if amount <= 0:
            raise ValueError(utils.MSG_AMOUNT_NOT_POSITIVE)
        return amount

    def round_amount(self, value: Decimal) -> Decimal:
        """Округлить вычисленную сумму к ближайшему шагу учёта.

        Для ввода пользователя есть normalize_amount, он отбрасывает вниз.

        Raises:
            ValueError: если значение не помещается в Decimal.
        """
        return self._quantize(value, ROUND_HALF_UP)

    def _quantize(self, value: Decimal, rounding: str) -> Decimal:
        try:
            return value.quantize(AMOUNT_STEP, rounding=rounding)
        except InvalidOperation:
            raise ValueError(MSG_AMOUNT_TOO_LARGE.format(code=self.code)) from None

    def format_amount(self, amount: Decimal) -> str:
        """Записать сумму не короче точности показа и не длиннее шага учёта.

        Хвостовые нули убираются: 150.00, 0.0500, 0.00000001.
        """
        text = f"{amount:.{AMOUNT_DECIMALS}f}"
        return utils.trim_zeros(text, self.display_precision)

    def format_value(self, amount: Decimal) -> str:
        """Записать стоимость с разделителем тысяч: 3,329.00."""
        text = f"{amount:,.{AMOUNT_DECIMALS}f}"
        return utils.trim_zeros(text, self.display_precision)


class FiatCurrency(Currency):
    """Государственная валюта с указанием страны или зоны эмиссии."""

    display_precision = 2

    def __init__(self, name: str, code: str, issuing_country: str) -> None:
        super().__init__(name, code)
        self.issuing_country = issuing_country

    def get_display_info(self) -> str:
        return f"[FIAT] {self.code} — {self.name} (Issuing: {self.issuing_country})"


class CryptoCurrency(Currency):
    """Криптовалюта с алгоритмом консенсуса и последней известной капитализацией."""

    display_precision = 4

    def __init__(self, name: str, code: str, algorithm: str, market_cap: float) -> None:
        super().__init__(name, code)
        self.algorithm = algorithm
        self.market_cap = market_cap

    def get_display_info(self) -> str:
        return (
            f"[CRYPTO] {self.code} — {self.name} "
            f"(Algo: {self.algorithm}, MCAP: {utils.format_mcap(self.market_cap)})"
        )


_REGISTRY: dict[str, Currency] = {
    currency.code: currency
    for currency in (
        FiatCurrency("US Dollar", "USD", "United States"),
        FiatCurrency("Euro", "EUR", "Eurozone"),
        FiatCurrency("Pound Sterling", "GBP", "United Kingdom"),
        FiatCurrency("Russian Ruble", "RUB", "Russia"),
        CryptoCurrency("Bitcoin", "BTC", "SHA-256", 1.12e12),
        CryptoCurrency("Ethereum", "ETH", "Ethash", 4.5e11),
        CryptoCurrency("Solana", "SOL", "Proof of History", 8.0e10),
    )
}


def get_currency(code: str) -> Currency:
    """Вернуть валюту из реестра по коду.

    Raises:
        CurrencyNotFoundError: если кода нет в реестре.
    """
    try:
        return _REGISTRY[code]
    except KeyError:
        raise CurrencyNotFoundError(code=code) from None


def supported_codes() -> tuple[str, ...]:
    """Вернуть коды всех валют реестра по алфавиту."""
    return tuple(sorted(_REGISTRY))
