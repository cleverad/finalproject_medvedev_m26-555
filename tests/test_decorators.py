import logging
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from valutatrade_hub.core.currencies import get_currency
from valutatrade_hub.core.decorators import log_action
from valutatrade_hub.core.exceptions import InsufficientFundsError
from valutatrade_hub.core.models import BUY_ACTION, SELL_ACTION, Rate, TradeResult
from valutatrade_hub.infra.logging_config import ACTIONS_LOGGER

UPDATED_AT = datetime(2025, 10, 9, 12, 5, 22, tzinfo=UTC)
RESULT = TradeResult(
    BUY_ACTION,
    "alice",
    get_currency("BTC"),
    Decimal("0.05"),
    Rate("BTC", "USD", Decimal(59300), UPDATED_AT, "CoinGecko"),
    Decimal(0),
    Decimal("0.05"),
    get_currency("USD"),
    Decimal(10000),
    Decimal(7035),
    Decimal(2965),
)
SAMPLE = (
    "BUY user='alice' currency='BTC' amount=0.0500 rate=59300.00 base='USD' result=OK"
)


def trade(user_id: int, currency_code: str, amount: object) -> TradeResult:
    """Сделка, которая всегда удаётся."""
    return RESULT


def failing_trade(user_id: int, currency_code: str, amount: object) -> TradeResult:
    raise InsufficientFundsError(available="0.0400", required="0.0500", code="BTC")


@pytest.fixture
def actions(caplog):
    caplog.set_level(logging.INFO, logger=ACTIONS_LOGGER)
    return caplog


class TestLogAction:
    def test_success_is_logged_as_in_the_sample(self, actions):
        result = log_action(BUY_ACTION)(trade)(1, "BTC", 0.05)
        assert result is RESULT
        (record,) = actions.records
        assert (record.name, record.levelname) == (ACTIONS_LOGGER, "INFO")
        assert record.getMessage() == SAMPLE

    def test_keyword_arguments_are_bound_too(self, actions):
        log_action(BUY_ACTION)(trade)(user_id=1, currency_code="BTC", amount=0.05)
        assert actions.records[0].getMessage() == SAMPLE

    def test_verbose_adds_balances_before_and_after(self, actions):
        log_action(BUY_ACTION, verbose=True)(trade)(1, "BTC", 0.05)
        assert actions.records[0].getMessage() == (
            "BUY user='alice' currency='BTC' amount=0.0500 rate=59300.00 base='USD' "
            "before=0.0000 after=0.0500 base_before=10000.00 base_after=7035.00 "
            "result=OK"
        )

    def test_error_is_logged_and_raised_again(self, actions):
        with pytest.raises(InsufficientFundsError) as info:
            log_action(SELL_ACTION)(failing_trade)(1, "BTC", 0.05)
        (record,) = actions.records
        assert record.levelname == "ERROR"
        assert record.getMessage() == (
            "SELL user=1 currency='BTC' amount=0.05 result=ERROR "
            "error_type=InsufficientFundsError "
            f"error_message='{info.value}'"
        )

    def test_wrong_call_is_not_logged(self, actions):
        with pytest.raises(TypeError):
            log_action(BUY_ACTION)(trade)(1)
        assert actions.records == []

    def test_wrapper_keeps_name_and_docstring(self):
        wrapped = log_action(BUY_ACTION)(trade)
        assert wrapped.__name__ == "trade"
        assert wrapped.__doc__ == trade.__doc__
        assert wrapped.__wrapped__ is trade
