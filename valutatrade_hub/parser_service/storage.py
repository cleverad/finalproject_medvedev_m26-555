"""Хранилище парсера: снимок rates.json и история exchange_rates.json.

Снимок хранит по одному последнему курсу на пару, updated_at в нём это время,
когда курс получен нами. История хранит каждый замер со временем источника,
поэтому повторный запрос в пределах одного обновления источника даёт тот же
идентификатор записи и не дублируется. Оба файла пишутся атомарно.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from valutatrade_hub.core import utils
from valutatrade_hub.core.models import Rate, RatesSnapshot
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.parser_service.api_clients import Observation
from valutatrade_hub.parser_service.config import ParserConfig

__all__ = ["RatesStorage"]

HISTORY_ID_SEPARATOR = "_"

MSG_NO_PATHS = "В настройках парсера не заданы пути к файлам курсов"


class RatesStorage:
    """Чтение и запись файлов курсов через DatabaseManager по путям из конфига."""

    def __init__(self, db: DatabaseManager, config: ParserConfig) -> None:
        """Запомнить хранилище и пути.

        Raises:
            ValueError: если в конфиге нет путей к файлам.
        """
        if config.rates_file is None or config.history_file is None:
            raise ValueError(MSG_NO_PATHS)
        self._db = db
        self.rates_file = config.rates_file
        self.history_file = config.history_file

    def load_snapshot(self) -> RatesSnapshot:
        """Вернуть текущий снимок, пустой, если файла ещё нет."""
        data = self._db.read_json(self.rates_file, default=None)
        return RatesSnapshot({}) if data is None else RatesSnapshot.from_dict(data)

    def save_snapshot(
        self, observed: Mapping[str, Observation], now: datetime
    ) -> RatesSnapshot:
        """Влить замеры в снимок и записать его.

        Пары, которых в замерах нет, остаются со старыми значениями и временем.
        Более новое значение побеждает, поэтому запоздавшее обновление не
        затирает свежее, и last_refresh тоже не уходит назад.
        """
        current_snapshot = self.load_snapshot()
        pairs = dict(current_snapshot.pairs)
        for key, observation in observed.items():
            rate = observation.rate
            fetched = Rate(rate.from_code, rate.to_code, rate.value, now, rate.source)
            current = pairs.get(key)
            if current is None or fetched.updated_at > current.updated_at:
                pairs[key] = fetched
        last_refresh = max(now, current_snapshot.last_refresh or now)
        snapshot = RatesSnapshot(pairs, last_refresh)
        self._db.write_json(self.rates_file, snapshot.to_dict())
        return snapshot

    def append_history(self, observed: Mapping[str, Observation]) -> int:
        """Дописать замеры в историю, пропуская уже записанные, вернуть число новых."""
        records: list[dict[str, Any]] = self._db.read_json(
            self.history_file, default=[]
        )
        known = {record["id"] for record in records}
        added = [
            record
            for record in map(history_record, observed.values())
            if record["id"] not in known
        ]
        if added:
            self._db.write_json(self.history_file, [*records, *added])
        return len(added)


def history_record(observation: Observation) -> dict[str, Any]:
    """Собрать запись истории: пара, курс, время источника и данные ответа."""
    rate = observation.rate
    timestamp = utils.format_utc(rate.updated_at)
    return {
        "id": f"{rate.key}{HISTORY_ID_SEPARATOR}{timestamp}",
        "from_currency": rate.from_code,
        "to_currency": rate.to_code,
        "rate": float(rate.value),
        "timestamp": timestamp,
        "source": rate.source,
        "meta": {
            "raw_id": observation.raw_id,
            "request_ms": observation.response.request_ms,
            "status_code": observation.response.status_code,
            "etag": observation.response.etag,
        },
    }
