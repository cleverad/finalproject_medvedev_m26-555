import logging
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler

from valutatrade_hub.infra import logging_config
from valutatrade_hub.infra.logging_config import ACTIONS_LOGGER, setup_logging
from valutatrade_hub.infra.settings import SettingsLoader


def file_handlers(name: str) -> list[RotatingFileHandler]:
    logger = logging.getLogger(name)
    return [h for h in logger.handlers if isinstance(h, RotatingFileHandler)]


class TestSetupLogging:
    def test_actions_log_goes_to_a_rotating_file_in_log_dir(self, workdir):
        setup_logging()
        handlers = file_handlers(ACTIONS_LOGGER)
        assert len(handlers) == 1
        handler = handlers[0]
        settings = SettingsLoader()
        assert handler.baseFilename == str(
            settings.get("log_dir") / logging_config.ACTIONS_LOG_FILE
        )
        assert handler.maxBytes == settings.get("log_max_bytes")
        assert handler.backupCount == settings.get("log_backup_count")
        assert (workdir / "logs").is_dir()

    def test_level_comes_from_settings(self, workdir):
        setup_logging()
        assert logging.getLogger(ACTIONS_LOGGER).level == logging.INFO

    def test_repeated_setup_does_not_duplicate_handlers(self, workdir):
        setup_logging()
        setup_logging()
        assert len(file_handlers(ACTIONS_LOGGER)) == 1

    def test_record_format_matches_the_sample(self, workdir):
        setup_logging()
        record = logging.LogRecord(
            ACTIONS_LOGGER, logging.INFO, __file__, 1, "BUY result=OK", None, None
        )
        record.created = datetime(2025, 10, 9, 12, 5, 22, tzinfo=UTC).timestamp()
        text = file_handlers(ACTIONS_LOGGER)[0].format(record)
        assert text == "INFO 2025-10-09T12:05:22 BUY result=OK"

    def test_records_reach_the_file(self, workdir):
        setup_logging()
        logging.getLogger(ACTIONS_LOGGER).info("SELL result=OK")
        for handler in file_handlers(ACTIONS_LOGGER):
            handler.flush()
        text = (workdir / "logs" / logging_config.ACTIONS_LOG_FILE).read_text()
        assert text.endswith("SELL result=OK\n")
