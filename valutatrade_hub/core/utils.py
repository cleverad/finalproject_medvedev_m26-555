"""Вспомогательные функции ядра: пароли, валидация ввода, время.

Все функции чистые, ошибки ввода пользователя поднимаются как ValueError.
"""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime

__all__ = [
    "MIN_PASSWORD_LENGTH",
    "ensure_utc",
    "format_utc",
    "hash_password",
    "make_salt",
    "parse_utc",
    "normalize_password",
    "normalize_username",
    "strip_password",
    "utc_now",
    "verify_password",
]

MIN_PASSWORD_LENGTH = 4
SALT_BYTES = 8
HASH_NAME = "sha256"
PBKDF2_ITERATIONS = 100_000
UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

MSG_EMPTY_USERNAME = "Имя пользователя не может быть пустым"
MSG_SHORT_PASSWORD = f"Пароль должен быть не короче {MIN_PASSWORD_LENGTH} символов"
MSG_NAIVE_DATETIME = "Время без часового пояса недопустимо, ожидается UTC"


def make_salt() -> str:
    """Вернуть случайную соль в шестнадцатеричном виде."""
    return secrets.token_hex(SALT_BYTES)


def hash_password(password: str, salt: str) -> str:
    """Вернуть hex-хеш пароля с солью.

    Используется PBKDF2 поверх SHA-256: та же пара соль плюс SHA-256, что требует
    ТЗ, но устойчивее к перебору, чем один вызов sha256.
    """
    digest = hashlib.pbkdf2_hmac(
        HASH_NAME, password.encode(), salt.encode(), PBKDF2_ITERATIONS
    )
    return digest.hex()


def verify_password(password: str, salt: str, hashed_password: str) -> bool:
    """Проверить, что пароль с этой солью даёт сохранённый хеш."""
    return hmac.compare_digest(hash_password(password, salt), hashed_password)


def normalize_username(username: object) -> str:
    """Вернуть имя пользователя без краевых пробелов.

    Raises:
        ValueError: если имя не строка или после очистки пустое.
    """
    if not isinstance(username, str) or not username.strip():
        raise ValueError(MSG_EMPTY_USERNAME)
    return username.strip()


def strip_password(password: str) -> str:
    """Убрать краевые пробелы пароля.

    Пользователь часто не замечает пробел по краям при регистрации и потом не
    может войти. Поэтому пароль чистится одинаково при регистрации, входе и смене.
    """
    return password.strip()


def normalize_password(password: object) -> str:
    """Вернуть пароль без краевых пробелов, если он достаточно длинный.

    Raises:
        ValueError: если пароль не строка или после очистки короче MIN_PASSWORD_LENGTH.
    """
    if not isinstance(password, str):
        raise ValueError(MSG_SHORT_PASSWORD)
    password = strip_password(password)
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(MSG_SHORT_PASSWORD)
    return password


def utc_now() -> datetime:
    """Вернуть текущее время в UTC."""
    return datetime.now(UTC)


def ensure_utc(moment: datetime) -> datetime:
    """Вернуть момент времени в UTC.

    Наивное время отвергается: astimezone считал бы его локальным и молча
    сдвинул бы на смещение зоны.

    Raises:
        ValueError: если у момента нет часового пояса.
    """
    if moment.tzinfo is None:
        raise ValueError(MSG_NAIVE_DATETIME)
    return moment.astimezone(UTC)


def format_utc(moment: datetime) -> str:
    """Записать момент времени в формате 2026-10-30T09:01:37Z.

    Raises:
        ValueError: если у момента нет часового пояса.
    """
    return ensure_utc(moment).strftime(UTC_FORMAT)


def parse_utc(text: str) -> datetime:
    """Прочитать ISO-время, наивное время считается UTC.

    Raises:
        ValueError: если строка не является ISO-временем.
    """
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return ensure_utc(moment)
