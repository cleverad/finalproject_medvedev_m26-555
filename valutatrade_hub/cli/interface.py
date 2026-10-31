"""Командный интерфейс ValutaTrade Hub.

Интерфейс только разбирает команды, вызывает usecases и печатает результат.
Таблица COMMANDS служит и справкой, и диспетчером.
"""

import shlex
from collections.abc import Callable
from dataclasses import dataclass

from prettytable import PrettyTable

from valutatrade_hub.core import usecases, utils
from valutatrade_hub.core.currencies import BASE_CURRENCY, supported_codes
from valutatrade_hub.core.exceptions import CurrencyNotFoundError, ValutaTradeError
from valutatrade_hub.core.models import TradeResult, User

__all__ = ["COMMANDS", "Command", "Session", "execute", "parse_options", "run"]

PROMPT = "> "
OPTION_PREFIX = "--"

MSG_WELCOME = "ValutaTrade Hub. Введите help для списка команд, exit для выхода."
MSG_LOGIN_REQUIRED = "Сначала выполните login"
MSG_UNKNOWN_COMMAND = "Неизвестная команда '{name}'. Введите help"
MSG_PARSE_FAILED = "Не удалось разобрать команду: {reason}"
MSG_USAGE = "Использование: {usage}"
MSG_REGISTERED = (
    "Пользователь '{username}' зарегистрирован (id={user_id}). "
    "Войдите: login --username {username} --password ****"
)
MSG_LOGGED_IN = "Вы вошли как '{username}'"
MSG_LOGGED_OUT = "Вы вышли"
MSG_MISSING_OPTION = "Не указан аргумент '--{name}'"
MSG_OPTION_WITHOUT_VALUE = "Аргумент '--{name}' указан без значения"
MSG_UNKNOWN_OPTION = "Неизвестный аргумент '--{name}'"
MSG_REPEATED_OPTION = "Аргумент '--{name}' повторяется"
MSG_EXTRA_VALUE = "Лишнее значение '{value}'"
MSG_PORTFOLIO_HEADER = "Портфель пользователя '{username}' (база: {base}):"
MSG_NO_WALLETS = "Кошельков пока нет"
MSG_TOTAL = "ИТОГО: {total} {base}"
MSG_RATE = "Курс {from_code}→{to_code}: {rate} (обновлено: {updated_at})"
MSG_INVERSE_RATE = "Обратный курс {from_code}→{to_code}: {rate}"
MSG_SUPPORTED_CODES = "Поддерживаемые коды: {codes}"
CODES_SEPARATOR = ", "
MSG_BOUGHT = "Покупка выполнена: {amount} {code} по курсу {rate} {base}/{code}"
MSG_CHANGES = "Изменения в портфеле:"
MSG_CHANGE_LINE = "- {code}: было {before} → стало {after}"
MSG_BUY_VALUE = "Оценочная стоимость покупки: {value} {base}"
COLUMN_CURRENCY = "Валюта"
COLUMN_BALANCE = "Баланс"
COLUMN_VALUE = "Стоимость, {base}"
ALIGN_LEFT = "l"
ALIGN_RIGHT = "r"


class Session:
    """Состояние одного запуска интерфейса: кто вошёл и работает ли цикл."""

    def __init__(self) -> None:
        self.user: User | None = None
        self.running = True

    def login(self, user: User) -> None:
        """Запомнить вошедшего пользователя, заменив предыдущего."""
        self.user = user

    def logout(self) -> None:
        """Забыть пользователя."""
        self.user = None

    def require(self) -> User:
        """Вернуть вошедшего пользователя.

        Raises:
            ValueError: если никто не вошёл.
        """
        if self.user is None:
            raise ValueError(MSG_LOGIN_REQUIRED)
        return self.user


Options = dict[str, str]
Handler = Callable[[Session, Options], None]


@dataclass(frozen=True)
class Command:
    """Описание команды: обработчик, строка использования и допустимые аргументы."""

    handler: Handler
    usage: str
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    positional: str | None = None
    requires_login: bool = False


def parse_options(tokens: list[str], command: Command) -> Options:
    """Разобрать аргументы вида --имя значение по описанию команды.

    Raises:
        ValueError: если аргумент неизвестен, повторяется, без значения,
            обязательный пропущен или встретилось лишнее значение.
    """
    allowed = set(command.required) | set(command.optional)
    options: Options = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if not token.startswith(OPTION_PREFIX):
            if command.positional is None or command.positional in options:
                raise ValueError(MSG_EXTRA_VALUE.format(value=token))
            options[command.positional] = token
            index += 1
            continue
        name = token.removeprefix(OPTION_PREFIX)
        if name not in allowed:
            raise ValueError(MSG_UNKNOWN_OPTION.format(name=name))
        if name in options:
            raise ValueError(MSG_REPEATED_OPTION.format(name=name))
        value = tokens[index + 1] if index + 1 < len(tokens) else None
        if value is None or value.startswith(OPTION_PREFIX):
            raise ValueError(MSG_OPTION_WITHOUT_VALUE.format(name=name))
        options[name] = value
        index += 2
    for name in command.required:
        if name not in options:
            raise ValueError(MSG_MISSING_OPTION.format(name=name))
    return options


def cmd_register(session: Session, options: Options) -> None:
    """Зарегистрировать пользователя."""
    user = usecases.register(options["username"], options["password"])
    print(MSG_REGISTERED.format(username=user.username, user_id=user.user_id))


def cmd_login(session: Session, options: Options) -> None:
    """Войти в систему."""
    user = usecases.login(options["username"], options["password"])
    session.login(user)
    print(MSG_LOGGED_IN.format(username=user.username))


def cmd_logout(session: Session, options: Options) -> None:
    """Выйти из системы."""
    session.logout()
    print(MSG_LOGGED_OUT)


def cmd_show_portfolio(session: Session, options: Options) -> None:
    """Показать кошельки и их стоимость в базовой валюте."""
    user = session.require()
    base = utils.normalize_currency_code(options.get("base", BASE_CURRENCY))
    valuation = usecases.show_portfolio(user.user_id, base)
    print(MSG_PORTFOLIO_HEADER.format(username=user.username, base=base))
    wallets = valuation.portfolio.wallets
    if not wallets:
        print(MSG_NO_WALLETS)
        return
    value_column = COLUMN_VALUE.format(base=base)
    table = PrettyTable([COLUMN_CURRENCY, COLUMN_BALANCE, value_column])
    table.align[COLUMN_CURRENCY] = ALIGN_LEFT
    table.align[COLUMN_BALANCE] = ALIGN_RIGHT
    table.align[value_column] = ALIGN_RIGHT
    for code, wallet in wallets.items():
        balance = wallet.currency.format_amount(wallet.balance)
        value = valuation.base.format_value(valuation.values[code])
        table.add_row([code, balance, value])
    print(table)
    total = valuation.base.format_value(valuation.total)
    print(MSG_TOTAL.format(total=total, base=base))


def cmd_buy(session: Session, options: Options) -> None:
    """Купить валюту за базовую и показать изменения в портфеле."""
    user = session.require()
    code = utils.normalize_currency_code(options["currency"])
    amount = utils.parse_amount(options["amount"])
    result = usecases.buy(user.user_id, code, amount)
    print(
        MSG_BOUGHT.format(
            amount=result.currency.format_amount(result.amount),
            code=result.currency.code,
            rate=utils.format_rate(result.rate.value),
            base=result.base.code,
        )
    )
    print_changes(result)
    value = result.base.format_value(result.value)
    print(MSG_BUY_VALUE.format(value=value, base=result.base.code))


def print_changes(result: TradeResult) -> None:
    """Напечатать, как сделка изменила оба кошелька."""
    print(MSG_CHANGES)
    print(
        MSG_CHANGE_LINE.format(
            code=result.currency.code,
            before=result.currency.format_amount(result.before),
            after=result.currency.format_amount(result.after),
        )
    )
    print(
        MSG_CHANGE_LINE.format(
            code=result.base.code,
            before=result.base.format_amount(result.base_before),
            after=result.base.format_amount(result.base_after),
        )
    )


def cmd_get_rate(session: Session, options: Options) -> None:
    """Показать курс пары, время обновления и обратный курс."""
    from_code = utils.normalize_currency_code(options["from"])
    to_code = utils.normalize_currency_code(options["to"])
    rate = usecases.get_rate(from_code, to_code)
    print(
        MSG_RATE.format(
            from_code=rate.from_code,
            to_code=rate.to_code,
            rate=utils.format_rate(rate.value),
            updated_at=utils.format_display_time(rate.updated_at),
        )
    )
    inverse = rate.inverse()
    print(
        MSG_INVERSE_RATE.format(
            from_code=inverse.from_code,
            to_code=inverse.to_code,
            rate=utils.format_rate(inverse.value),
        )
    )


def cmd_help(session: Session, options: Options) -> None:
    """Показать справку по всем командам или по одной."""
    name = options.get("command")
    if name is None:
        for command in COMMANDS.values():
            print(command.usage)
        return
    command = COMMANDS.get(name)
    if command is None:
        print(MSG_UNKNOWN_COMMAND.format(name=name))
        return
    print(command.usage)


def cmd_exit(session: Session, options: Options) -> None:
    """Завершить работу."""
    session.running = False


COMMANDS: dict[str, Command] = {
    "register": Command(
        cmd_register,
        "register --username <str> --password <str>",
        required=("username", "password"),
    ),
    "login": Command(
        cmd_login,
        "login --username <str> --password <str>",
        required=("username", "password"),
    ),
    "logout": Command(cmd_logout, "logout", requires_login=True),
    "show-portfolio": Command(
        cmd_show_portfolio,
        "show-portfolio [--base <str>]",
        optional=("base",),
        requires_login=True,
    ),
    "buy": Command(
        cmd_buy,
        "buy --currency <str> --amount <float>",
        required=("currency", "amount"),
        requires_login=True,
    ),
    "get-rate": Command(
        cmd_get_rate, "get-rate --from <str> --to <str>", required=("from", "to")
    ),
    "help": Command(cmd_help, "help [команда]", positional="command"),
    "exit": Command(cmd_exit, "exit"),
}


def execute(line: str, session: Session) -> None:
    """Выполнить одну строку ввода, ошибки напечатать пользователю."""
    try:
        tokens = shlex.split(line)
    except ValueError as error:
        print(MSG_PARSE_FAILED.format(reason=error))
        return
    if not tokens:
        return
    name, *arguments = tokens
    command = COMMANDS.get(name)
    if command is None:
        print(MSG_UNKNOWN_COMMAND.format(name=name))
        return
    try:
        options = parse_options(arguments, command)
    except ValueError as error:
        print(error)
        print(MSG_USAGE.format(usage=command.usage))
        return
    try:
        if command.requires_login:
            session.require()
        command.handler(session, options)
    except (ValutaTradeError, ValueError) as error:
        print(error)
        if isinstance(error, CurrencyNotFoundError):
            print(
                MSG_SUPPORTED_CODES.format(
                    codes=CODES_SEPARATOR.join(supported_codes())
                )
            )


def run() -> None:
    """Запустить цикл чтения и выполнения команд до exit или конца ввода."""
    print(MSG_WELCOME)
    session = Session()
    while session.running:
        try:
            line = input(PROMPT)
        except (EOFError, KeyboardInterrupt):
            print()
            break
        execute(line, session)
