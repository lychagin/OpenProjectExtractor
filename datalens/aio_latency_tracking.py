# Patched copy of dl_app_tools/aio_latency_tracking.py from
# ghcr.io/datalens-tech/datalens-data-api:0.2457.0, bind-mounted over the
# original in docker-compose.prod.yml.
#
# Why: dl_data_api/app.py calls `await LatencyTracker().run_task()`
# unconditionally, and dl_logging.configure_logging() is called there WITHOUT a
# log_level, so dl_logging.config._make_logging_config falls back to
# `log_level or "DEBUG"` for the root logger. There is no env var and no
# settings hook to raise that level for this app, so the tracker emits a DEBUG
# "Latency stats: ..." line every 5s per gunicorn worker forever. At
# GUNICORN_WORKERS_COUNT=5 that is ~58 MB/day on a fully idle instance; it grew
# to 4.7 GB and took the VM disk to 95%.
#
# The ONLY change vs upstream is _log_stats(): it still drains self._bins (not
# draining would leak) but does not log them. Everything else, including the
# "High latency" WARNING, is byte-for-byte upstream behaviour.
#
# If DataLens is upgraded and the interpreter path changes (this mount targets
# /venv/lib/python3.10/site-packages/...), the mount silently stops applying and
# the spam returns — it cannot break the container. Re-check after any bump of
# the datalens-data-api image tag.

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Optional


LOGGER = logging.getLogger(__name__)


class LatencyTracker:
    bins_base: float = 1.5  # logarithmic binning exponent
    sleep_interval_sec: float = 100 / 1000
    stats_log_interval_sec: float = 5.0
    min_warn_interval_sec: float = 2.5

    def __init__(self) -> None:
        # duration lower bound (milliseconds) -> count
        self._bins: dict[int, int] = {}
        self._last_log_time = time.monotonic()
        self._logger = LOGGER.getChild(self.__class__.__name__)
        self._task: Optional[asyncio.Task] = None

    def _to_bin(self, value: float) -> int:
        return int(self.bins_base ** math.floor(math.log(value) / math.log(self.bins_base)))

    def _log_duration(self, duration_sec: float) -> None:
        duration_msec = duration_sec * 1000
        duration_bin = self._to_bin(duration_msec)
        self._bins[duration_bin] = self._bins.setdefault(duration_bin, 0) + 1

    def _log_stats(self) -> None:
        # PATCHED: upstream logs the collected bins at DEBUG here. Draining the
        # bins is kept (upstream resets them on every call; skipping that would
        # grow the dict forever), the logging is dropped.
        self._bins = {}

    def _maybe_log_stats(self) -> None:
        now = time.monotonic()
        if now - self._last_log_time < self.stats_log_interval_sec:
            return
        self._last_log_time = now
        self._log_stats()

    def _log_warn(self, duration_sec: float) -> None:
        # The idea is to add a log line explicitly,
        # to look for logs before it,
        # to maybe find whatever caused the latency.
        self._logger.warning("High latency: %.3fs", duration_sec)

    def _handle_duration(self, duration_sec: float) -> None:
        self._log_duration(duration_sec)
        self._maybe_log_stats()
        if duration_sec >= self.min_warn_interval_sec:
            self._log_warn(duration_sec)

    async def _task_main(self) -> None:
        while True:
            ts0 = time.monotonic()
            await asyncio.sleep(self.sleep_interval_sec)
            ts1 = time.monotonic()
            self._handle_duration(ts1 - ts0 - self.sleep_interval_sec)

    async def run_task(self) -> None:
        # Does not have to be `async`, but it is more consistent this way.
        asyncio.create_task(self._task_main())
