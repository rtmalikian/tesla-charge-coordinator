"""Fake Tesla fleet for testing and demos — no API calls, no real cars.

Simulates N vehicles charging over virtual time so the coordinator logic
can be exercised end-to-end (``tcc simulate``) and asserted in tests.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class FakeVehicle:
    vin: str
    name: str
    soc: float  # percent
    plugged_in: bool = True
    charging: bool = False
    capacity_kwh: float = 75.0
    charge_kw: float = 11.5  # typical 48A / 240V home charging
    efficiency: float = 0.92

    def tick(self, minutes: float) -> None:
        if self.charging and self.plugged_in:
            gain = self.charge_kw * self.efficiency / self.capacity_kwh * 100 * (minutes / 60)
            self.soc = min(100.0, self.soc + gain)

    @property
    def charging_state(self) -> str:
        if not self.plugged_in:
            return "Disconnected"
        if self.charging:
            return "Charging"
        return "Stopped"


class FakeTeslaClient:
    """Drop-in stand-in for TeslaClient with the methods the scheduler uses."""

    def __init__(self, vehicles: list[FakeVehicle]):
        self.vehicles = {v.vin: v for v in vehicles}
        self.command_log: list[tuple[str, str]] = []  # (command, vin)

    # -- scheduler interface -------------------------------------------
    def ensure_awake(self, vin: str) -> dict:
        v = self.vehicles[vin]
        return {
            "state": "online",
            "charge_state": {
                "battery_level": int(v.soc),
                "charge_limit_soc": 100,
                "charging_state": v.charging_state,
            },
        }

    def charge_start(self, vin: str) -> dict:
        self.vehicles[vin].charging = True
        self.command_log.append(("charge_start", vin))
        return {"result": True}

    def charge_stop(self, vin: str) -> dict:
        self.vehicles[vin].charging = False
        self.command_log.append(("charge_stop", vin))
        return {"result": True}

    # -- simulation driver ----------------------------------------------
    def tick(self, minutes: float) -> None:
        for v in self.vehicles.values():
            v.tick(minutes)

    def charging_vins(self) -> list[str]:
        return [vin for vin, v in self.vehicles.items() if v.charging and v.plugged_in]


def run_simulation(vehicles: list[FakeVehicle], coordinator, settings,
                   start: datetime, hours: float = 12.0,
                   tick_minutes: float = 5.0) -> dict:
    """Drive the real scheduler against the fake fleet over virtual time.

    Returns a report with the per-tick timeline and invariant checks.
    """
    from .scheduler import run_once  # local import to avoid cycles

    client = FakeTeslaClient(vehicles)
    now = start
    end = start + timedelta(hours=hours)
    timeline: list[dict] = []
    max_concurrent = 0

    while now < end:
        decision = run_once(client, coordinator, settings, now=now)
        concurrent = client.charging_vins()
        max_concurrent = max(max_concurrent, len(concurrent))
        timeline.append({
            "time": now.strftime("%H:%M"),
            "charging": [client.vehicles[v].name for v in concurrent],
            "socs": {client.vehicles[v].name: round(client.vehicles[v].soc, 1)
                     for v in client.vehicles},
            "note": decision.note,
        })
        client.tick(tick_minutes)
        now += timedelta(minutes=tick_minutes)
        # early exit: everything at target and nothing charging
        if all(v.soc >= settings.target_soc or not v.plugged_in for v in vehicles) \
                and not concurrent:
            break

    return {
        "timeline": timeline,
        "max_concurrent_charging": max_concurrent,
        "final_soc": {v.name: round(v.soc, 1) for v in vehicles},
        "commands": client.command_log,
    }
