"""Publish SP half-hour readings as corrected hourly energy statistics."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    statistics_during_period,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.util.unit_conversion import EnergyConverter

from .const import DOMAIN

STATISTIC_ID = f"{DOMAIN}:electricity_consumption"
NAME = "SP Services Electricity Consumption"
UTC = ZoneInfo("UTC")


async def async_sync_statistics(hass: HomeAssistant, readings: dict[datetime, float]) -> None:
    """Import hourly sums, preserving the running sum before the correction window."""
    if not readings:
        return

    by_hour: dict[datetime, float] = {}
    for local_start, value in readings.items():
        utc_start = local_start.astimezone(UTC)
        hour = utc_start.replace(minute=0, second=0, microsecond=0)
        by_hour[hour] = by_hour.get(hour, 0.0) + value

    recorder = get_instance(hass)
    earliest = min(by_hour)
    latest = max(by_hour)
    # A stable prefix gives the running sum continuity; fetch the record just
    # before the rolling repair window rather than summing the window twice.
    previous = await recorder.async_add_executor_job(
        statistics_during_period,
        hass,
        earliest - timedelta(hours=1),
        earliest,
        {STATISTIC_ID},
        "hour",
        None,
        {"sum"},
    )
    base_sum = 0.0
    # The API refreshes the last 3 calendar days. As long as the previous
    # statistic is older than the first regenerated hour it is a safe prefix.
    previous_rows = previous.get(STATISTIC_ID, [])
    if previous_rows:
        base_sum = float(previous_rows[-1].get("sum", 0))

    metadata = StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=NAME,
        source=DOMAIN,
        statistic_id=STATISTIC_ID,
        unit_class=EnergyConverter.UNIT_CLASS,
        unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    )
    running = base_sum
    output: list[StatisticData] = []
    for hour in sorted(by_hour):
        state = by_hour[hour]
        running += state
        output.append(StatisticData(start=hour, state=state, sum=running))

    async_add_external_statistics(hass, metadata, output)
