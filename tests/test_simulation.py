"""End-to-end test: run the real scheduler against a fake two-car fleet.

The key invariant under test: the two vehicles must NEVER charge
simultaneously, and both must reach their target state of charge.
"""
from datetime import datetime
from datetime import time as dtime

from tesla_charge_coordinator.config import Settings, VehicleConfig
from tesla_charge_coordinator.scheduler import make_coordinator
from tesla_charge_coordinator.simulator import FakeVehicle, run_simulation


def make_settings(**overrides):
    kwargs = dict(
        vehicles=[
            VehicleConfig(name="Model Y", vin="SIM1", priority=0),
            VehicleConfig(name="Model 3", vin="SIM2", priority=1),
        ],
        target_soc=80,
        offpeak_start=dtime(22, 0),
        offpeak_end=dtime(6, 0),
    )
    kwargs.update(overrides)
    return Settings(**kwargs)


def test_two_cars_never_charge_simultaneously():
    vehicles = [FakeVehicle(vin="SIM1", name="Model Y", soc=20.0),
                FakeVehicle(vin="SIM2", name="Model 3", soc=55.0)]
    settings = make_settings()
    report = run_simulation(
        vehicles, make_coordinator(settings), settings,
        start=datetime(2026, 10, 8, 22, 0), hours=12.0,
    )
    assert report["max_concurrent_charging"] <= 1, (
        f"two cars charged at once! timeline: "
        f"{[t for t in report['timeline'] if len(t['charging']) > 1]}"
    )


def test_both_cars_reach_target():
    vehicles = [FakeVehicle(vin="SIM1", name="Model Y", soc=20.0),
                FakeVehicle(vin="SIM2", name="Model 3", soc=55.0)]
    settings = make_settings()
    report = run_simulation(
        vehicles, make_coordinator(settings), settings,
        start=datetime(2026, 10, 8, 22, 0), hours=12.0,
    )
    assert report["final_soc"]["Model Y"] >= 80
    assert report["final_soc"]["Model 3"] >= 80


def test_priority_order_is_respected():
    """Car 1 (priority 0) must finish before car 2 (priority 1) starts."""
    vehicles = [FakeVehicle(vin="SIM1", name="Model Y", soc=70.0),
                FakeVehicle(vin="SIM2", name="Model 3", soc=70.0)]
    settings = make_settings()
    report = run_simulation(
        vehicles, make_coordinator(settings), settings,
        start=datetime(2026, 10, 8, 22, 0), hours=12.0,
    )
    starts = [c for c in report["commands"] if c[0] == "charge_start"]
    assert starts, "expected at least one charge_start"
    assert starts[0][1] == "SIM1", f"priority violated: {starts}"


def test_unplugged_car_does_not_block_the_other():
    vehicles = [FakeVehicle(vin="SIM1", name="Model Y", soc=20.0, plugged_in=False),
                FakeVehicle(vin="SIM2", name="Model 3", soc=55.0)]
    settings = make_settings()
    report = run_simulation(
        vehicles, make_coordinator(settings), settings,
        start=datetime(2026, 10, 8, 22, 0), hours=12.0,
    )
    assert report["max_concurrent_charging"] <= 1
    assert report["final_soc"]["Model 3"] >= 80
    # the unplugged car simply stays where it was
    assert report["final_soc"]["Model Y"] == 20.0
