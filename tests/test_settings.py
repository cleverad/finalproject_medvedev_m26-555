import os
from pathlib import Path

import pytest

from valutatrade_hub.infra.settings import DEFAULTS, SettingsLoader

LOADED_FROM_DOTENV = ("VT_TEST_KEY", "VT_TEST_PRESENT", "VT_TEST_QUOTED")


def test_singleton():
    assert SettingsLoader() is SettingsLoader()


def test_defaults_without_pyproject(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = SettingsLoader()
    settings.reload()
    assert settings.get("rates_ttl_seconds") == DEFAULTS["rates_ttl_seconds"]
    assert settings.get("default_base_currency") == DEFAULTS["default_base_currency"]


def test_pyproject_overrides_defaults(tmp_path, monkeypatch):
    ttl = DEFAULTS["rates_ttl_seconds"] + 1
    (tmp_path / "pyproject.toml").write_text(
        f"[tool.valutatrade]\nrates_ttl_seconds = {ttl}\n"
    )
    monkeypatch.chdir(tmp_path)
    settings = SettingsLoader()
    settings.reload()
    assert settings.get("rates_ttl_seconds") == ttl
    assert settings.get("start_balance_usd") == DEFAULTS["start_balance_usd"]


def test_file_paths_are_absolute_and_inside_data_dir(workdir: Path):
    settings = SettingsLoader()
    users = settings.get("users_file")
    assert isinstance(users, Path)
    assert users == workdir / "data" / "users.json"
    assert settings.get("rates_file") == workdir / "data" / "rates.json"
    assert settings.get("log_dir") == workdir / DEFAULTS["log_dir"]


def test_unknown_key_without_default_is_an_error(workdir):
    with pytest.raises(KeyError):
        SettingsLoader().get("no_such_key")
    assert SettingsLoader().get("no_such_key", 7) == 7


def test_pyproject_without_our_section_is_fine(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    monkeypatch.chdir(tmp_path)
    SettingsLoader().reload()
    assert SettingsLoader().get("rates_ttl_seconds") == DEFAULTS["rates_ttl_seconds"]


def test_dotenv_is_loaded_without_overriding_environment(workdir, monkeypatch):
    monkeypatch.delenv("VT_TEST_KEY", raising=False)
    monkeypatch.setenv("VT_TEST_PRESENT", "from-shell")
    (workdir / ".env").write_text(
        "# comment\n\nVT_TEST_KEY=secret value\nVT_TEST_PRESENT=from-file\n"
        "export VT_TEST_QUOTED='quoted'\nBROKEN LINE\n"
    )
    SettingsLoader().reload()
    loaded = {name: os.environ.get(name) for name in LOADED_FROM_DOTENV}
    for name in LOADED_FROM_DOTENV:
        monkeypatch.delenv(name, raising=False)
    assert loaded == {
        "VT_TEST_KEY": "secret value",
        "VT_TEST_PRESENT": "from-shell",
        "VT_TEST_QUOTED": "quoted",
    }
