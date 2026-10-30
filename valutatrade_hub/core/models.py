"""Модели ядра: пользователь, кошелёк, портфель, курс."""

from datetime import datetime
from typing import Any

from valutatrade_hub.core import utils

__all__ = ["User"]

UserRecord = dict[str, int | str]


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
