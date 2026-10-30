"""Доменные исключения ValutaTrade Hub.

Текст сообщения задаётся шаблоном класса, поля шаблона сохраняются атрибутами.
"""

__all__ = [
    "ApiRequestError",
    "CurrencyNotFoundError",
    "InsufficientFundsError",
    "InvalidPasswordError",
    "RateNotAvailableError",
    "StorageError",
    "UserNotFoundError",
    "UsernameTakenError",
    "ValutaTradeError",
    "WalletNotFoundError",
]


class ValutaTradeError(Exception):
    """Базовое исключение приложения.

    Raises:
        KeyError: если при создании не передано поле, которое требует шаблон.
    """

    template = "Ошибка ValutaTrade Hub"

    def __init__(self, **fields: object) -> None:
        for name, value in fields.items():
            setattr(self, name, value)
        super().__init__(self.template.format(**fields))


class InsufficientFundsError(ValutaTradeError):
    """На кошельке меньше средств, чем требует операция."""

    template = (
        "Недостаточно средств: доступно {available} {code}, требуется {required} {code}"
    )


class CurrencyNotFoundError(ValutaTradeError):
    """Код валюты отсутствует в реестре."""

    template = "Неизвестная валюта '{code}'"


class ApiRequestError(ValutaTradeError):
    """Сбой при обращении к внешнему API курсов."""

    template = "Ошибка при обращении к внешнему API: {reason}"


class UserNotFoundError(ValutaTradeError):
    """Пользователь с таким именем не зарегистрирован."""

    template = "Пользователь '{username}' не найден"


class UsernameTakenError(ValutaTradeError):
    """Имя пользователя уже занято."""

    template = "Имя пользователя '{username}' уже занято"


class InvalidPasswordError(ValutaTradeError):
    """Пароль не совпал с сохранённым хешем."""

    template = "Неверный пароль"


class WalletNotFoundError(ValutaTradeError):
    """В портфеле нет кошелька для этой валюты."""

    template = (
        "У вас нет кошелька '{code}'. Добавьте валюту: она создаётся "
        "автоматически при первой покупке."
    )


class RateNotAvailableError(ValutaTradeError):
    """В кеше нет курса для пары валют."""

    template = "Не удалось получить курс для {from_code}→{to_code}"


class StorageError(ValutaTradeError):
    """Файл данных недоступен или повреждён."""

    template = "Не удалось прочитать {path}: {reason}"
