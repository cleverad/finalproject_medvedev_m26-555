"""Хранилище данных: синглтон DatabaseManager над JSON-файлами.

Единственная точка чтения и записи файлов в data/. Запись атомарная: сначала во
временный файл в той же папке, затем os.replace. Прочитанное кешируется до записи.
"""

import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from valutatrade_hub.core.exceptions import StorageError
from valutatrade_hub.core.models import User
from valutatrade_hub.infra.settings import SettingsLoader

__all__ = ["DatabaseManager", "make_cached_reader"]

JSON_INDENT = 2
TEMP_SUFFIX = ".tmp"

MSG_NOT_JSON = "не JSON ({detail})"


def make_cached_reader(
    load: Callable[[Path], Any],
) -> tuple[Callable[[Path], Any], Callable[[Path | None], None]]:
    """Обернуть загрузчик файлов кешем на замыкании.

    Возвращает пару функций: read(path) читает через кеш, forget(path) сбрасывает
    кеш одного файла, forget() сбрасывает весь кеш.
    """
    cache: dict[Path, Any] = {}

    def read(path: Path) -> Any:
        if path not in cache:
            cache[path] = load(path)
        return cache[path]

    def forget(path: Path | None = None) -> None:
        if path is None:
            cache.clear()
        else:
            cache.pop(path, None)

    return read, forget


class DatabaseManager:
    """Единственный экземпляр доступа к JSON-хранилищу.

    Синглтон через __new__, как и SettingsLoader.
    """

    _instance: "DatabaseManager | None" = None

    def __new__(cls) -> "DatabaseManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._settings = SettingsLoader()
            cls._instance._read, cls._instance._forget = make_cached_reader(_load_json)
        return cls._instance

    def reset_cache(self) -> None:
        """Забыть всё прочитанное, следующее чтение пойдёт с диска."""
        self._forget()

    def read_json(self, path: Path, default: Any) -> Any:
        """Прочитать JSON-файл, для отсутствующего файла вернуть default.

        Raises:
            StorageError: если файл не читается или содержит не JSON.
        """
        data = self._read(path)
        return default if data is None else data

    def write_json(self, path: Path, data: Any) -> None:
        """Атомарно записать данные в JSON-файл, создав папки по пути.

        Raises:
            StorageError: если файл не удалось записать.
        """
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle, temp_name = tempfile.mkstemp(dir=path.parent, suffix=TEMP_SUFFIX)
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=JSON_INDENT)
            os.replace(temp_name, path)
        except OSError as error:
            raise StorageError(path=path, reason=error.strerror) from error
        self._forget(path)

    def get_users(self) -> list[User]:
        """Вернуть всех пользователей из users.json."""
        records = self.read_json(self._settings.get("users_file"), default=[])
        return [User.from_dict(record) for record in records]

    def save_users(self, users: list[User]) -> None:
        """Записать весь список пользователей в users.json."""
        path = self._settings.get("users_file")
        self.write_json(path, [user.to_dict() for user in users])

    def find_user(self, username: str) -> User | None:
        """Найти пользователя по точному имени."""
        for user in self.get_users():
            if user.username == username:
                return user
        return None

    def next_user_id(self) -> int:
        """Вернуть следующий свободный идентификатор пользователя."""
        return max((user.user_id for user in self.get_users()), default=0) + 1


def _load_json(path: Path) -> Any:
    """Прочитать файл с диска, для отсутствующего вернуть None.

    Raises:
        StorageError: если файл не читается или содержит не JSON.
    """
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as stream:
            return json.load(stream)
    except json.JSONDecodeError as error:
        reason = MSG_NOT_JSON.format(detail=error.msg)
        raise StorageError(path=path, reason=reason) from error
    except OSError as error:
        raise StorageError(path=path, reason=error.strerror) from error
