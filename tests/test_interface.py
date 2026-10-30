import re

import pytest

from valutatrade_hub.cli import interface
from valutatrade_hub.cli.interface import COMMANDS, Command, Session, parse_options


def escaped(template: str, **fields) -> str:
    return re.escape(template.format(**fields))


def run_lines(lines, capsys) -> str:
    """Выполнить строки как ввод пользователя и вернуть вывод."""
    session = Session()
    for line in lines:
        interface.execute(line, session)
    return capsys.readouterr().out


class TestParseOptions:
    command = Command(lambda s, o: None, "x --a <v> [--b <v>]", ("a",), ("b",))

    def test_required_and_optional(self):
        assert parse_options(["--a", "1", "--b", "2"], self.command) == {
            "a": "1",
            "b": "2",
        }
        assert parse_options(["--a", "1"], self.command) == {"a": "1"}

    @pytest.mark.parametrize(
        ("tokens", "message"),
        [
            ([], interface.MSG_MISSING_OPTION.format(name="a")),
            (["--b", "2"], interface.MSG_MISSING_OPTION.format(name="a")),
            (["--a"], interface.MSG_OPTION_WITHOUT_VALUE.format(name="a")),
            (["--a", "1", "--c", "3"], interface.MSG_UNKNOWN_OPTION.format(name="c")),
            (["--a", "1", "--a", "2"], interface.MSG_REPEATED_OPTION.format(name="a")),
            (["--a", "1", "stray"], interface.MSG_EXTRA_VALUE.format(value="stray")),
            (["--a", "--b"], interface.MSG_OPTION_WITHOUT_VALUE.format(name="a")),
        ],
    )
    def test_errors(self, tokens, message):
        with pytest.raises(ValueError, match=re.escape(message)):
            parse_options(tokens, self.command)

    def test_positional(self):
        command = Command(lambda s, o: None, "help [команда]", positional="command")
        assert parse_options([], command) == {}
        assert parse_options(["buy"], command) == {"command": "buy"}
        with pytest.raises(
            ValueError, match=escaped(interface.MSG_EXTRA_VALUE, value="x")
        ):
            parse_options(["buy", "x"], command)


class TestSession:
    def test_require_without_login(self):
        with pytest.raises(ValueError, match="Сначала выполните login"):
            Session().require()


class TestCommands:
    def test_register_then_login(self, workdir, capsys):
        out = run_lines(
            [
                "register --username alice --password 1234",
                "login --username alice --password 1234",
            ],
            capsys,
        )
        assert out.splitlines() == [
            "Пользователь 'alice' зарегистрирован (id=1). "
            "Войдите: login --username alice --password ****",
            "Вы вошли как 'alice'",
        ]

    def test_quoted_username_with_space(self, workdir, capsys):
        out = run_lines(['register --username "John Doe" --password 1234'], capsys)
        assert "Пользователь 'John Doe' зарегистрирован" in out

    @pytest.mark.parametrize(
        ("line", "message"),
        [
            ("register --username alice --password 123", "не короче 4 символов"),
            ("login --username alice --password 1234", "'alice' не найден"),
            ("logout", "Сначала выполните login"),
            ("frobnicate", interface.MSG_UNKNOWN_COMMAND.format(name="frobnicate")),
            (
                "register --username alice",
                interface.MSG_MISSING_OPTION.format(name="password"),
            ),
            (
                'register --username "alice',
                interface.MSG_PARSE_FAILED.format(reason="No closing quotation"),
            ),
        ],
    )
    def test_errors_are_printed_not_raised(self, workdir, capsys, line, message):
        out = run_lines([line], capsys)
        assert message in out

    def test_argument_error_shows_usage(self, workdir, capsys):
        out = run_lines(["register --username alice"], capsys)
        assert interface.MSG_USAGE.format(usage=COMMANDS["register"].usage) in out

    def test_duplicate_and_wrong_password(self, workdir, capsys):
        out = run_lines(
            [
                "register --username alice --password 1234",
                "register --username alice --password 1234",
                "login --username alice --password 0000",
            ],
            capsys,
        )
        assert "Имя пользователя 'alice' уже занято" in out
        assert "Неверный пароль" in out

    def test_second_login_replaces_session(self, workdir, capsys):
        session = Session()
        for line in [
            "register --username alice --password 1234",
            "register --username bob --password 1234",
            "login --username alice --password 1234",
            "login --username bob --password 1234",
        ]:
            interface.execute(line, session)
        assert session.user.username == "bob"
        interface.execute("logout", session)
        assert session.user is None
        assert interface.MSG_LOGGED_OUT in capsys.readouterr().out

    def test_empty_line_is_ignored(self, workdir, capsys):
        assert run_lines(["", "   "], capsys) == ""

    def test_help_lists_every_command(self, workdir, capsys):
        out = run_lines(["help"], capsys)
        for name, command in COMMANDS.items():
            assert command.usage in out
        assert run_lines(["help nothing"], capsys).startswith(
            interface.MSG_UNKNOWN_COMMAND.format(name="nothing")
        )
        assert run_lines(["help login"], capsys).startswith(COMMANDS["login"].usage)

    def test_exit_stops_session(self, workdir, capsys):
        session = Session()
        interface.execute("exit", session)
        assert session.running is False


class TestRun:
    def test_loop_until_eof(self, workdir, capsys, monkeypatch):
        lines = iter(["register --username alice --password 1234", "exit"])
        monkeypatch.setattr("builtins.input", lambda prompt: next(lines))
        interface.run()
        out = capsys.readouterr().out
        assert "зарегистрирован" in out

    def test_eof_ends_loop(self, workdir, capsys, monkeypatch):
        def raise_eof(prompt):
            raise EOFError

        monkeypatch.setattr("builtins.input", raise_eof)
        interface.run()
