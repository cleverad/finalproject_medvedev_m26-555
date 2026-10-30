"""Валюты: абстрактный Currency, наследники Fiat и Crypto, реестр по коду.

Точность хранения сумм задаётся видом валюты: у фиата два знака, у крипты восемь.
Реестр принимает только коды в верхнем регистре, приведение к нему делает CLI.
"""

import re
from abc import ABC, abstractmethod
from decimal import ROUND_HALF_UP, Decimal

from valutatrade_hub.core import utils
from valutatrade_hub.core.exceptions import CurrencyNotFoundError

__all__ = [
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
MSG_TOO_PRECISE = (
    "Сумма в {code} не может иметь больше {precision} знаков после запятой"
)


class Currency(ABC):
    """Валюта: код, имя и точность, с которой хранятся суммы в ней."""

    precision: int
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
        """Вернуть сумму в этой валюте как Decimal с точностью хранения.

        Лишние знаки это ошибка, а не молчаливое округление: 0.123 USD не бывает.

        Raises:
            TypeError: если значение не число.
            ValueError: если значение не конечное или точнее, чем допускает валюта.
        """
        amount = utils.to_decimal(value)
        if amount != amount.quantize(self.step):
            raise ValueError(
                MSG_TOO_PRECISE.format(code=self.code, precision=self.precision)
            )
        return amount.quantize(self.step)

    def round_amount(self, value: Decimal, rounding: str = ROUND_HALF_UP) -> Decimal:
        """Округлить вычисленную сумму до точности валюты заданным способом.

        Для ввода пользователя есть normalize_amount, он лишние знаки отвергает.
        """
        return value.quantize(self.step, rounding=rounding)

    @property
    def step(self) -> Decimal:
        """Минимальная сумма в валюте: 0.01 для фиата, 0.00000001 для крипты."""
        return Decimal(1).scaleb(-self.precision)

    def format_amount(self, amount: Decimal) -> str:
        """Записать сумму с точностью вывода: 150.00 для фиата, 0.0500 для крипты."""
        return f"{amount:.{self.display_precision}f}"


class FiatCurrency(Currency):
    """Государственная валюта с указанием страны или зоны эмиссии."""

    precision = 2
    display_precision = 2

    def __init__(self, name: str, code: str, issuing_country: str) -> None:
        super().__init__(name, code)
        self.issuing_country = issuing_country

    def get_display_info(self) -> str:
        return f"[FIAT] {self.code} — {self.name} (Issuing: {self.issuing_country})"


class CryptoCurrency(Currency):
    """Криптовалюта с алгоритмом консенсуса и последней известной капитализацией."""

    precision = 8
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
