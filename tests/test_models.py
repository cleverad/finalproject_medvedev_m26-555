import re
from datetime import UTC, datetime

import pytest

from valutatrade_hub.core import utils
from valutatrade_hub.core.models import User

REGISTERED_AT = datetime(2025, 10, 9, 12, 0, 0, tzinfo=UTC)


def make_user(password: str = "1234", **overrides) -> User:
    salt = overrides.pop("salt", "x5T9!")
    fields = {
        "user_id": 1,
        "username": "alice",
        "hashed_password": utils.hash_password(password, salt),
        "salt": salt,
        "registration_date": REGISTERED_AT,
    }
    fields.update(overrides)
    return User(**fields)


class TestUser:
    def test_create_hashes_the_password(self):
        user = User.create(7, "alice", " 1234 ", REGISTERED_AT)
        assert user.user_id == 7
        assert user.hashed_password != "1234"
        assert user.verify_password("1234")
        assert user.hashed_password == utils.hash_password("1234", user.salt)

    def test_create_uses_a_fresh_salt_each_time(self):
        first = User.create(1, "alice", "1234", REGISTERED_AT)
        second = User.create(2, "bob", "1234", REGISTERED_AT)
        assert first.salt != second.salt
        assert first.hashed_password != second.hashed_password

    def test_create_rejects_short_password(self):
        with pytest.raises(ValueError, match="не короче 4 символов"):
            User.create(1, "alice", "123", REGISTERED_AT)

    def test_getters(self):
        user = make_user()
        assert user.user_id == 1
        assert user.username == "alice"
        assert user.salt == "x5T9!"
        assert user.registration_date == REGISTERED_AT
        assert user.hashed_password == utils.hash_password("1234", "x5T9!")

    def test_attributes_are_private(self):
        user = make_user()
        assert set(vars(user)) == {
            "_user_id",
            "_username",
            "_hashed_password",
            "_salt",
            "_registration_date",
        }

    def test_verify_password(self):
        user = make_user("1234")
        assert user.verify_password("1234")
        assert not user.verify_password("4321")

    def test_change_password_rehashes(self):
        user = make_user("1234")
        old_hash = user.hashed_password
        user.change_password("abcd")
        assert user.hashed_password != old_hash
        assert user.verify_password("abcd")
        assert not user.verify_password("1234")
        assert user.salt == "x5T9!"

    def test_edge_spaces_do_not_lock_the_user_out(self):
        user = make_user("1234")
        assert user.verify_password(" 1234 ")
        user.change_password(" abcd ")
        assert user.verify_password("abcd")

    def test_change_password_rejects_short(self):
        user = make_user("1234")
        with pytest.raises(ValueError, match="не короче 4 символов"):
            user.change_password("123")
        assert user.verify_password("1234")

    def test_username_setter_validates(self):
        user = make_user()
        user.username = "  bob "
        assert user.username == "bob"
        with pytest.raises(ValueError, match="не может быть пустым"):
            user.username = "   "
        assert user.username == "bob"

    def test_constructor_rejects_naive_registration_date(self):
        with pytest.raises(ValueError, match=re.escape(utils.MSG_NAIVE_DATETIME)):
            make_user(registration_date=datetime(2025, 10, 9, 12, 0, 0))

    def test_constructor_validates_username(self):
        with pytest.raises(ValueError, match="не может быть пустым"):
            make_user(username="")

    def test_read_only_attributes(self):
        user = make_user()
        for name in ("user_id", "hashed_password", "salt", "registration_date"):
            with pytest.raises(AttributeError):
                setattr(user, name, "x")

    def test_user_info_has_no_secrets(self):
        info = make_user().get_user_info()
        assert info == {
            "user_id": 1,
            "username": "alice",
            "registration_date": "2025-10-09T12:00:00Z",
        }

    def test_to_dict_matches_users_json_layout(self):
        user = make_user()
        assert user.to_dict() == {
            "user_id": 1,
            "username": "alice",
            "hashed_password": user.hashed_password,
            "salt": "x5T9!",
            "registration_date": "2025-10-09T12:00:00Z",
        }

    def test_round_trip(self):
        user = make_user()
        copy = User.from_dict(user.to_dict())
        assert copy.to_dict() == user.to_dict()
        assert copy.verify_password("1234")

    def test_from_dict_accepts_naive_date_from_task_example(self):
        data = make_user().to_dict() | {"registration_date": "2025-10-09T12:00:00"}
        assert User.from_dict(data).registration_date == REGISTERED_AT

    def test_from_dict_rejects_missing_fields(self):
        with pytest.raises(KeyError):
            User.from_dict({"user_id": 1, "username": "alice"})
