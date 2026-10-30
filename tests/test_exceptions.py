import pytest

from valutatrade_hub.core.exceptions import (
    ApiRequestError,
    CurrencyNotFoundError,
    InsufficientFundsError,
    InvalidPasswordError,
    RateNotAvailableError,
    StorageError,
    UsernameTakenError,
    UserNotFoundError,
    ValutaTradeError,
    WalletNotFoundError,
)


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (
            InsufficientFundsError(available="0.0400", required="0.0500", code="BTC"),
            "Недостаточно средств: доступно 0.0400 BTC, требуется 0.0500 BTC",
        ),
        (CurrencyNotFoundError(code="ABC"), "Неизвестная валюта 'ABC'"),
        (
            ApiRequestError(reason="timeout"),
            "Ошибка при обращении к внешнему API: timeout",
        ),
        (UserNotFoundError(username="alice"), "Пользователь 'alice' не найден"),
        (
            UsernameTakenError(username="alice"),
            "Имя пользователя 'alice' уже занято",
        ),
        (InvalidPasswordError(), "Неверный пароль"),
        (
            WalletNotFoundError(code="BTC"),
            "У вас нет кошелька 'BTC'. Добавьте валюту: она создаётся "
            "автоматически при первой покупке.",
        ),
        (
            RateNotAvailableError(from_code="BTC", to_code="USD"),
            "Не удалось получить курс для BTC→USD",
        ),
    ],
)
def test_messages_from_the_task(error, message):
    assert str(error) == message
    assert isinstance(error, ValutaTradeError)


def test_storage_error_uses_its_template():
    error = StorageError(path="data/users.json", reason="bad json")
    assert str(error) == StorageError.template.format(
        path="data/users.json", reason="bad json"
    )


def test_fields_are_kept():
    error = InsufficientFundsError(available="1", required="2", code="USD")
    assert (error.available, error.required, error.code) == ("1", "2", "USD")


def test_missing_field_is_an_error():
    with pytest.raises(KeyError):
        CurrencyNotFoundError()
