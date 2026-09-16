"""Sensors, one device in Home Assistant per inverter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SolariotCoordinator


@dataclass(frozen=True, kw_only=True)
class SolariotSensorDescription(SensorEntityDescription):
    """A metric key plus how Home Assistant should treat it."""

    metric: str


def _power(key: str, name: str) -> SolariotSensorDescription:
    return SolariotSensorDescription(
        key=key, metric=key, name=name,
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
    )


def _energy(key: str, name: str) -> SolariotSensorDescription:
    # TOTAL_INCREASING, not MEASUREMENT: this is what makes the sensor usable in
    # Home Assistant's Energy dashboard. HA refuses it in the picker otherwise,
    # which is itself the check that the class is right.
    return SolariotSensorDescription(
        key=key, metric=key, name=name,
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    )


def _volt(key: str, name: str) -> SolariotSensorDescription:
    return SolariotSensorDescription(
        key=key, metric=key, name=name,
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
    )


SENSORS: tuple[SolariotSensorDescription, ...] = (
    _power("pvPower", "PV power"),
    _power("pv1Power", "PV1 power"),
    _power("pv2Power", "PV2 power"),
    _power("loadPower", "Load power"),
    _power("inverterPower", "Inverter power"),
    # Signed, and the sign is the server's: positive = discharging, positive =
    # importing. Left as-is rather than split into two always-positive sensors,
    # because a single signed number is what a flow card wants.
    _power("batteryFlow", "Battery power"),
    _power("gridFlow", "Grid power"),
    _power("epsPower", "EPS power"),
    _volt("batteryVoltage", "Battery voltage"),
    _volt("gridVoltage", "Grid voltage"),
    _volt("epsVoltage", "EPS voltage"),
    _volt("pv1Voltage", "PV1 voltage"),
    _volt("pv2Voltage", "PV2 voltage"),
    SolariotSensorDescription(
        key="batterySoc", metric="batterySoc", name="Battery SOC",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SolariotSensorDescription(
        key="batterySoh", metric="batterySoh", name="Battery SOH",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SolariotSensorDescription(
        key="batteryCurrent", metric="batteryCurrent", name="Battery current",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SolariotSensorDescription(
        key="gridFrequency", metric="gridFrequency", name="Grid frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SolariotSensorDescription(
        key="batteryTemperature", metric="batteryTemperature", name="Battery temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SolariotSensorDescription(
        key="inverterTemperature", metric="inverterTemperature", name="Inverter temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    # The heatsink, and on several brands the ONLY temperature that survives the
    # server's plausibility gate: LuxPower's `inverterTemperature` register is
    # unpopulated on the machines measured here and decodes to -100 °C, so it is
    # dropped upstream and this is what a dashboard has left to draw.
    SolariotSensorDescription(
        key="radiator1Temperature", metric="radiator1Temperature", name="Radiator temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    _energy("pvEnergyToday", "PV energy today"),
    _energy("loadEnergyToday", "Load energy today"),
    _energy("importEnergyToday", "Grid import today"),
    _energy("exportEnergyToday", "Grid export today"),
    _energy("batteryChargeEnergyToday", "Battery charged today"),
    _energy("batteryDischargeEnergyToday", "Battery discharged today"),
    _energy("pvEnergyTotal", "PV energy total"),
    _energy("importEnergyTotal", "Grid import total"),
    _energy("exportEnergyTotal", "Grid export total"),
    SolariotSensorDescription(
        key="inverterState", metric="inverterState", name="Inverter state",
    ),
    # The numeric code as well as the sentence, and enabled by default: a flow
    # card classifies the inverter from the CODE (0 standby, 2 inverting,
    # 16 discharging…). Given only the sentence it has nothing to match and
    # draws a blank status pill, which is what shipped first.
    SolariotSensorDescription(
        key="inverterStateCode", metric="inverterStateCode", name="Inverter state code",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SolariotCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SolariotSensor] = []
    for sn, payload in (coordinator.data or {}).items():
        metrics = (payload.get("latest") or {}).get("metrics") or {}
        for desc in SENSORS:
            # Only create what the device actually reports. A brand with no EPS
            # should not grow three EPS entities that read "unknown" forever —
            # the server omits an absent field rather than sending null, which is
            # what makes this test meaningful.
            if desc.metric in metrics:
                entities.append(SolariotSensor(coordinator, sn, payload["device"], desc))
    async_add_entities(entities)


class SolariotSensor(CoordinatorEntity[SolariotCoordinator], SensorEntity):
    """One metric of one device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SolariotCoordinator,
        device_sn: str,
        device: dict[str, Any],
        description: SolariotSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._device_sn = device_sn
        self._attr_unique_id = f"{device_sn}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_sn)},
            name=device.get("name") or device_sn,
            manufacturer="Solariot",
            model=device.get("type"),
            serial_number=device_sn,
        )

    @property
    def native_value(self) -> Any:
        payload = (self.coordinator.data or {}).get(self._device_sn) or {}
        metrics = (payload.get("latest") or {}).get("metrics") or {}
        return metrics.get(self.entity_description.metric)

    @property
    def available(self) -> bool:
        """Availability is PER DEVICE, not per integration.

        One inverter going quiet must not take the other one's entities down
        with it, so this looks at that device's own payload rather than at the
        coordinator's overall success.
        """
        if not self.coordinator.last_update_success:
            return False
        payload = (self.coordinator.data or {}).get(self._device_sn) or {}
        metrics = (payload.get("latest") or {}).get("metrics") or {}
        return self.entity_description.metric in metrics
