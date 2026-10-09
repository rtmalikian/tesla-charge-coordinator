"""Run loop: read vehicle state, ask the coordinator, execute the decision."""
from __future__ import annotations

import logging
import time
from datetime import datetime

from .config import Settings
from .coordinator import (
    Coordinator,
    Decision,
    StartCharging,
    StopCharging,
    VehicleState,
)
from .tesla_client import TeslaApiError, TeslaClient

log = logging.getLogger("tcc")


def build_states(client, settings: Settings) -> list[VehicleState]:
    """Fetch fresh state for every configured vehicle."""
    states = []
    for vc in settings.vehicles:
        try:
            data = client.ensure_awake(vc.vin)
        except TeslaApiError as exc:
            log.warning("%s: could not reach vehicle: %s", vc.name, exc)
            continue
        charge = data.get("charge_state", {}) or {}
        states.append(
            VehicleState(
                vin=vc.vin,
                name=vc.name,
                priority=vc.priority,
                battery_level=int(charge.get("battery_level", 0) or 0),
                charge_limit_soc=int(charge.get("charge_limit_soc", 100) or 100),
                charging_state=str(charge.get("charging_state", "Disconnected")),
                target_soc=settings.target_soc,
            )
        )
    return states


def execute(client, settings: Settings, decision: Decision) -> None:
    for action in decision.actions:
        if isinstance(action, StopCharging):
            log.info("STOP  %s (%s)", action.name, action.reason)
            if not settings.dry_run:
                try:
                    client.charge_stop(action.vin)
                except TeslaApiError as exc:
                    log.warning("charge_stop failed for %s: %s", action.name, exc)
        elif isinstance(action, StartCharging):
            log.info("START %s", action.name)
            if not settings.dry_run:
                try:
                    client.charge_start(action.vin)
                except TeslaApiError as exc:
                    log.warning("charge_start failed for %s: %s", action.name, exc)
    if decision.note:
        log.info("note: %s", decision.note)


def run_once(client, coordinator: Coordinator, settings: Settings,
             now: datetime | None = None) -> Decision:
    """A single sense-decide-act pass. Returns the decision (test hook)."""
    now = now or datetime.now()
    states = build_states(client, settings)
    if not states:
        log.warning("No vehicle states available; skipping pass")
        return Decision(note="no vehicle states")
    for s in states:
        log.info(
            "%-12s %3d%% %-12s target %d%%",
            s.name, s.battery_level, s.charging_state, s.target_soc,
        )
    decision = coordinator.decide(states, now)
    execute(client, settings, decision)
    return decision


def make_coordinator(settings: Settings) -> Coordinator:
    return Coordinator(
        vehicles=settings.vehicles,
        target_soc=settings.target_soc,
        offpeak_start=settings.offpeak_start,
        offpeak_end=settings.offpeak_end,
        respect_manual_override=settings.respect_manual_override,
    )


def run_loop(settings: Settings) -> None:
    client = TeslaClient(
        client_id=settings.client_id,
        client_secret=settings.client_secret,
        region=settings.region,
        redirect_uri=settings.redirect_uri,
        token_file=settings.token_file,
    )
    coordinator = make_coordinator(settings)
    mode = "DRY-RUN" if settings.dry_run else "LIVE"
    log.info("Tesla Charge Coordinator starting (%s), poll every %ds",
             mode, settings.poll_interval_seconds)
    while True:
        try:
            run_once(client, coordinator, settings)
        except TeslaApiError as exc:
            log.error("Fleet API error: %s", exc)
        except Exception:  # never let the daemon die silently
            log.exception("Unexpected error in run loop")
        time.sleep(settings.poll_interval_seconds)
