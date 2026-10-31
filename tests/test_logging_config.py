import logging
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler

import pytest

from valutatrade_hub.infra import logging_config
from valutatrade_hub.infra.logging_config import (
    ACTIONS_LOGGER,
    PARSER_LOGGER,
    parser_console,
    setup_logging,
)
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

    def test_parser_log_is_a_debug_file_with_a_detailed_format(self, workdir):
        setup_logging()
        logger = logging.getLogger(PARSER_LOGGER)
        assert logger.level == logging.DEBUG
        (handler,) = file_handlers(PARSER_LOGGER)
        assert handler.baseFilename == str(logging_config.parser_log_path())
        assert logging_config.parser_log_path() == workdir / "logs" / "parser.log"
        record = logging.LogRecord(
            PARSER_LOGGER, logging.DEBUG, __file__, 1, "GET x -> 200", None, None
        )
        record.created = datetime(2025, 10, 10, 15, 30, 0, tzinfo=UTC).timestamp()
        assert handler.format(record) == (
            "2025-10-10T15:30:00 DEBUG valutatrade.parser GET x -> 200"
        )

    def test_http_library_debug_records_are_silenced(self, workdir):
        setup_logging()
        http_logger = logging.getLogger(logging_config.HTTP_LIBRARY_LOGGER)
        assert not http_logger.isEnabledFor(logging.DEBUG)
        assert http_logger.isEnabledFor(logging.INFO)


class TestParserConsole:
    def test_shows_info_and_error_lines_only_inside_the_context(self, workdir, capsys):
        setup_logging()
        logger = logging.getLogger(PARSER_LOGGER)
        logger.info("before")
        with parser_console():
            logger.debug("GET ... -> 200")
            logger.info("Starting rates update...")
            logger.error("Failed to fetch from CoinGecko: Network error.")
        logger.info("after")
        assert capsys.readouterr().out.splitlines() == [
            "INFO: Starting rates update...",
            "ERROR: Failed to fetch from CoinGecko: Network error.",
        ]
        assert not any(
            isinstance(h, logging.StreamHandler)
            and not isinstance(h, RotatingFileHandler)
            for h in logger.handlers
        )

    def test_handler_is_removed_even_after_an_error(self, workdir):
        setup_logging()
        logger = logging.getLogger(PARSER_LOGGER)
        before = list(logger.handlers)
        with pytest.raises(RuntimeError), parser_console():
            raise RuntimeError
        assert logger.handlers == before
