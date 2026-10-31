import logging
import re
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from valutatrade_hub.cli import interface
from valutatrade_hub.cli.interface import COMMANDS, Command, Session, parse_options
from valutatrade_hub.core import usecases, utils
from valutatrade_hub.core.currencies import supported_codes
from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.core.models import Rate, RatesSnapshot
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.logging_config import PARSER_LOGGER, setup_logging
from valutatrade_hub.parser_service.updater import UpdateResult


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
        eur_usd = Rate(
            "EUR", "USD", Decimal("1.07"), datetime(2025, 1, 1, tzinfo=UTC), "x"
        )
        DatabaseManager().save_rates(RatesSnapshot({eur_usd.key: eur_usd}))
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

    def test_buy_prints_result_and_changes(self, workdir, capsys, monkeypatch):
        updated_at = datetime(2025, 10, 9, 0, 3, 22, tzinfo=UTC)
        btc_usd = Rate("BTC", "USD", Decimal(59300), updated_at, "CoinGecko")
        DatabaseManager().save_rates(RatesSnapshot({btc_usd.key: btc_usd}, updated_at))
        monkeypatch.setattr(utils, "utc_now", lambda: updated_at)
        out = run_lines(
            [
                "register --username alice --password 1234",
                "login --username alice --password 1234",
                "buy --currency btc --amount 0.05",
            ],
            capsys,
        ).splitlines()
        assert out[-5:] == [
            "Покупка выполнена: 0.0500 BTC по курсу 59300.00 USD/BTC",
            "Изменения в портфеле:",
            "- BTC: было 0.0000 → стало 0.0500",
            "- USD: было 10000.00 → стало 7035.00",
            "Оценочная стоимость покупки: 2,965.00 USD",
        ]

    @pytest.mark.parametrize(
        ("line", "message"),
        [
            (
                "buy --currency BTC --amount abc",
                "'amount' должен быть положительным числом",
            ),
            (
                "buy --currency BTC --amount -1",
                "'amount' должен быть положительным числом",
            ),
            ("buy --currency USD --amount 1", "Нельзя торговать базовой валютой USD"),
            ("buy --currency ABC --amount 1", "Неизвестная валюта 'ABC'"),
            ("buy --currency BTC --amount 1e30", "Сумма слишком велика для BTC"),
        ],
    )
    def test_buy_errors(self, workdir, capsys, line, message):
        out = run_lines(
            [
                "register --username alice --password 1234",
                "login --username alice --password 1234",
                line,
            ],
            capsys,
        )
        assert message in out

    def test_buy_requires_login(self, workdir, capsys):
        out = run_lines(["buy --currency BTC --amount 1"], capsys)
        assert out.strip() == "Сначала выполните login"

    def test_sell_prints_result_and_changes(self, workdir, capsys, monkeypatch):
        updated_at = datetime(2025, 10, 9, 0, 3, 22, tzinfo=UTC)
        btc_usd = Rate("BTC", "USD", Decimal(59800), updated_at, "CoinGecko")
        DatabaseManager().save_rates(RatesSnapshot({btc_usd.key: btc_usd}, updated_at))
        monkeypatch.setattr(utils, "utc_now", lambda: updated_at)
        out = run_lines(
            [
                "register --username alice --password 1234",
                "login --username alice --password 1234",
                "buy --currency BTC --amount 0.05",
                "sell --currency btc --amount 0.01",
            ],
            capsys,
        ).splitlines()
        assert out[-5:] == [
            "Продажа выполнена: 0.0100 BTC по курсу 59800.00 USD/BTC",
            "Изменения в портфеле:",
            "- BTC: было 0.0500 → стало 0.0400",
            "- USD: было 7010.00 → стало 7608.00",
            "Оценочная выручка: 598.00 USD",
        ]

    MISSING_WALLET = (
        "У вас нет кошелька 'BTC'. Добавьте валюту: она создаётся "
        "автоматически при первой покупке."
    )

    @pytest.mark.parametrize(
        ("lines", "message"),
        [
            (["sell --currency BTC --amount 0.01"], MISSING_WALLET),
            (
                [
                    "buy --currency BTC --amount 0.04",
                    "sell --currency BTC --amount 0.05",
                ],
                "Недостаточно средств: доступно 0.0400 BTC, требуется 0.0500 BTC",
            ),
            (
                ["sell --currency BTC --amount abc"],
                "'amount' должен быть положительным числом",
            ),
            (
                ["sell --currency USD --amount 100"],
                "Нельзя торговать базовой валютой USD",
            ),
        ],
    )
    def test_sell_errors(self, workdir, capsys, lines, message):
        out = run_lines(
            [
                "register --username alice --password 1234",
                "login --username alice --password 1234",
                *lines,
            ],
            capsys,
        )
        assert message in out

    def test_sell_requires_login(self, workdir, capsys):
        out = run_lines(["sell --currency BTC --amount 1"], capsys)
        assert out.strip() == "Сначала выполните login"

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

    def test_get_rate_refreshes_an_empty_cache(self, workdir, capsys, offline_refresh):
        out = run_lines(["get-rate --from BTC --to USD"], capsys)
        assert offline_refresh == [None]
        assert out.startswith("Курс BTC→USD: 59337.21")

    def test_get_rate_reports_a_failed_refresh(self, workdir, capsys, monkeypatch):
        def refresh(sources=None):
            raise ApiRequestError(reason="CoinGecko: сбой сети")

        monkeypatch.setattr(usecases, "refresh_rates", refresh)
        out = run_lines(["get-rate --from USD --to BTC"], capsys).splitlines()
        assert out == [
            "Курс USD→BTC недоступен. Повторите попытку позже.",
            "Ошибка при обращении к внешнему API: CoinGecko: сбой сети",
            "Проверьте сеть и ключ API. Подробности в logs/parser.log",
        ]

    def test_buy_reports_a_failed_refresh(self, workdir, capsys, monkeypatch):
        def refresh(sources=None):
            raise ApiRequestError(reason="down")

        monkeypatch.setattr(usecases, "refresh_rates", refresh)
        out = run_lines(
            [
                "register --username alice --password 1234",
                "login --username alice --password 1234",
                "buy --currency BTC --amount 0.01",
            ],
            capsys,
        ).splitlines()
        assert out[-2:] == [
            "Ошибка при обращении к внешнему API: down",
            "Проверьте сеть и ключ API. Подробности в logs/parser.log",
        ]

    def test_update_rates_shows_parser_log_and_summary(
        self, workdir, capsys, monkeypatch
    ):
        setup_logging()
        now = datetime(2025, 10, 10, 15, 30, 0, tzinfo=UTC)

        def refresh(sources=None):
            logger = logging.getLogger(PARSER_LOGGER)
            logger.info("Starting rates update...")
            logger.debug("GET https://x -> 200")
            logger.info("Fetching from CoinGecko... OK (3 rates)")
            return UpdateResult(6, now, ())

        monkeypatch.setattr(usecases, "refresh_rates", refresh)
        out = run_lines(["update-rates"], capsys).splitlines()
        summary = (
            "Update successful. Total rates updated: 6. "
            "Last refresh: 2025-10-10 15:30:00 UTC"
        )
        assert out == [
            "INFO: Starting rates update...",
            "INFO: Fetching from CoinGecko... OK (3 rates)",
            summary,
        ]

    def test_update_rates_with_errors(self, workdir, capsys, monkeypatch):
        setup_logging()
        now = datetime(2025, 10, 10, 15, 30, 0, tzinfo=UTC)

        def refresh(sources=None):
            logging.getLogger(PARSER_LOGGER).error(
                "Failed to fetch from CoinGecko: Network error."
            )
            return UpdateResult(3, now, ("CoinGecko: Network error.",))

        monkeypatch.setattr(usecases, "refresh_rates", refresh)
        out = run_lines(["update-rates"], capsys).splitlines()
        assert out == [
            "ERROR: Failed to fetch from CoinGecko: Network error.",
            "Update completed with errors. Check logs/parser.log for details.",
        ]

    def test_update_rates_when_every_source_failed(self, workdir, capsys, monkeypatch):
        def refresh(sources=None):
            raise ApiRequestError(reason="ни один источник не ответил")

        monkeypatch.setattr(usecases, "refresh_rates", refresh)
        out = run_lines(["update-rates"], capsys).splitlines()
        assert out == [
            "Update completed with errors. Check logs/parser.log for details.",
            "Ошибка при обращении к внешнему API: ни один источник не ответил",
            "Проверьте сеть и ключ API. Подробности в logs/parser.log",
        ]

    def test_update_rates_source_is_passed_lowercased(
        self, workdir, capsys, offline_refresh
    ):
        run_lines(["update-rates --source CoinGecko"], capsys)
        assert offline_refresh == [("coingecko",)]

    def test_update_rates_unknown_source(self, workdir, capsys, monkeypatch):
        def refresh(sources=None):
            raise ValueError("Неизвестный источник 'binance'. Доступны: coingecko")

        monkeypatch.setattr(usecases, "refresh_rates", refresh)
        out = run_lines(["update-rates --source binance"], capsys)
        assert out.startswith("Неизвестный источник 'binance'")

    def test_show_rates_prints_header_and_table(self, workdir, capsys, monkeypatch):
        updated_at = datetime(2025, 10, 10, 15, 30, 0, tzinfo=UTC)
        monkeypatch.setattr(utils, "utc_now", lambda: updated_at)
        run_lines(["update-rates"], capsys)
        capsys.readouterr()
        out = run_lines(["show-rates --top 2"], capsys).splitlines()
        assert out[0] == "Rates from cache (updated at 2025-10-10 15:30:00 UTC):"
        rows = [line for line in out if line.startswith("| ")]
        assert rows[0].split("|")[1].strip() == "Пара"
        assert [row.split("|")[1].strip() for row in rows[1:]] == ["BTC_USD", "ETH_USD"]
        assert "59337.21" in rows[1] and "2025-10-10 15:30:00 UTC" in rows[1]
        assert rows[1].split("|")[4].strip() == "stub"

    def test_show_rates_filters_are_normalized(self, workdir, capsys, offline_refresh):
        run_lines(["update-rates"], capsys)
        capsys.readouterr()
        out = run_lines(["show-rates --currency rub --base eur"], capsys).splitlines()
        assert out[0].startswith("Rates from cache")
        assert any(line.startswith("| RUB_EUR") for line in out)

    EMPTY_CACHE = (
        "Локальный кеш курсов пуст. Выполните 'update-rates', чтобы загрузить данные."
    )

    @pytest.mark.parametrize(
        ("line", "message"),
        [
            ("show-rates", EMPTY_CACHE),
            ("show-rates --top 0", "'top' должен быть положительным целым числом"),
            ("show-rates --top x", "'top' должен быть положительным целым числом"),
        ],
    )
    def test_show_rates_errors_on_empty_cache(self, workdir, capsys, line, message):
        assert run_lines([line], capsys).strip() == message

    def test_show_rates_unknown_currency(self, workdir, capsys, offline_refresh):
        run_lines(["update-rates"], capsys)
        capsys.readouterr()
        out = run_lines(["show-rates --currency abc"], capsys)
        assert out.strip() == "Курс для 'ABC' не найден в кеше."

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

    def test_run_sets_up_logging(self, workdir, capsys, monkeypatch):
        lines = iter(
            [
                "register --username alice --password 1234",
                "login --username alice --password 1234",
                "buy --currency BTC --amount 0.01",
                "exit",
            ]
        )
        monkeypatch.setattr("builtins.input", lambda prompt: next(lines))
        interface.run()
        text = (workdir / "logs" / "actions.log").read_text()
        assert " BUY user='alice' currency='BTC' amount=0.0100 " in text
        assert text.endswith("result=OK\n")

    def test_eof_ends_loop(self, workdir, capsys, monkeypatch):
        def raise_eof(prompt):
            raise EOFError

        monkeypatch.setattr("builtins.input", raise_eof)
        interface.run()
