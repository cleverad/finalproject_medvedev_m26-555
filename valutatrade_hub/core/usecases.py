"""Бизнес-логика: регистрация, вход, сделки, курсы.

Функции не хранят состояние, данные берут через синглтон DatabaseManager.
"""

from valutatrade_hub.core import utils
from valutatrade_hub.core.currencies import BASE_CURRENCY
from valutatrade_hub.core.exceptions import (
    InvalidPasswordError,
    UsernameTakenError,
    UserNotFoundError,
)
from valutatrade_hub.core.models import Portfolio, User, Wallet
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.settings import SettingsLoader

__all__ = ["login", "register"]


def register(username: str, password: str) -> User:
    """Зарегистрировать пользователя и завести ему портфель с кошельком USD.

    Стартовый баланс USD берётся из настройки start_balance_usd. Портфель
    пишется раньше пользователя: сбой между двумя записями не оставит
    пользователя без портфеля, а портфель без пользователя безвреден.

    Raises:
        ValueError: если имя пустое или пароль короче допустимого.
        UsernameTakenError: если имя уже занято.
    """
    db = DatabaseManager()
    user = User.create(db.next_user_id(), username, password, utils.utc_now())
    if db.find_user(user.username) is not None:
        raise UsernameTakenError(username=user.username)
    start_balance = SettingsLoader().get("start_balance_usd")
    usd = Wallet(BASE_CURRENCY, start_balance)
    db.save_portfolio(Portfolio(user.user_id, {BASE_CURRENCY: usd}, user=user))
    db.save_users([*db.get_users(), user])
    return user


def login(username: str, password: str) -> User:
    """Найти пользователя и проверить пароль.

    Raises:
        ValueError: если имя пустое.
        UserNotFoundError: если пользователя с таким именем нет.
        InvalidPasswordError: если пароль не совпал.
    """
    username = utils.normalize_username(username)
    user = DatabaseManager().find_user(username)
    if user is None:
        raise UserNotFoundError(username=username)
    if not user.verify_password(password):
        raise InvalidPasswordError()
    return user
