"""Модели ядра: пользователь, кошелёк, портфель, курс."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Self

from valutatrade_hub.core import utils
from valutatrade_hub.core.currencies import BASE_CURRENCY, Currency, get_currency
from valutatrade_hub.core.exceptions import (
    InsufficientFundsError,
    RateNotAvailableError,
)

__all__ = ["EXCHANGE_RATES", "Rate", "RatesSnapshot", "User", "Wallet"]

UserRecord = dict[str, int | str]
WalletRecord = dict[str, float]

MSG_NEGATIVE_BALANCE = "Баланс не может быть отрицательным"
MSG_BAD_RATE = "Курс должен быть положительным числом"
MSG_BAD_CROSS = "Кросс-курс считается через общую валюту, а не {left} и {right}"
MSG_BAD_PAIR_KEY = "Ключ пары должен быть вида BTC_USD, получен {key!r}"

PAIR_SEPARATOR = "_"
IDENTITY_SOURCE = "identity"
STUB_SOURCE = "stub"
CROSS_SOURCE_SEPARATOR = "+"

# Фиксированные курсы из примера ТЗ на случай, когда снимок rates.json ещё пуст.
EXCHANGE_RATES: dict[str, float] = {
    "EUR_USD": 1.0786,
    "BTC_USD": 59337.21,
    "RUB_USD": 0.01016,
    "ETH_USD": 3720.00,
}


class User:
    """Пользователь системы.

    Пароль хранится только в виде хеша с солью, наружу он не отдаётся.
    """

    def __init__(
        self,
        user_id: int,
        username: str,
        hashed_password: str,
        salt: str,
        registration_date: datetime,
    ) -> None:
        """Создать пользователя из готовых полей.

        Raises:
            ValueError: если имя пользователя пустое или дата без часового пояса.
        """
        self._user_id = user_id
        self._username = utils.normalize_username(username)
        self._hashed_password = hashed_password
        self._salt = salt
        self._registration_date = utils.ensure_utc(registration_date)

    @classmethod
    def create(
        cls, user_id: int, username: str, password: str, registration_date: datetime
    ) -> "User":
        """Создать пользователя по открытому паролю.

        Соль и хеш считаются здесь, вызывающему коду они не нужны.

        Raises:
            ValueError: если имя пустое, пароль короче допустимого или дата без
                часового пояса.
        """
        password = utils.normalize_password(password)
        salt = utils.make_salt()
        return cls(
            user_id=user_id,
            username=username,
            hashed_password=utils.hash_password(password, salt),
            salt=salt,
            registration_date=registration_date,
        )

    @property
    def user_id(self) -> int:
        """Уникальный идентификатор."""
        return self._user_id

    @property
    def username(self) -> str:
        """Имя пользователя."""
        return self._username

    @username.setter
    def username(self, value: str) -> None:
        """Сменить имя пользователя.

        Raises:
            ValueError: если новое имя пустое.
        """
        self._username = utils.normalize_username(value)

    @property
    def hashed_password(self) -> str:
        """Хеш пароля с солью."""
        return self._hashed_password

    @property
    def salt(self) -> str:
        """Соль, с которой посчитан хеш."""
        return self._salt

    @property
    def registration_date(self) -> datetime:
        """Дата и время регистрации в UTC."""
        return self._registration_date

    def verify_password(self, password: str) -> bool:
        """Проверить, что пароль совпадает с сохранённым.

        Краевые пробелы убираются так же, как при регистрации.
        """
        password = utils.strip_password(password)
        return utils.verify_password(password, self._salt, self._hashed_password)

    def change_password(self, new_password: str) -> None:
        """Заменить пароль, пересчитав хеш с той же солью.

        Raises:
            ValueError: если новый пароль короче допустимого.
        """
        password = utils.normalize_password(new_password)
        self._hashed_password = utils.hash_password(password, self._salt)

    def get_user_info(self) -> UserRecord:
        """Вернуть сведения о пользователе без пароля и соли."""
        return {
            "user_id": self._user_id,
            "username": self._username,
            "registration_date": utils.format_utc(self._registration_date),
        }

    def to_dict(self) -> UserRecord:
        """Вернуть запись для users.json."""
        return {
            "user_id": self._user_id,
            "username": self._username,
            "hashed_password": self._hashed_password,
            "salt": self._salt,
            "registration_date": utils.format_utc(self._registration_date),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "User":
        """Собрать пользователя из записи users.json.

        Raises:
            KeyError: если в записи нет обязательного поля.
            ValueError: если дата регистрации не в формате ISO.
        """
        return cls(
            user_id=data["user_id"],
            username=data["username"],
            hashed_password=data["hashed_password"],
            salt=data["salt"],
            registration_date=utils.parse_utc(data["registration_date"]),
        )


class Wallet:
    """Кошелёк пользователя в одной валюте.

    Баланс хранится как Decimal с точностью валюты, поэтому остаток после
    нескольких продаж сходится ровно, без двоичного хвоста float.
    """

    def __init__(self, currency_code: str, balance: object = 0) -> None:
        """Создать кошелёк известной валюты.

        Raises:
            CurrencyNotFoundError: если кода нет в реестре валют.
            TypeError: если баланс не число.
            ValueError: если баланс отрицательный, не конечный или точнее валюты.
        """
        self._currency = get_currency(currency_code)
        self.balance = balance

    @property
    def currency(self) -> Currency:
        """Валюта кошелька."""
        return self._currency

    @property
    def currency_code(self) -> str:
        """Код валюты кошелька."""
        return self._currency.code

    @property
    def balance(self) -> Decimal:
        """Текущий баланс."""
        return self._balance

    @balance.setter
    def balance(self, value: object) -> None:
        """Установить баланс.

        Raises:
            TypeError: если значение не число.
            ValueError: если значение отрицательное, не конечное или точнее валюты.
        """
        amount = self._currency.normalize_amount(value)
        if amount < 0:
            raise ValueError(MSG_NEGATIVE_BALANCE)
        self._balance = amount

    def deposit(self, amount: object) -> None:
        """Пополнить баланс на положительную сумму.

        Raises:
            TypeError: если сумма не число.
            ValueError: если сумма не положительная или точнее валюты.
        """
        self._balance += self._positive(amount)

    def withdraw(self, amount: object) -> None:
        """Снять положительную сумму, не больше баланса.

        Raises:
            TypeError: если сумма не число.
            ValueError: если сумма не положительная или точнее валюты.
            InsufficientFundsError: если на кошельке меньше, чем требуется.
        """
        required = self._positive(amount)
        if required > self._balance:
            raise InsufficientFundsError(
                available=self._currency.format_amount(self._balance),
                required=self._currency.format_amount(required),
                code=self.currency_code,
            )
        self._balance -= required

    def get_balance_info(self) -> str:
        """Вернуть строку вида BTC: 0.0500."""
        return f"{self.currency_code}: {self._currency.format_amount(self._balance)}"

    def to_dict(self) -> WalletRecord:
        """Вернуть запись кошелька для portfolios.json, код валюты хранится в ключе."""
        return {"balance": float(self._balance)}

    @classmethod
    def from_dict(cls, currency_code: str, data: dict[str, Any]) -> "Wallet":
        """Собрать кошелёк из записи portfolios.json.

        Raises:
            KeyError: если в записи нет баланса.
        """
        return cls(currency_code, data["balance"])

    def _positive(self, amount: object) -> Decimal:
        value = self._currency.normalize_amount(amount)
        if value <= 0:
            raise ValueError(utils.MSG_AMOUNT_NOT_POSITIVE)
        return value


@dataclass(frozen=True)
class Rate:
    """Курс пары: сколько to_code стоит одна единица from_code.

    Значение не округляется: обратный и кросс-курс считаются из точного числа,
    округление только при выводе.
    """

    from_code: str
    to_code: str
    value: Decimal
    updated_at: datetime
    source: str

    def __post_init__(self) -> None:
        """Проверить курс и привести время к UTC.

        Raises:
            ValueError: если курс не положительный или время без часового пояса.
        """
        if not self.value.is_finite() or self.value <= 0:
            raise ValueError(MSG_BAD_RATE)
        object.__setattr__(self, "updated_at", utils.ensure_utc(self.updated_at))

    @property
    def key(self) -> str:
        """Ключ пары в rates.json, например BTC_USD."""
        return f"{self.from_code}{PAIR_SEPARATOR}{self.to_code}"

    def inverse(self) -> "Rate":
        """Вернуть курс в обратную сторону с теми же временем и источником."""
        return Rate(
            self.to_code, self.from_code, 1 / self.value, self.updated_at, self.source
        )

    def cross(self, other: "Rate") -> "Rate":
        """Вернуть курс from_code к other.from_code через общую валюту.

        BTC_USD и EUR_USD дают BTC_EUR. Время кросс-курса берётся у более
        старого из двух курсов, источник перечисляет оба.

        Raises:
            ValueError: если у курсов разная валюта справа.
        """
        if self.to_code != other.to_code:
            raise ValueError(MSG_BAD_CROSS.format(left=self.key, right=other.key))
        sources = [self.source]
        if other.source != self.source:
            sources.append(other.source)
        return Rate(
            self.from_code,
            other.from_code,
            self.value / other.value,
            min(self.updated_at, other.updated_at),
            CROSS_SOURCE_SEPARATOR.join(sources),
        )

    def is_fresh(self, ttl_seconds: int, now: datetime) -> bool:
        """Проверить, моложе ли курс срока годности на момент now."""
        return utils.ensure_utc(now) - self.updated_at <= timedelta(seconds=ttl_seconds)


@dataclass(frozen=True)
class RatesSnapshot:
    """Снимок rates.json: пары X_USD по ключу и время последнего обновления."""

    pairs: dict[str, Rate]
    last_refresh: datetime | None = None

    def get_rate(self, from_code: str, to_code: str) -> Rate:
        """Вернуть курс пары, выводя обратный и кросс-курс из пар X_USD.

        Raises:
            RateNotAvailableError: если в снимке нет нужного курса к USD.
        """
        if from_code == to_code:
            return Rate(
                from_code, to_code, Decimal(1), utils.utc_now(), IDENTITY_SOURCE
            )
        try:
            if to_code == BASE_CURRENCY:
                return self._to_base(from_code)
            if from_code == BASE_CURRENCY:
                return self._to_base(to_code).inverse()
            return self._to_base(from_code).cross(self._to_base(to_code))
        except KeyError:
            raise RateNotAvailableError(from_code=from_code, to_code=to_code) from None

    def _to_base(self, code: str) -> Rate:
        return self.pairs[f"{code}{PAIR_SEPARATOR}{BASE_CURRENCY}"]

    def to_dict(self) -> dict[str, Any]:
        """Вернуть снимок в формате rates.json."""
        return {
            "pairs": {
                rate.key: {
                    "rate": float(rate.value),
                    "updated_at": utils.format_utc(rate.updated_at),
                    "source": rate.source,
                }
                for rate in self.pairs.values()
            },
            "last_refresh": (
                None
                if self.last_refresh is None
                else utils.format_utc(self.last_refresh)
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Собрать снимок из rates.json.

        Raises:
            KeyError: если у пары нет курса, времени или источника.
            ValueError: если ключ пары не вида BTC_USD или время не ISO.
        """
        pairs = {}
        for key, record in data.get("pairs", {}).items():
            from_code, separator, to_code = key.partition(PAIR_SEPARATOR)
            if not separator or not from_code or not to_code:
                raise ValueError(MSG_BAD_PAIR_KEY.format(key=key))
            pairs[key] = Rate(
                from_code,
                to_code,
                utils.to_decimal(record["rate"]),
                utils.parse_utc(record["updated_at"]),
                record["source"],
            )
        last_refresh = data.get("last_refresh")
        return cls(
            pairs, None if last_refresh is None else utils.parse_utc(last_refresh)
        )

    @classmethod
    def stub(cls, now: datetime) -> Self:
        """Собрать снимок из фиксированных курсов EXCHANGE_RATES."""
        pairs = {}
        for key, value in EXCHANGE_RATES.items():
            from_code, _, to_code = key.partition(PAIR_SEPARATOR)
            pairs[key] = Rate(
                from_code, to_code, utils.to_decimal(value), now, STUB_SOURCE
            )
        return cls(pairs, now)
