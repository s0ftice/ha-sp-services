"""Electricity sensors for SP Services."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_ACCOUNT_NUMBER, DOMAIN
from .coordinator import ElectricityCoordinator
from .statistics import NAME, STATISTIC_ID


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    """Set up the electricity sensors."""
    coordinator: ElectricityCoordinator = entry.runtime_data
    async_add_entities([
        SPTotalConsumptionSensor(coordinator, entry),
        SPLatestIntervalSensor(coordinator, entry),
        SPUpdateSensor(coordinator, entry),
    ])


class SPBaseSensor(CoordinatorEntity, SensorEntity):
    """Shared metadata."""

    def __init__(self, coordinator: ElectricityCoordinator, entry: ConfigEntry, unique_id: str, name: str):
        super().__init__(coordinator)
        account = entry.data[CONF_ACCOUNT_NUMBER]
        self._attr_unique_id = f"{account}_{unique_id}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, account)},
            name=f"SP Services {account[-4:]}",
            manufacturer="SP Group",
            model="Electricity account",
        )


class SPTotalConsumptionSensor(SPBaseSensor):
    """Non-meter total for the latest three fetched days."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "electricity_consumption", NAME)
        self._attr_entity_registry_enabled_default = False
        self._attr_extra_state_attributes = {"statistic_id": STATISTIC_ID, "source": "SP Group electricity chart"}

    @property
    def native_value(self):
        return round(sum(self.coordinator.readings.values()), 3) if self.coordinator.readings else None


class SPLatestIntervalSensor(SPBaseSensor):
    """Most recently known 30-minute interval reading."""

    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "latest_interval", "SP Electricity Latest Interval")

    @property
    def native_value(self):
        latest = self.coordinator.data.get("latest") if self.coordinator.data else None
        return round(latest[1], 3) if latest and latest[1] is not None else None

    @property
    def extra_state_attributes(self):
        latest = self.coordinator.data.get("latest") if self.coordinator.data else None
        return {"interval_start": latest[0].isoformat() if latest and latest[0] else None,
                "timezone": "Asia/Singapore", "interval_minutes": 30}


class SPUpdateSensor(SPBaseSensor):
    """Last successful API fetch time."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "last_updated", "SP Electricity Last Updated")

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.data.get("updated_at") if self.coordinator.data else None

    @property
    def extra_state_attributes(self):
        return {"source_days": self.coordinator.data.get("days") if self.coordinator.data else None,
                "known_interval_count": self.coordinator.data.get("interval_count") if self.coordinator.data else 0}
