import re
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from valutatrade_hub.core import utils

NAIVE = re.escape(utils.MSG_NAIVE_DATETIME)


class TestPasswords:
    def test_salt_is_random_hex(self):
        first, second = utils.make_salt(), utils.make_salt()
        assert first != second
        int(first, 16)

    def test_hash_is_deterministic_for_same_salt(self):
        salt = utils.make_salt()
        assert utils.hash_password("1234", salt) == utils.hash_password("1234", salt)

    def test_hash_depends_on_salt(self):
        assert utils.hash_password("1234", "a") != utils.hash_password("1234", "b")

    def test_hash_is_hex_and_not_the_password(self):
        digest = utils.hash_password("1234", "salt")
        int(digest, 16)
        assert "1234" not in digest

    def test_verify_password(self):
        salt = utils.make_salt()
        digest = utils.hash_password("1234", salt)
        assert utils.verify_password("1234", salt, digest)
        assert not utils.verify_password("1235", salt, digest)


class TestValidation:
    @pytest.mark.parametrize("name", ["alice", "  bob  ", "John Doe"])
    def test_username_is_stripped(self, name):
        assert utils.normalize_username(name) == name.strip()

    @pytest.mark.parametrize("name", ["", "   ", "\t"])
    def test_empty_username_is_rejected(self, name):
        with pytest.raises(ValueError, match="не может быть пустым"):
            utils.normalize_username(name)

    def test_username_must_be_a_string(self):
        with pytest.raises(TypeError, match="NoneType"):
            utils.normalize_username(None)

    @pytest.mark.parametrize("password", ["1234", "longer password", " 1234 "])
    def test_password_is_stripped(self, password):
        assert utils.normalize_password(password) == password.strip()

    @pytest.mark.parametrize("password", ["", "123", "   ", " ab ", "    "])
    def test_short_password_is_rejected(self, password):
        with pytest.raises(ValueError, match="не короче 4 символов"):
            utils.normalize_password(password)

    def test_password_must_be_a_string(self):
        with pytest.raises(TypeError, match="int"):
            utils.normalize_password(1234)

    def test_inner_spaces_are_kept(self):
        assert utils.normalize_password(" correct horse ") == "correct horse"

    @pytest.mark.parametrize("code", ["btc", " BTC ", "Btc"])
    def test_currency_code_is_stripped_and_uppercased(self, code):
        assert utils.normalize_currency_code(code) == "BTC"

    @pytest.mark.parametrize("code", ["", "   "])
    def test_empty_currency_code_is_rejected(self, code):
        with pytest.raises(ValueError, match="не может быть пустым"):
            utils.normalize_currency_code(code)

    def test_currency_code_must_be_a_string(self):
        with pytest.raises(TypeError, match="NoneType"):
            utils.normalize_currency_code(None)


class TestTime:
    def test_utc_now_is_aware_utc(self):
        now = utils.utc_now()
        assert now.tzinfo is UTC
        assert abs(now - datetime.now(UTC)) < timedelta(seconds=5)

    def test_format_utc(self):
        moment = datetime(2026, 10, 30, 9, 1, 37, 123456, tzinfo=UTC)
        assert utils.format_utc(moment) == "2026-10-30T09:01:37Z"

    def test_format_converts_other_zones_to_utc(self):
        moscow = timezone(timedelta(hours=3))
        moment = datetime(2026, 10, 30, 12, 1, 37, tzinfo=moscow)
        assert utils.format_utc(moment) == "2026-10-30T09:01:37Z"

    def test_naive_datetime_is_rejected_inside_the_program(self):
        naive = datetime(2026, 10, 30, 9, 1, 37)
        with pytest.raises(ValueError, match=NAIVE):
            utils.format_utc(naive)
        with pytest.raises(ValueError, match=NAIVE):
            utils.ensure_utc(naive)

    def test_ensure_utc_converts_other_zones(self):
        moscow = timezone(timedelta(hours=3))
        moment = datetime(2026, 10, 30, 12, 0, tzinfo=moscow)
        assert utils.ensure_utc(moment) == datetime(2026, 10, 30, 9, 0, tzinfo=UTC)

    @pytest.mark.parametrize(
        "text",
        ["2026-10-30T09:01:37Z", "2026-10-30T09:01:37+00:00", "2026-10-30T09:01:37"],
    )
    def test_parse_utc_accepts_zulu_offset_and_naive(self, text):
        assert utils.parse_utc(text) == datetime(2026, 10, 30, 9, 1, 37, tzinfo=UTC)

    def test_parse_rejects_garbage(self):
        with pytest.raises(ValueError):
            utils.parse_utc("yesterday")

    def test_round_trip(self):
        moment = datetime(2026, 10, 30, 9, 1, 37, tzinfo=UTC)
        assert utils.parse_utc(utils.format_utc(moment)) == moment


class TestFormatting:
    @pytest.mark.parametrize(
        ("market_cap", "text"),
        [
            (1.12e12, "1.12e12"),
            (4.5e11, "4.50e11"),
            (123456.0, "1.23e5"),
            (0.0, "0.00e0"),
            (0.00123, "1.23e-3"),
        ],
    )
    def test_format_mcap(self, market_cap, text):
        assert utils.format_mcap(market_cap) == text

    @pytest.mark.parametrize(
        ("value", "text"),
        [
            ("59337.21", "59337.21"),
            ("3720", "3720.00"),
            ("0.01016", "0.01016"),
            ("0.0000168528", "0.00001685"),
            ("1", "1.00"),
            ("0.000000001", "0.00"),
            ("1.0786", "1.0786"),
        ],
    )
    def test_format_rate(self, value, text):
        assert utils.format_rate(Decimal(value)) == text


class TestDecimal:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (1, Decimal(1)),
            (0.1, Decimal("0.1")),
            (1200.0, Decimal(1200)),
            (Decimal("0.05"), Decimal("0.05")),
            (1e-8, Decimal("0.00000001")),
        ],
    )
    def test_numbers_become_exact_decimals(self, value, expected):
        result = utils.to_decimal(value)
        assert isinstance(result, Decimal)
        assert result == expected

    @pytest.mark.parametrize("value", [True, False, "1", None, [1]])
    def test_non_numbers_are_a_type_error(self, value):
        with pytest.raises(TypeError, match=type(value).__name__):
            utils.to_decimal(value)

    @pytest.mark.parametrize(
        "value", [float("nan"), float("inf"), -float("inf"), Decimal("NaN")]
    )
    def test_non_finite_is_rejected(self, value):
        with pytest.raises(ValueError, match="конечным"):
            utils.to_decimal(value)
