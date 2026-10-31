import logging
from datetime import UTC, datetime
from typing import ClassVar

import pytest

from valutatrade_hub.core.exceptions import ApiRequestError
from valutatrade_hub.infra.settings import SettingsLoader
from valutatrade_hub.logging_config import PARSER_LOGGER
from valutatrade_hub.parser_service import scheduler
from valutatrade_hub.parser_service.scheduler import RatesScheduler
from valutatrade_hub.parser_service.updater import UpdateResult

NOW = datetime(2025, 10, 10, 15, 30, 0, tzinfo=UTC)


class FakeRefresh:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@pytest.fixture
def parser_log(caplog):
    caplog.set_level(logging.INFO, logger=PARSER_LOGGER)
    return caplog


class TestRatesScheduler:
    def test_refreshes_then_sleeps_the_interval(self, parser_log):
        refresh = FakeRefresh([UpdateResult(6, NOW, ())] * 3)
        naps = []
        RatesScheduler(300, refresh, sleep=naps.append).run(runs=3)
        assert refresh.calls == 3
        assert naps == [300, 300]

    def test_keeps_going_after_a_failed_update(self, parser_log):
        refresh = FakeRefresh(
            [ApiRequestError(reason="all down"), UpdateResult(3, NOW, ("x",))]
        )
        RatesScheduler(60, refresh, sleep=lambda seconds: None).run(runs=2)
        assert refresh.calls == 2
        errors = [r.getMessage() for r in parser_log.records if r.levelname == "ERROR"]
        assert errors == [
            "Scheduled update failed: Ошибка при обращении к внешнему API: all down"
        ]

    def test_logs_each_run(self, parser_log):
        refresh = FakeRefresh([UpdateResult(6, NOW, ())])
        RatesScheduler(300, refresh, sleep=lambda seconds: None).run(runs=1)
        infos = [r.getMessage() for r in parser_log.records if r.levelname == "INFO"]
        assert infos == [
            "Scheduler started: every 300 s",
            "Scheduled update done: 6 rates, errors: 0",
            "Scheduler stopped",
        ]

    def test_keyboard_interrupt_during_sleep_stops_quietly(self, parser_log):
        def sleep(seconds):
            raise KeyboardInterrupt

        refresh = FakeRefresh([UpdateResult(6, NOW, ())] * 5)
        RatesScheduler(300, refresh, sleep=sleep).run()
        assert refresh.calls == 1
        assert parser_log.records[-1].getMessage() == "Scheduler stopped"

    @pytest.mark.parametrize("interval", [0, -5])
    def test_interval_must_be_positive(self, interval):
        with pytest.raises(ValueError, match="Интервал"):
            RatesScheduler(interval, lambda: None)

    @pytest.mark.parametrize("runs", [0, -1])
    def test_runs_must_be_positive_when_given(self, runs):
        with pytest.raises(ValueError, match="runs"):
            RatesScheduler(1, lambda: None).run(runs=runs)


class FakeScheduler:
    """Подмена RatesScheduler: запоминает, с чем создан и как запущен."""

    seen: ClassVar[dict[str, object]] = {}

    def __init__(self, interval_seconds, refresh, sleep=None):
        self.seen.clear()
        self.seen["interval"] = interval_seconds
        self.seen["refresh"] = refresh

    def run(self, runs=None):
        self.seen["runs"] = runs


class TestMain:
    @pytest.fixture
    def fake_scheduler(self, monkeypatch):
        monkeypatch.setattr(scheduler, "RatesScheduler", FakeScheduler)
        return FakeScheduler.seen

    def test_interval_defaults_to_settings(self, workdir, fake_scheduler):
        scheduler.main([])
        assert fake_scheduler == {
            "interval": SettingsLoader().get("scheduler_interval_seconds"),
            "refresh": scheduler.refresh_rates,
            "runs": None,
        }
        assert (workdir / "logs").is_dir()

    def test_arguments_override_interval_and_runs(self, workdir, fake_scheduler):
        scheduler.main(["--interval", "30", "--runs", "2"])
        assert fake_scheduler["interval"] == 30
        assert fake_scheduler["runs"] == 2
