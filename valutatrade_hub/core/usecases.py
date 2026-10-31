"""Бизнес-логика: регистрация, вход, сделки, курсы.

Функции не хранят состояние, данные берут через синглтон DatabaseManager.
"""

from decimal import Decimal

from valutatrade_hub.core import utils
from valutatrade_hub.core.currencies import (
    AMOUNT_STEP,
    BASE_CURRENCY,
    CryptoCurrency,
    Currency,
    get_currency,
)
from valutatrade_hub.core.decorators import log_action
from valutatrade_hub.core.exceptions import (
    ApiRequestError,
    CurrencyNotFoundError,
    InvalidPasswordError,
    RateNotAvailableError,
    UsernameTakenError,
    UserNotFoundError,
    WalletNotFoundError,
)
from valutatrade_hub.core.models import (
    BUY_ACTION,
    SELL_ACTION,
    Portfolio,
    PortfolioValuation,
    Rate,
    RatesSnapshot,
    TradeResult,
    User,
    Wallet,
)
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.settings import SettingsLoader
from valutatrade_hub.parser_service.updater import UpdateResult, refresh_rates

MSG_UNKNOWN_BASE = "Неизвестная базовая валюта '{code}'"
MSG_BASE_CURRENCY_TRADE = "Нельзя торговать базовой валютой {code}"
MSG_TRADE_TOO_SMALL = "Стоимость сделки меньше {step} {code}"
MSG_EMPTY_CACHE = (
    "Локальный кеш курсов пуст. Выполните 'update-rates', чтобы загрузить данные."
)
MSG_NOT_IN_CACHE = "Курс для '{code}' не найден в кеше."
MSG_BAD_TOP = "'top' должен быть положительным целым числом"

__all__ = [
    "buy",
    "get_rate",
    "login",
    "register",
    "sell",
    "show_portfolio",
    "show_rates",
    "update_rates",
]


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
    Пока rates.json нет, оценить можно только базовую валюту.

    Raises:
        UserNotFoundError: если пользователя с таким id нет.
        ValueError: если базовой валюты нет в реестре.
        RateNotAvailableError: если для какой-то валюты портфеля нет курса.
    """
    db = DatabaseManager()
    _, portfolio = _user_portfolio(db, user_id)
    try:
        get_currency(base_currency)
    except CurrencyNotFoundError:
        raise ValueError(MSG_UNKNOWN_BASE.format(code=base_currency)) from None
    return portfolio.get_valuation(base_currency, _snapshot(db))


def _user_portfolio(db: DatabaseManager, user_id: int) -> tuple[User, Portfolio]:
    """Найти пользователя и его портфель, пустой портфель при отсутствии записи.

    Raises:
        UserNotFoundError: если пользователя с таким id нет.
    """
    user = db.find_user_by_id(user_id)
    if user is None:
        raise UserNotFoundError(username=user_id)
    portfolio = db.get_portfolio(user) or Portfolio(user.user_id, user=user)
    return user, portfolio


def get_rate(from_code: str, to_code: str) -> Rate:
    """Вернуть свежий курс пары, при устаревшем снимке обновить его через парсер.

    Курс старше rates_ttl_seconds или отсутствующий в снимке считается
    устаревшим: тогда запускается обновление и снимок перечитывается.

    Raises:
        CurrencyNotFoundError: если один из кодов не в реестре.
        ApiRequestError: если обновление не удалось, или курса нет и после
            него, а источники сообщали об ошибках.
        RateNotAvailableError: если курса нет и после удачного обновления.
    """
    get_currency(from_code)
    get_currency(to_code)
    db = DatabaseManager()
    rate = _fresh_rate(db, from_code, to_code)
    if rate is not None:
        return rate
    result = refresh_rates()
    rate = _fresh_rate(db, from_code, to_code)
    if rate is not None:
        return rate
    if result.errors:
        raise ApiRequestError(reason=result.describe_errors())
    raise RateNotAvailableError(from_code=from_code, to_code=to_code)


def _snapshot(db: DatabaseManager) -> RatesSnapshot:
    """Вернуть снимок rates.json, пустой, если файла ещё нет."""
    return db.get_rates() or RatesSnapshot({})


def _fresh_rate(db: DatabaseManager, from_code: str, to_code: str) -> Rate | None:
    """Вернуть курс из снимка, если он есть и не старше TTL, иначе None."""
    try:
        rate = _snapshot(db).get_rate(from_code, to_code)
    except RateNotAvailableError:
        return None
    ttl = SettingsLoader().get("rates_ttl_seconds")
    return rate if rate.is_fresh(ttl, utils.utc_now()) else None


@log_action(BUY_ACTION)
def buy(user_id: int, currency_code: str, amount: object) -> TradeResult:
    """Купить валюту за базовую по свежему курсу.

    Проверки идут до изменений, изменения до единственной записи портфеля.

    Raises:
        UserNotFoundError: если пользователя с таким id нет.
        CurrencyNotFoundError: если код валюты не в реестре.
        ValueError: если валюта базовая, сумма не положительная или стоимость
            сделки меньше шага учёта.
        RateNotAvailableError: если курса к базовой валюте нет или он устарел.
        InsufficientFundsError: если базовой валюты не хватает на стоимость.
    """
    currency, amount = _tradable(currency_code, amount)
    db = DatabaseManager()
    user, portfolio = _user_portfolio(db, user_id)
    rate = get_rate(currency.code, BASE_CURRENCY)
    base_wallet = portfolio.add_currency(BASE_CURRENCY)
    cost = _trade_value(amount, rate, base_wallet.currency)
    wallet = portfolio.add_currency(currency.code)
    before, base_before = wallet.balance, base_wallet.balance
    base_wallet.withdraw(cost)
    wallet.deposit(amount)
    db.save_portfolio(portfolio)
    return _trade_result(
        BUY_ACTION, user, wallet, base_wallet, rate, amount, before, base_before, cost
    )


@log_action(SELL_ACTION)
def sell(user_id: int, currency_code: str, amount: object) -> TradeResult:
    """Продать валюту за базовую по свежему курсу.

    Кошелёк валюты должен существовать, после продажи он остаётся и с нулём.

    Raises:
        UserNotFoundError: если пользователя с таким id нет.
        CurrencyNotFoundError: если код валюты не в реестре.
        ValueError: если валюта базовая, сумма не положительная или выручка
            меньше шага учёта.
        WalletNotFoundError: если кошелька этой валюты в портфеле нет.
        RateNotAvailableError: если курса к базовой валюте нет или он устарел.
        InsufficientFundsError: если на кошельке меньше, чем продаётся.
    """
    currency, amount = _tradable(currency_code, amount)
    db = DatabaseManager()
    user, portfolio = _user_portfolio(db, user_id)
    wallet = portfolio.get_wallet(currency.code)
    if wallet is None:
        raise WalletNotFoundError(code=currency.code)
    rate = get_rate(currency.code, BASE_CURRENCY)
    base_wallet = portfolio.add_currency(BASE_CURRENCY)
    proceeds = _trade_value(amount, rate, base_wallet.currency)
    before, base_before = wallet.balance, base_wallet.balance
    wallet.withdraw(amount)
    base_wallet.deposit(proceeds)
    db.save_portfolio(portfolio)
    return _trade_result(
        SELL_ACTION,
        user,
        wallet,
        base_wallet,
        rate,
        amount,
        before,
        base_before,
        proceeds,
    )


def _tradable(currency_code: str, amount: object) -> tuple[Currency, Decimal]:
    """Вернуть валюту сделки и её сумму с шагом учёта.

    Raises:
        CurrencyNotFoundError: если код валюты не в реестре.
        ValueError: если валюта базовая или сумма не положительная.
    """
    currency = get_currency(currency_code)
    if currency.code == BASE_CURRENCY:
        raise ValueError(MSG_BASE_CURRENCY_TRADE.format(code=BASE_CURRENCY))
    return currency, currency.positive_amount(amount)


def _trade_result(
    action: str,
    user: User,
    wallet: Wallet,
    base_wallet: Wallet,
    rate: Rate,
    amount: Decimal,
    before: Decimal,
    base_before: Decimal,
    value: Decimal,
) -> TradeResult:
    """Собрать итог сделки из кошельков после изменения и балансов до него."""
    return TradeResult(
        action,
        user.username,
        wallet.currency,
        amount,
        rate,
        before,
        wallet.balance,
        base_wallet.currency,
        base_before,
        base_wallet.balance,
        value,
    )


def _trade_value(amount: Decimal, rate: Rate, base: Currency) -> Decimal:
    """Вернуть стоимость сделки в базовой валюте, округлённую к ближайшему шагу.

    Raises:
        ValueError: если стоимость округляется в ноль: такую сделку нечем оплатить
            и не за что зачислить.
    """
    value = base.round_amount(amount * rate.value)
    if value <= 0:
        step = base.format_amount(AMOUNT_STEP)
        raise ValueError(MSG_TRADE_TOO_SMALL.format(step=step, code=base.code))
    return value


def update_rates(source: str | None = None) -> UpdateResult:
    """Обновить кеш курсов из всех источников или из одного по псевдониму.

    Raises:
        ValueError: если псевдоним источника неизвестен.
        ApiRequestError: если не ответил ни один источник.
    """
    return refresh_rates(None if source is None else (source,))


def show_rates(
    currency: str | None = None,
    top: int | None = None,
    base_currency: str = BASE_CURRENCY,
) -> RatesSnapshot:
    """Вернуть курсы из кеша к базовой валюте с фильтрами, без проверки TTL.

    Пары идут по алфавиту. С currency остаётся одна пара, с top остаются
    top самых дорогих криптовалют по убыванию курса. Базовая валюта не USD
    пересчитывается через кросс-курс.

    Raises:
        ValueError: если кеш пуст, валюты нет в кеше или top не положительный.
        CurrencyNotFoundError: если базовой валюты нет в реестре.
        RateNotAvailableError: если курс к базовой валюте нельзя вывести.
    """
    if top is not None and top <= 0:
        raise ValueError(MSG_BAD_TOP)
    base = get_currency(base_currency).code
    cached = _snapshot(DatabaseManager())
    codes = {rate.from_code for rate in cached.pairs.values()} | {BASE_CURRENCY}
    if not cached.pairs:
        raise ValueError(MSG_EMPTY_CACHE)
    if currency is not None:
        if currency not in codes or currency == base:
            raise ValueError(MSG_NOT_IN_CACHE.format(code=currency))
        codes = {currency}
    rates = [cached.get_rate(code, base) for code in sorted(codes) if code != base]
    if top is not None:
        rates = sorted(
            (rate for rate in rates if _is_crypto(rate.from_code)),
            key=lambda rate: rate.value,
            reverse=True,
        )[:top]
    return RatesSnapshot({rate.key: rate for rate in rates}, cached.last_refresh)


def _is_crypto(code: str) -> bool:
    """Проверить по реестру, криптовалюта ли это; чужой код не криптовалюта."""
    try:
        return isinstance(get_currency(code), CryptoCurrency)
    except CurrencyNotFoundError:
        return False
