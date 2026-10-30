"""Бизнес-логика: регистрация, вход, сделки, курсы.

Функции не хранят состояние, данные берут через синглтон DatabaseManager.
"""

from valutatrade_hub.core import utils
from valutatrade_hub.core.currencies import BASE_CURRENCY, get_currency
from valutatrade_hub.core.exceptions import (
    CurrencyNotFoundError,
    InvalidPasswordError,
    RateNotAvailableError,
    UsernameTakenError,
    UserNotFoundError,
)
from valutatrade_hub.core.models import (
    Portfolio,
    PortfolioValuation,
    Rate,
    RatesSnapshot,
    User,
    Wallet,
)
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.settings import SettingsLoader

MSG_UNKNOWN_BASE = "Неизвестная базовая валюта '{code}'"

__all__ = ["get_rate", "login", "register", "show_portfolio"]


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


def show_portfolio(
    user_id: int, base_currency: str = BASE_CURRENCY
) -> PortfolioValuation:
    """Оценить портфель пользователя в базовой валюте по снимку rates.json.

    Срок годности курсов здесь не проверяется: показ портфеля не сделка.
    Пока rates.json нет, берётся заглушка EXCHANGE_RATES.

    Raises:
        UserNotFoundError: если пользователя с таким id нет.
        ValueError: если базовой валюты нет в реестре.
        RateNotAvailableError: если для какой-то валюты портфеля нет курса.
    """
    db = DatabaseManager()
    user = db.find_user_by_id(user_id)
    if user is None:
        raise UserNotFoundError(username=user_id)
    try:
        get_currency(base_currency)
    except CurrencyNotFoundError:
        raise ValueError(MSG_UNKNOWN_BASE.format(code=base_currency)) from None
    portfolio = db.get_portfolio(user) or Portfolio(user.user_id, user=user)
    return portfolio.get_valuation(base_currency, db.get_rates())


def get_rate(from_code: str, to_code: str) -> Rate:
    """Вернуть свежий курс пары по снимку rates.json.

    Курс старше rates_ttl_seconds считается отсутствующим. Пока rates.json нет,
    берётся заглушка EXCHANGE_RATES.

    Raises:
        CurrencyNotFoundError: если один из кодов не в реестре.
        RateNotAvailableError: если курса нет в снимке или он устарел.
    """
    get_currency(from_code)
    get_currency(to_code)
    now = utils.utc_now()
    rates = DatabaseManager().get_rates() or RatesSnapshot.stub(now)
    rate = rates.get_rate(from_code, to_code)
    if not rate.is_fresh(SettingsLoader().get("rates_ttl_seconds"), now):
        raise RateNotAvailableError(from_code=from_code, to_code=to_code)
    return rate
