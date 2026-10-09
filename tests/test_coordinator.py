"""Unit tests for the coordinator decision logic."""
from datetime import datetime
from datetime import time as dtime

import pytest

from tesla_charge_coordinator.config import VehicleConfig
from tesla_charge_coordinator.coordinator import (
    Coordinator,
    StartCharging,
    StopCharging,
    VehicleState,
)

NOW = datetime(2026, 10, 8, 23, 0)


def make_coordinator(**kwargs):
    vehicles = [
        VehicleConfig(name="Model Y", vin="VIN1", priority=0),
        VehicleConfig(name="Model 3", vin="VIN2", priority=1),
    ]
    defaults = dict(vehicles=vehicles, target_soc=80)
    defaults.update(kwargs)
    return Coordinator(**defaults)


def state(vin, name, priority, battery, charging_state="Stopped", target=80):
    return VehicleState(
        vin=vin, name=name, priority=priority, battery_level=battery,
        charge_limit_soc=100, charging_state=charging_state, target_soc=target,
    )


def test_first_car_starts_when_nothing_charging():
    c = make_coordinator()
    d = c.decide([state("VIN1", "Model Y", 0, 30), state("VIN2", "Model 3", 1, 30)], NOW)
    assert any(isinstance(a, StartCharging) and a.vin == "VIN1" for a in d.actions)
    assert c.active_vin == "VIN1"


def test_second_car_waits_while_first_charges():
    c = make_coordinator()
    c.decide([state("VIN1", "Model Y", 0, 30, "Charging"), state("VIN2", "Model 3", 1, 30)], NOW)
    d = c.decide([state("VIN1", "Model Y", 0, 50, "Charging"), state("VIN2", "Model 3", 1, 30)], NOW)
    assert not any(isinstance(a, StartCharging) and a.vin == "VIN2" for a in d.actions)
    assert c.active_vin == "VIN1"


def test_handoff_when_first_car_finishes():
    c = make_coordinator()
    c.active_vin = "VIN1"
    d = c.decide([
        state("VIN1", "Model Y", 0, 80, "Complete"),  # hit target
        state("VIN2", "Model 3", 1, 30, "Stopped"),
    ], NOW)
    starts = [a for a in d.actions if isinstance(a, StartCharging)]
    assert len(starts) == 1 and starts[0].vin == "VIN2"
    assert c.active_vin == "VIN2"


def test_unexpected_second_charger_is_stopped():
    # With manual-override respect disabled, the coordinator enforces
    # one-at-a-time strictly (with it enabled, a second charging car is
    # treated as a manual session and coordination suspends instead).
    c = make_coordinator(respect_manual_override=False)
    c.active_vin = "VIN1"
    d = c.decide([
        state("VIN1", "Model Y", 0, 30, "Charging"),
        state("VIN2", "Model 3", 1, 30, "Charging"),  # should not happen
    ], NOW)
    stops = [a for a in d.actions if isinstance(a, StopCharging)]
    assert any(a.vin == "VIN2" for a in stops)
    assert not any(isinstance(a, StopCharging) and a.vin == "VIN1" for a in stops)


def test_manual_override_suspends_coordination():
    c = make_coordinator(respect_manual_override=True)
    c.active_vin = "VIN1"
    d = c.decide([
        state("VIN1", "Model Y", 0, 30, "Charging"),
        state("VIN2", "Model 3", 1, 30, "Charging"),  # driver started it manually
    ], NOW)
    assert d.suspended is True
    assert d.actions == []  # we back off, we don't fight the driver


def test_unplugged_car_is_skipped():
    c = make_coordinator()
    d = c.decide([
        state("VIN1", "Model Y", 0, 10, "Disconnected"),  # not plugged in
        state("VIN2", "Model 3", 1, 30, "Stopped"),
    ], NOW)
    starts = [a for a in d.actions if isinstance(a, StartCharging)]
    assert len(starts) == 1 and starts[0].vin == "VIN2"


def test_nothing_starts_outside_offpeak_window():
    c = make_coordinator(offpeak_start=dtime(22, 0), offpeak_end=dtime(6, 0))
    noon = datetime(2026, 10, 8, 12, 0)
    d = c.decide([
        state("VIN1", "Model Y", 0, 30, "Charging"),
        state("VIN2", "Model 3", 1, 30, "Stopped"),
    ], noon)
    stops = [a for a in d.actions if isinstance(a, StopCharging)]
    assert any(a.vin == "VIN1" for a in stops)
    assert not any(isinstance(a, StartCharging) for a in d.actions)


def test_charging_allowed_inside_offpeak_window():
    c = make_coordinator(offpeak_start=dtime(22, 0), offpeak_end=dtime(6, 0))
    night = datetime(2026, 10, 8, 23, 30)
    d = c.decide([state("VIN1", "Model Y", 0, 30), state("VIN2", "Model 3", 1, 30)], night)
    assert any(isinstance(a, StartCharging) for a in d.actions)


def test_all_done_stops_stragglers():
    c = make_coordinator()
    c.active_vin = "VIN1"
    d = c.decide([
        state("VIN1", "Model Y", 0, 85, "Charging"),  # above target but still drawing
        state("VIN2", "Model 3", 1, 90, "Stopped"),
    ], NOW)
    stops = [a for a in d.actions if isinstance(a, StopCharging)]
    assert any(a.vin == "VIN1" for a in stops)
    assert c.active_vin is None
