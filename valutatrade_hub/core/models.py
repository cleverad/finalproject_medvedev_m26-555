"""Модели ядра: пользователь, кошелёк, портфель, курс."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from valutatrade_hub.core import utils
from valutatrade_hub.core.currencies import Currency, get_currency
from valutatrade_hub.core.exceptions import InsufficientFundsError

__all__ = ["User", "Wallet"]

UserRecord = dict[str, int | str]
WalletRecord = dict[str, float]

MSG_NEGATIVE_BALANCE = "Баланс не может быть отрицательным"


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
