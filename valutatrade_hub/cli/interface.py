"""Командный интерфейс ValutaTrade Hub.

Интерфейс только разбирает команды, вызывает usecases и печатает результат.
Таблица COMMANDS служит и справкой, и диспетчером.
"""

import shlex
from collections.abc import Callable
from dataclasses import dataclass

from valutatrade_hub.core import usecases
from valutatrade_hub.core.exceptions import ValutaTradeError
from valutatrade_hub.core.models import User

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
