"""Configuration loading for Tesla Charge Coordinator.

All settings come from environment variables (see ``.env.example``).
Copy ``.env.example`` to ``.env`` and fill in your values — ``.env`` is
git-ignored so secrets never end up in the repository.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time as dtime


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


@dataclass
class VehicleConfig:
    name: str
    vin: str
    priority: int  # lower number = charged first


@dataclass
class Settings:
    client_id: str = ""
    client_secret: str = ""
    region: str = "na"
    redirect_uri: str = "http://localhost:8080/callback"
    token_file: str = "~/.tcc/tokens.json"
    vehicles: list[VehicleConfig] = field(default_factory=list)
    target_soc: int = 80
    poll_interval_seconds: int = 300
    offpeak_start: dtime | None = None
    offpeak_end: dtime | None = None
    respect_manual_override: bool = True
    dry_run: bool = False


def _parse_vehicles(raw: str) -> list[VehicleConfig]:
    """Parse TCC_VEHICLES.

    Format: ``name=VIN:priority,name2=VIN2`` — priority is optional and
    defaults to list order (first listed = highest priority).
    A bare ``VIN`` without a name is also accepted.
    """
    vehicles: list[VehicleConfig] = []
    for idx, item in enumerate(raw.split(",")):
        item = item.strip()
        if not item:
            continue
        name: str | None = None
        priority = idx
        if "=" in item:
            name, item = item.split("=", 1)
            name = name.strip()
        if ":" in item:
            item, pr = item.rsplit(":", 1)
            try:
                priority = int(pr.strip())
            except ValueError as exc:
                raise ConfigError(f"Invalid priority in TCC_VEHICLES entry {item!r}: {pr!r}") from exc
        vin = item.strip()
        if not vin:
            raise ConfigError("Empty VIN in TCC_VEHICLES")
        vehicles.append(VehicleConfig(name=name or f"car-{idx + 1}", vin=vin, priority=priority))
    if not vehicles:
        raise ConfigError("TCC_VEHICLES is empty — add at least one name=VIN entry")
    return vehicles


def _parse_time(raw: str) -> dtime:
    try:
        hour, minute = raw.strip().split(":")
        return dtime(int(hour), int(minute))
    except (ValueError, AttributeError) as exc:
        raise ConfigError(f"Invalid time {raw!r} — expected HH:MM") from exc


def _parse_bool(raw: str, name: str) -> bool:
    return raw.strip().lower() in ("1", "true", "yes", "on")


def load_settings(env: dict | None = None, require_auth: bool = True) -> Settings:
    """Load settings from the environment (or a supplied mapping)."""
    env = env if env is not None else os.environ

    client_id = env.get("TESLA_CLIENT_ID", "").strip()
    client_secret = env.get("TESLA_CLIENT_SECRET", "").strip()
    if require_auth and (not client_id or not client_secret):
        raise ConfigError(
            "TESLA_CLIENT_ID and TESLA_CLIENT_SECRET are required. "
            "Copy .env.example to .env and fill them in (see README)."
        )

    region = env.get("TESLA_REGION", "na").strip().lower()
    if region not in ("na", "eu", "cn"):
        raise ConfigError(f"TESLA_REGION must be one of na/eu/cn, got {region!r}")

    offpeak_start = offpeak_end = None
    if env.get("TCC_OFFPEAK_START"):
        offpeak_start = _parse_time(env["TCC_OFFPEAK_START"])
    if env.get("TCC_OFFPEAK_END"):
        offpeak_end = _parse_time(env["TCC_OFFPEAK_END"])
    if bool(offpeak_start) != bool(offpeak_end):
        raise ConfigError("TCC_OFFPEAK_START and TCC_OFFPEAK_END must be set together")

    try:
        target_soc = int(env.get("TCC_TARGET_SOC", "80"))
        poll_interval = int(env.get("TCC_POLL_INTERVAL_SECONDS", "300"))
    except ValueError as exc:
        raise ConfigError("TCC_TARGET_SOC and TCC_POLL_INTERVAL_SECONDS must be integers") from exc
    if not 1 <= target_soc <= 100:
        raise ConfigError("TCC_TARGET_SOC must be between 1 and 100")

    vehicles_raw = env.get("TCC_VEHICLES", "").strip()
    if require_auth or vehicles_raw:
        vehicles = _parse_vehicles(vehicles_raw)
    else:
        vehicles = []  # e.g. `tcc simulate` needs no real vehicles configured

    return Settings(
        client_id=client_id,
        client_secret=client_secret,
        region=region,
        redirect_uri=env.get("TESLA_REDIRECT_URI", "http://localhost:8080/callback").strip(),
        token_file=env.get("TESLA_TOKEN_FILE", "~/.tcc/tokens.json").strip(),
        vehicles=vehicles,
        target_soc=target_soc,
        poll_interval_seconds=poll_interval,
        offpeak_start=offpeak_start,
        offpeak_end=offpeak_end,
        respect_manual_override=_parse_bool(env.get("TCC_RESPECT_MANUAL_OVERRIDE", "true"), "TCC_RESPECT_MANUAL_OVERRIDE"),
        dry_run=_parse_bool(env.get("TCC_DRY_RUN", "false"), "TCC_DRY_RUN"),
    )
