"""Tests for configuration parsing."""
import pytest

from tesla_charge_coordinator.config import ConfigError, load_settings


BASE = {
    "TESLA_CLIENT_ID": "id",
    "TESLA_CLIENT_SECRET": "secret",
    "TCC_VEHICLES": "Daily=VIN1,Second=VIN2:5",
}


def test_parse_vehicles_with_names_and_priority():
    s = load_settings({**BASE}, require_auth=True)
    assert [(v.name, v.vin, v.priority) for v in s.vehicles] == [
        ("Daily", "VIN1", 0),
        ("Second", "VIN2", 5),
    ]


def test_parse_bare_vins():
    s = load_settings({**BASE, "TCC_VEHICLES": "VIN1,VIN2"}, require_auth=True)
    assert [v.name for v in s.vehicles] == ["car-1", "car-2"]
    assert [v.priority for v in s.vehicles] == [0, 1]


def test_defaults():
    s = load_settings({**BASE}, require_auth=True)
    assert s.target_soc == 80
    assert s.poll_interval_seconds == 300
    assert s.region == "na"
    assert s.respect_manual_override is True
    assert s.dry_run is False
    assert s.offpeak_start is None


def test_offpeak_parsing():
    s = load_settings({**BASE, "TCC_OFFPEAK_START": "22:00", "TCC_OFFPEAK_END": "06:00"})
    assert (s.offpeak_start.hour, s.offpeak_start.minute) == (22, 0)
    assert (s.offpeak_end.hour, s.offpeak_end.minute) == (6, 0)


def test_missing_credentials_rejected():
    with pytest.raises(ConfigError):
        load_settings({"TCC_VEHICLES": "VIN1"}, require_auth=True)


def test_missing_vehicles_rejected():
    with pytest.raises(ConfigError):
        load_settings({"TESLA_CLIENT_ID": "x", "TESLA_CLIENT_SECRET": "y"}, require_auth=True)


def test_bad_region_rejected():
    with pytest.raises(ConfigError):
        load_settings({**BASE, "TESLA_REGION": "mars"})


def test_half_offpeak_window_rejected():
    with pytest.raises(ConfigError):
        load_settings({**BASE, "TCC_OFFPEAK_START": "22:00"})
