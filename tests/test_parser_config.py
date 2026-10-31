import pytest

from valutatrade_hub.infra.settings import SettingsLoader
from valutatrade_hub.parser_service import config
from valutatrade_hub.parser_service.config import ParserConfig


class TestParserConfig:
    def test_currency_lists_come_from_the_registry_without_the_base(self):
        parser = ParserConfig()
        assert parser.base_currency == "USD"
        assert parser.fiat_currencies == ("EUR", "GBP", "RUB")
        assert parser.crypto_currencies == ("BTC", "ETH", "SOL")

    def test_every_crypto_has_a_coingecko_id(self):
        parser = ParserConfig()
        assert parser.crypto_id_map == {
            "BTC": "bitcoin",
            "ETH": "ethereum",
            "SOL": "solana",
        }

    def test_crypto_without_an_id_is_rejected(self):
        with pytest.raises(ValueError, match="SOL"):
            ParserConfig(crypto_id_map={"BTC": "bitcoin", "ETH": "ethereum"})

    def test_defaults_for_endpoints_and_timeout(self):
        parser = ParserConfig()
        assert parser.coingecko_url == "https://api.coingecko.com/api/v3/simple/price"
        assert parser.exchangerate_api_url == "https://v6.exchangerate-api.com/v6"
        assert parser.request_timeout == 10
        assert parser.exchangerate_api_key is None

    def test_load_reads_the_key_from_the_environment(self, workdir, monkeypatch):
        monkeypatch.setenv(config.API_KEY_ENV, "secret")
        parser = ParserConfig.load()
        assert parser.exchangerate_api_key == "secret"

    def test_load_reads_the_key_from_dotenv(self, workdir, monkeypatch):
        monkeypatch.delenv(config.API_KEY_ENV, raising=False)
        (workdir / ".env").write_text(f"{config.API_KEY_ENV}=from-file\n")
        SettingsLoader().reload()
        try:
            assert ParserConfig.load().exchangerate_api_key == "from-file"
        finally:
            monkeypatch.delenv(config.API_KEY_ENV, raising=False)

    def test_load_without_a_key_leaves_it_empty(self, workdir, monkeypatch):
        monkeypatch.delenv(config.API_KEY_ENV, raising=False)
        assert ParserConfig.load().exchangerate_api_key is None

    def test_load_takes_file_paths_from_settings(self, workdir):
        parser = ParserConfig.load()
        settings = SettingsLoader()
        assert parser.rates_file == settings.get("rates_file")
        assert parser.history_file == settings.get("history_file")

    def test_config_is_frozen(self):
        parser = ParserConfig()
        with pytest.raises(AttributeError):
            parser.request_timeout = 1  # type: ignore[misc]
