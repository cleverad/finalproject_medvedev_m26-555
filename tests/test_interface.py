import re
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from valutatrade_hub.cli import interface
from valutatrade_hub.cli.interface import COMMANDS, Command, Session, parse_options
from valutatrade_hub.core import utils
from valutatrade_hub.core.currencies import supported_codes
from valutatrade_hub.core.models import Rate, RatesSnapshot
from valutatrade_hub.infra.database import DatabaseManager


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
            (
                "Пользователь 'alice' зарегистрирован (id=1). "
                "Войдите: login --username alice --password ****"
            ),
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
        for command in COMMANDS.values():
            assert command.usage in out
        assert run_lines(["help nothing"], capsys).startswith(
            interface.MSG_UNKNOWN_COMMAND.format(name="nothing")
        )
        assert run_lines(["help login"], capsys).startswith(COMMANDS["login"].usage)

    def test_show_portfolio_prints_header_table_and_total(self, workdir, capsys):
        out = run_lines(
            [
                "register --username alice --password 1234",
                "login --username alice --password 1234",
                "show-portfolio",
            ],
            capsys,
        ).splitlines()
        assert "Портфель пользователя 'alice' (база: USD):" in out
        assert any("| USD" in line and "10,000.00" in line for line in out)
        assert out[-1] == "ИТОГО: 10,000.00 USD"

    def test_show_portfolio_base_is_uppercased(self, workdir, capsys):
        out = run_lines(
            [
                "register --username alice --password 1234",
                "login --username alice --password 1234",
                "show-portfolio --base eur",
            ],
            capsys,
        ).splitlines()
        assert "Портфель пользователя 'alice' (база: EUR):" in out
        assert out[-1].endswith(" EUR")

    def test_show_portfolio_errors(self, workdir, capsys):
        out = run_lines(["show-portfolio"], capsys)
        assert "Сначала выполните login" in out
        out = run_lines(
            [
                "register --username alice --password 1234",
                "login --username alice --password 1234",
                "show-portfolio --base ABC",
            ],
            capsys,
        )
        assert "Неизвестная базовая валюта 'ABC'" in out

    def test_get_rate_prints_rate_time_and_inverse(self, workdir, capsys, monkeypatch):
        updated_at = datetime(2025, 10, 9, 0, 3, 22, tzinfo=UTC)
        btc_usd = Rate("BTC", "USD", Decimal("59337.21"), updated_at, "CoinGecko")
        DatabaseManager().save_rates(RatesSnapshot({btc_usd.key: btc_usd}, updated_at))
        monkeypatch.setattr(utils, "utc_now", lambda: updated_at)
        out = run_lines(["get-rate --from usd --to btc"], capsys).splitlines()
        assert out == [
            "Курс USD→BTC: 0.00001685 (обновлено: 2025-10-09 00:03:22 UTC)",
            "Обратный курс BTC→USD: 59337.21",
        ]

    def test_get_rate_does_not_require_login(self, workdir, capsys):
        out = run_lines(["get-rate --from USD --to USD"], capsys)
        assert "Сначала выполните login" not in out
        assert "Курс USD→USD: 1.00" in out

    def test_get_rate_unknown_code_lists_supported(self, workdir, capsys):
        out = run_lines(["get-rate --from USD --to ABC"], capsys).splitlines()
        codes = interface.CODES_SEPARATOR.join(supported_codes())
        assert out == [
            "Неизвестная валюта 'ABC'",
            interface.MSG_SUPPORTED_CODES.format(codes=codes),
        ]

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
