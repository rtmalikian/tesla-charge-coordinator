"""Thin wrapper around the official Tesla Fleet API.

Docs: https://developer.tesla.com/docs/fleet-api

Auth model: the user registers a developer application at
developer.tesla.com, then authorizes it once via OAuth (``tcc auth``).
Tokens are stored locally (default ``~/.tcc/tokens.json``) and refreshed
automatically. Vehicles also need the app's virtual key installed —
see ``tcc pairing`` / README for the one-time setup.
"""
from __future__ import annotations

import json
import time
import urllib.parse
from pathlib import Path

import requests

AUTH_URL = "https://auth.tesla.com/oauth2/v3/authorize"
TOKEN_URL = "https://auth.tesla.com/oauth2/v3/token"

REGION_BASE_URLS = {
    "na": "https://fleet-api.prd.na.vn.cloud.tesla.com",
    "eu": "https://fleet-api.prd.eu.vn.cloud.tesla.com",
    "cn": "https://fleet-api.prd.cn.vn.cloud.tesla.cn",
}

# Scopes needed: read vehicle state + issue charging commands.
SCOPES = "openid offline_access vehicle_device_data vehicle_location vehicle_cmds vehicle_charging_cmds"


class TeslaApiError(Exception):
    """Fleet API returned an error."""


class TeslaClient:
    def __init__(self, client_id: str, client_secret: str, region: str = "na",
                 redirect_uri: str = "http://localhost:8080/callback",
                 token_file: str = "~/.tcc/tokens.json"):
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = REGION_BASE_URLS[region]
        self.redirect_uri = redirect_uri
        self.token_file = Path(token_file).expanduser()
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "tesla-charge-coordinator/0.1.0"})
        self._tokens: dict | None = None

    # -- OAuth -----------------------------------------------------------
    def authorize_url(self, state: str = "tcc") -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": SCOPES,
            "state": state,
            "prompt": "login",
            "locale": "en-US",
        }
        return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    def exchange_code(self, code: str) -> dict:
        data = {
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "audience": self.base_url,
            "redirect_uri": self.redirect_uri,
        }
        return self._save_tokens(self._token_request(data))

    def refresh(self) -> dict:
        tokens = self._load_tokens()
        data = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": tokens["refresh_token"],
        }
        return self._save_tokens(self._token_request(data))

    def _token_request(self, data: dict) -> dict:
        resp = self.session.post(TOKEN_URL, data=data, timeout=30)
        if resp.status_code != 200:
            raise TeslaApiError(f"Token request failed ({resp.status_code}): {resp.text[:200]}")
        return resp.json()

    def _load_tokens(self) -> dict:
        if self._tokens:
            return self._tokens
        if not self.token_file.exists():
            raise TeslaApiError(
                f"No tokens found at {self.token_file}. Run `tcc auth` first."
            )
        self._tokens = json.loads(self.token_file.read_text())
        return self._tokens

    def _save_tokens(self, tokens: dict) -> dict:
        tokens["obtained_at"] = int(time.time())
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        self.token_file.write_text(json.dumps(tokens, indent=2))
        self.token_file.chmod(0o600)
        self._tokens = tokens
        return tokens

    def _auth_header(self) -> dict:
        tokens = self._load_tokens()
        if tokens.get("expires_in") and time.time() > tokens["obtained_at"] + tokens["expires_in"] - 300:
            tokens = self.refresh()
        return {"Authorization": f"Bearer {tokens['access_token']}"}

    # -- API -------------------------------------------------------------
    def _request(self, method: str, path: str, **kwargs) -> dict:
        resp = self.session.request(
            method, f"{self.base_url}{path}", headers=self._auth_header(), timeout=30, **kwargs
        )
        if resp.status_code == 401:  # token may have died; try one refresh
            self.refresh()
            resp = self.session.request(
                method, f"{self.base_url}{path}", headers=self._auth_header(), timeout=30, **kwargs
            )
        if resp.status_code >= 400:
            raise TeslaApiError(f"{method} {path} -> {resp.status_code}: {resp.text[:200]}")
        return resp.json().get("response", {})

    def get_vehicles(self) -> list[dict]:
        return self._request("GET", "/api/1/vehicles")

    def vehicle_data(self, vin: str) -> dict:
        vehicle_id = self._vehicle_id(vin)
        return self._request("GET", f"/api/1/vehicles/{vehicle_id}/vehicle_data")

    def wake_up(self, vin: str) -> dict:
        return self._request("POST", f"/api/1/vehicles/{self._vehicle_id(vin)}/wake_up")

    def command(self, vin: str, name: str, data: dict | None = None) -> dict:
        return self._request(
            "POST",
            f"/api/1/vehicles/{self._vehicle_id(vin)}/command/{name}",
            json=data or {},
        )

    def charge_start(self, vin: str) -> dict:
        return self.command(vin, "charge_start")

    def charge_stop(self, vin: str) -> dict:
        return self.command(vin, "charge_stop")

    def set_charge_limit(self, vin: str, percent: int) -> dict:
        return self.command(vin, "set_charge_limit", {"percent": percent})

    def set_charging_amps(self, vin: str, amps: int) -> dict:
        return self.command(vin, "set_charging_amps", {"charging_amps": amps})

    def ensure_awake(self, vin: str, timeout: int = 90) -> dict:
        """Wake the vehicle if asleep, then return fresh vehicle_data."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                data = self.vehicle_data(vin)
            except TeslaApiError:
                data = None
            if data and data.get("state") == "online":
                return data
            try:
                self.wake_up(vin)
            except TeslaApiError:
                pass
            time.sleep(5)
        raise TeslaApiError(f"Vehicle {vin} did not wake within {timeout}s")

    # -- helpers ---------------------------------------------------------
    _id_cache: dict = {}

    def _vehicle_id(self, vin: str) -> int:
        if vin not in self._id_cache:
            for v in self.get_vehicles():
                self._id_cache[v["vin"]] = v["id"]
            if vin not in self._id_cache:
                raise TeslaApiError(f"VIN {vin} not found in your Tesla account")
        return self._id_cache[vin]
