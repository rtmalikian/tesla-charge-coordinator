"""Core coordination logic: guarantee at most one vehicle charges at a time.

This module is pure logic — no network calls, no clock reads except the
``now`` argument — so it is fully unit-testable and safe to simulate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from datetime import time as dtime


@dataclass
class VehicleState:
    """Snapshot of one vehicle's charging-relevant state."""

    vin: str
    name: str
    priority: int  # lower number = charged first
    battery_level: int  # percent
    charge_limit_soc: int  # percent, car's own limit
    charging_state: str  # Charging, Stopped, Complete, Disconnected, ...
    target_soc: int  # coordinator's target for this car

    @property
    def plugged_in(self) -> bool:
        return self.charging_state != "Disconnected"

    @property
    def is_charging(self) -> bool:
        return self.charging_state == "Charging"

    @property
    def needs_charge(self) -> bool:
        return (
            self.plugged_in
            and self.battery_level < self.target_soc
            and self.charging_state != "Complete"
        )


@dataclass
class StartCharging:
    vin: str
    name: str


@dataclass
class StopCharging:
    vin: str
    name: str
    reason: str


@dataclass
class Decision:
    actions: list = field(default_factory=list)  # list[StartCharging | StopCharging]
    active_vin: str | None = None
    note: str = ""
    suspended: bool = False  # True when a manual session pauses coordination


class Coordinator:
    """Decides which vehicle (if any) should be charging right now."""

    def __init__(
        self,
        vehicles: list,
        target_soc: int = 80,
        offpeak_start: dtime | None = None,
        offpeak_end: dtime | None = None,
        respect_manual_override: bool = True,
    ):
        self.vehicles = {v.vin: v for v in vehicles}
        self.target_soc = target_soc
        self.offpeak_start = offpeak_start
        self.offpeak_end = offpeak_end
        self.respect_manual_override = respect_manual_override
        self.active_vin: str | None = None  # VIN of the session we currently own

    # -- helpers ---------------------------------------------------------
    def _in_offpeak(self, now: datetime) -> bool:
        if self.offpeak_start is None:
            return True
        t = now.time()
        if self.offpeak_start <= self.offpeak_end:  # same-day window
            return self.offpeak_start <= t < self.offpeak_end
        return t >= self.offpeak_start or t < self.offpeak_end  # overnight window

    def _name(self, vin: str) -> str:
        v = self.vehicles.get(vin)
        return v.name if v else vin

    # -- main entry point ------------------------------------------------
    def decide(self, states: list[VehicleState], now: datetime) -> Decision:
        by_vin = {s.vin: s for s in states}
        charging = [s for s in states if s.is_charging]

        # 1. Off-peak gate: outside the cheap-electricity window, charge nothing.
        if not self._in_offpeak(now):
            actions = [StopCharging(s.vin, s.name, "outside off-peak window") for s in charging]
            self.active_vin = None
            return Decision(actions, None, note="Outside off-peak window — all charging stopped")

        # 2. Manual override: the driver started a car we don't own. Back off.
        if self.respect_manual_override and self.active_vin:
            strangers = [s for s in charging if s.vin != self.active_vin]
            if strangers:
                names = ", ".join(s.name for s in strangers)
                return Decision(
                    [],
                    self.active_vin,
                    note=f"Manual charging detected on {names}; coordination suspended until it ends",
                    suspended=True,
                )

        # 3. Who still needs charge, in priority order (ties: lowest battery first).
        needing = sorted(
            (s for s in states if s.needs_charge),
            key=lambda s: (s.priority, s.battery_level),
        )

        # 4. Nobody needs charge: stop anything still drawing power.
        if not needing:
            actions = [
                StopCharging(s.vin, s.name, "target reached")
                for s in charging
            ]
            self.active_vin = None
            return Decision(actions, None, note="No vehicles need charging")

        top = needing[0]
        needing_vins = {s.vin for s in needing}

        # 5. Our session is still valid: keep it, stop any unexpected others.
        active = by_vin.get(self.active_vin) if self.active_vin else None
        if active is not None and active.vin in needing_vins and active.is_charging:
            actions = [
                StopCharging(s.vin, s.name, "not the active vehicle")
                for s in charging
                if s.vin != active.vin
            ]
            return Decision(actions, active.vin, note=f"{active.name} continues charging")

        # 6. Otherwise: stop everything except the winner, then start the winner.
        #    The winner is the highest-priority vehicle still needing charge,
        #    so a finished car hands off to the next one automatically.
        actions: list = [
            StopCharging(s.vin, s.name, "yielding to higher-priority vehicle")
            for s in charging
            if s.vin != top.vin
        ]
        if not top.is_charging:
            actions.append(StartCharging(top.vin, top.name))
        self.active_vin = top.vin
        return Decision(actions, top.vin, note=f"Charge session handed to {top.name}")
