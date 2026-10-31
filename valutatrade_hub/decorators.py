"""Декораторы ядра: @log_action ведёт журнал действий пользователя."""

import functools
import inspect
import logging
from collections.abc import Callable
from typing import ParamSpec

from valutatrade_hub.core import utils
from valutatrade_hub.core.models import TradeResult
from valutatrade_hub.logging_config import ACTIONS_LOGGER

__all__ = ["log_action"]

P = ParamSpec("P")

# Имена аргументов сделки и подписи их полей в записи журнала.
ARGUMENT_FIELDS = {"user_id": "user", "currency_code": "currency", "amount": "amount"}
FIELD_SEPARATOR = " "
FIELD_ASSIGN = "="
RESULT_OK = "OK"
RESULT_ERROR = "ERROR"


def log_action(
    action: str, verbose: bool = False
) -> Callable[[Callable[P, TradeResult]], Callable[P, TradeResult]]:
    """Собрать декоратор, который записывает сделку в журнал действий.

    Успех даёт запись INFO с полями итога сделки, исключение даёт запись ERROR
    с типом и текстом ошибки и уходит дальше без изменений. С verbose в запись
    попадают балансы обоих кошельков до и после сделки.
    """
    logger = logging.getLogger(ACTIONS_LOGGER)

    def decorator(func: Callable[P, TradeResult]) -> Callable[P, TradeResult]:
        signature = inspect.signature(func)

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> TradeResult:
            arguments = signature.bind(*args, **kwargs).arguments
            fields = _argument_fields(arguments)
            try:
                result = func(*args, **kwargs)
            except Exception as error:
                fields["result"] = RESULT_ERROR
                fields["error_type"] = type(error).__name__
                fields["error_message"] = utils.quote(error)
                logger.error(_record(action, fields))
                raise
            fields.update(result.get_log_fields(verbose))
            fields["result"] = RESULT_OK
            logger.info(_record(action, fields))
            return result

        return wrapper

    return decorator


def _argument_fields(arguments: dict[str, object]) -> dict[str, str]:
    """Собрать поля записи из аргументов вызова: строки в кавычках, числа как есть."""
    fields = {}
    for name, field in ARGUMENT_FIELDS.items():
        if name in arguments:
            value = arguments[name]
            fields[field] = utils.quote(value) if isinstance(value, str) else str(value)
    return fields


def _record(action: str, fields: dict[str, str]) -> str:
    """Собрать строку записи: BUY user='alice' currency='BTC' ... result=OK."""
    pairs = (f"{name}{FIELD_ASSIGN}{value}" for name, value in fields.items())
    return FIELD_SEPARATOR.join((action, *pairs))
