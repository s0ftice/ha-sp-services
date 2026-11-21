"""Poll SP charts and synchronize hourly energy statistics."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import logging
import random
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import SPApi, SPApiError, SPAuthError
from .const import CONF_POLL_MINUTES, DOMAIN, HISTORY_DAYS
from .statistics import async_sync_statistics

_LOGGER = logging.getLogger(__name__)
SGT = ZoneInfo("Asia/Singapore")


class ElectricityCoordinator(DataUpdateCoordinator[dict]):
    """Refresh three calendar days and persist corrected half-hour readings."""

    def __init__(self, hass: HomeAssistant, api: SPApi, entry: ConfigEntry) -> None:
        self.api = api
        self.entry = entry
        self.hass = hass
        self.readings: dict[datetime, float] = {}
        self.rows: list[dict] = []
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=None)
        self.interval = timedelta(minutes=entry.options.get(CONF_POLL_MINUTES, 30))
        self._cancel_timer = None

    def start_polling(self) -> None:
        """Start polling after a small startup jitter, then relative to startup."""
        delay = random.uniform(10, 180)
        self._cancel_timer = async_call_later(self.hass, delay, self._first_poll)

    @callback
    def _first_poll(self, _now) -> None:
        """Trigger first refresh and schedule recurring relative polls."""
        self._cancel_timer = None
        self.hass.async_create_task(self.async_refresh())
        self._cancel_timer = async_track_time_interval(
            self.hass, self._scheduled_refresh, self.interval
        )

    @callback
    def _scheduled_refresh(self, _now) -> None:
        self.hass.async_create_task(self.async_refresh())

    def stop_polling(self) -> None:
        """Cancel scheduled polling."""
        if self._cancel_timer:
            self._cancel_timer()
            self._cancel_timer = None

    async def _async_update_data(self) -> dict:
        now = dt_util.now(SGT)
        merged = dict(self.readings)
        fresh_rows: list[dict] = []
        try:
            for offset in range(HISTORY_DAYS):
                day = now.date() - timedelta(days=offset)
                rows = await self.api.hourly(day)
                fresh_rows.extend(rows)
                self._merge_rows(day, rows, now, merged)
        except SPAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SPApiError as err:
            raise UpdateFailed(str(err)) from err

        # Keep only the rolling history window in memory.
        cutoff = now.date() - timedelta(days=HISTORY_DAYS - 1)
        merged = {stamp: value for stamp, value in merged.items() if stamp.date() >= cutoff}
        self.readings = merged
        self.rows = fresh_rows
        await self._persist_tokens()
        await async_sync_statistics(self.hass, self.readings)
        return {
            "latest": max(self.readings.items(), default=(None, None), key=lambda item: item[0]),
            "updated_at": dt_util.now(),
            "interval_count": len(self.readings),
            "days": HISTORY_DAYS,
        }

    def _merge_rows(self, day: date, rows: list[dict], now: datetime, merged: dict[datetime, float]) -> None:
        parsed: list[tuple[datetime, float]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                local = datetime.strptime(row["period"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=SGT)
                value = float(row["current"])
            except (KeyError, TypeError, ValueError):
                continue
            if local.date() != day or local + timedelta(minutes=30) > now:
                continue
            if value < 0:
                continue
            parsed.append((local, value))

        parsed.sort()
        # SP fills unavailable chart positions with zero. Do not commit a trailing
        # zero run for today or yesterday; allow two full calendar days for late
        # readings to arrive before treating that tail as settled.
        final_zero_boundary = None
        for index in range(len(parsed) - 1, -1, -1):
            if parsed[index][1] != 0:
                final_zero_boundary = index
                break
        settled_day = day < now.date() - timedelta(days=1)
        for index, (local, value) in enumerate(parsed):
            if value == 0 and not settled_day and final_zero_boundary is None:
                continue
            if value == 0 and not settled_day and index > final_zero_boundary:
                continue
            merged[local] = value

    async def _persist_tokens(self) -> None:
        if self.api.tokens != self.entry.data.get("tokens"):
            self.hass.config_entries.async_update_entry(
                self.entry,
                data={**self.entry.data, "tokens": self.api.tokens},
            )
