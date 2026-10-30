import json
import os
from pathlib import Path

import pytest

from valutatrade_hub.core.exceptions import StorageError
from valutatrade_hub.core.models import User
from valutatrade_hub.core.utils import hash_password, utc_now
from valutatrade_hub.infra.database import DatabaseManager, make_cached_reader


def make_user(user_id: int, username: str) -> User:
    return User(user_id, username, hash_password("1234", "s"), "s", utc_now())


class TestCachedReader:
    def test_reads_once_until_forgotten(self):
        calls = []

        def load(path):
            calls.append(path)
            return {"n": len(calls)}

        read, forget = make_cached_reader(load)
        assert read("a") == {"n": 1}
        assert read("a") == {"n": 1}
        assert read("b") == {"n": 2}
        forget("a")
        assert read("a") == {"n": 3}
        assert read("b") == {"n": 2}
        forget()
        assert read("b") == {"n": 4}


class TestJsonFiles:
    def test_singleton(self):
        assert DatabaseManager() is DatabaseManager()

    def test_missing_file_gives_default(self, workdir):
        db = DatabaseManager()
        assert db.read_json(workdir / "data" / "none.json", default=[]) == []

    def test_write_creates_directories_and_reads_back(self, workdir):
        db = DatabaseManager()
        path = workdir / "data" / "deep" / "x.json"
        db.write_json(path, {"a": [1, 2.5, "привет"]})
        assert json.loads(path.read_text(encoding="utf-8")) == {"a": [1, 2.5, "привет"]}
        assert db.read_json(path, default=None) == {"a": [1, 2.5, "привет"]}

    def test_write_is_atomic_leaves_no_temp_files(self, workdir):
        db = DatabaseManager()
        path = workdir / "data" / "x.json"
        db.write_json(path, [1])
        db.write_json(path, [2])
        assert os.listdir(path.parent) == ["x.json"]

    def test_write_invalidates_cache(self, workdir):
        db = DatabaseManager()
        path = workdir / "data" / "x.json"
        db.write_json(path, [1])
        assert db.read_json(path, default=[]) == [1]
        db.write_json(path, [2])
        assert db.read_json(path, default=[]) == [2]

    def test_external_change_is_not_seen_until_reset(self, workdir):
        db = DatabaseManager()
        path = workdir / "data" / "x.json"
        db.write_json(path, [1])
        db.read_json(path, default=[])
        path.write_text("[9]")
        assert db.read_json(path, default=[]) == [1]
        db.reset_cache()
        assert db.read_json(path, default=[]) == [9]

    def test_broken_json_is_a_storage_error(self, workdir):
        path = workdir / "data" / "x.json"
        path.parent.mkdir()
        path.write_text("{not json")
        with pytest.raises(StorageError, match="x.json"):
            DatabaseManager().read_json(path, default=None)


class TestUsers:
    def test_empty_storage(self, workdir):
        db = DatabaseManager()
        assert db.get_users() == []
        assert db.find_user("alice") is None
        assert db.next_user_id() == 1

    def test_save_and_find(self, workdir: Path):
        db = DatabaseManager()
        db.save_users([make_user(1, "alice"), make_user(5, "bob")])
        stored = json.loads((workdir / "data" / "users.json").read_text())
        assert [u["username"] for u in stored] == ["alice", "bob"]
        assert db.find_user("bob").user_id == 5
        assert db.find_user("Bob") is None
        assert db.next_user_id() == 6
