"""Command-line interface: tcc auth | status | run | once | simulate | pairing"""
from __future__ import annotations

import argparse
import logging
import sys
import webbrowser
from datetime import datetime
from datetime import time as dtime

from dotenv import load_dotenv

from . import __version__
from .config import ConfigError, Settings, load_settings
from .scheduler import build_states, make_coordinator, run_loop, run_once
from .simulator import FakeVehicle, run_simulation
from .tesla_client import TeslaApiError, TeslaClient

log = logging.getLogger("tcc")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_auth(args, settings: Settings) -> int:
    client = TeslaClient(
        client_id=settings.client_id,
        client_secret=settings.client_secret,
        region=settings.region,
        redirect_uri=settings.redirect_uri,
        token_file=settings.token_file,
    )
    url = client.authorize_url()
    print("1. Open this URL in your browser and sign in with your Tesla account:\n")
    print(f"   {url}\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    print("2. After signing in you will be redirected to a localhost URL that fails to load.")
    print("   Copy the FULL redirected URL (it contains ?code=...) and paste it here.")
    redirected = input("Redirected URL: ").strip()
    if "code=" not in redirected:
        print("ERROR: no ?code= found in that URL.", file=sys.stderr)
        return 1
    code = redirected.split("code=")[1].split("&")[0]
    try:
        client.exchange_code(code)
    except TeslaApiError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"\nAuthorized! Tokens saved to {settings.token_file} (mode 600).")
    print("Next: install the app's virtual key on each car — run `tcc pairing`.")
    return 0


def cmd_pairing(_args, _settings: Settings) -> int:
    print(
        "One-time virtual-key setup (required before the app can send commands):\n\n"
        "  1. Host your app's public key at\n"
        "     https://<your-domain>/.well-known/appspecific/com.tesla.3p.public-key.pem\n"
        "     (see README 'Virtual key setup' for the openssl one-liner)\n"
        "  2. Register the key: POST /api/1/partner_accounts {\"domain\": \"<your-domain>\"}\n"
        "  3. In EACH Tesla, open https://www.tesla.com/_ak/<your-domain>\n"
        "     and approve the key from the car's touchscreen.\n\n"
        "Until the key is approved, charge_start / charge_stop commands are rejected\n"
        "and the car simply won't respond. `tcc status` shows whether commands work."
    )
    return 0


def cmd_status(_args, settings: Settings) -> int:
    client = TeslaClient(
        client_id=settings.client_id,
        client_secret=settings.client_secret,
        region=settings.region,
        redirect_uri=settings.redirect_uri,
        token_file=settings.token_file,
    )
    states = build_states(client, settings)
    if not states:
        print("No vehicles reachable.")
        return 1
    print(f"{'Vehicle':<14}{'Battery':<10}{'State':<14}{'Target':<8}")
    print("-" * 46)
    for s in states:
        print(f"{s.name:<14}{s.battery_level}%{'':<6}{s.charging_state:<14}{s.target_soc}%")
    return 0


def cmd_run(args, settings: Settings) -> int:
    if args.once:
        client = TeslaClient(
            client_id=settings.client_id,
            client_secret=settings.client_secret,
            region=settings.region,
            redirect_uri=settings.redirect_uri,
            token_file=settings.token_file,
        )
        run_once(client, make_coordinator(settings), settings)
        return 0
    run_loop(settings)
    return 0


def cmd_simulate(args, _settings: Settings) -> int:
    """Demo the coordinator against a fake two-car fleet over virtual time."""
    vehicles = [
        FakeVehicle(vin="SIM1", name="Model Y", soc=args.soc1, plugged_in=True),
        FakeVehicle(vin="SIM2", name="Model 3", soc=args.soc2, plugged_in=True),
    ]
    settings = Settings(
        vehicles=[],
        target_soc=args.target,
        offpeak_start=dtime(22, 0),
        offpeak_end=dtime(6, 0),
    )
    from .config import VehicleConfig
    settings.vehicles = [
        VehicleConfig(name="Model Y", vin="SIM1", priority=0),
        VehicleConfig(name="Model 3", vin="SIM2", priority=1),
    ]
    coordinator = make_coordinator(settings)
    start = datetime(2026, 10, 8, 22, 0)
    report = run_simulation(vehicles, coordinator, settings, start, hours=args.hours)

    print(f"\nSimulating {args.hours}h from 22:00 — Model Y starts at {args.soc1}%, "
          f"Model 3 at {args.soc2}%, target {args.target}% (off-peak 22:00–06:00)\n")
    print(f"{'Time':<8}{'Charging':<16}{'Model Y':<10}{'Model 3':<10}Note")
    print("-" * 80)
    last_charging: list[str] = []
    for tick in report["timeline"]:
        if tick["charging"] != last_charging:  # print only on change
            print(f"{tick['time']:<8}{', '.join(tick['charging']) or '—':<16}"
                  f"{tick['socs']['Model Y']:<10}{tick['socs']['Model 3']:<10}{tick['note']}")
            last_charging = tick["charging"]
    print("\nResult:")
    print(f"  Max vehicles charging at once: {report['max_concurrent_charging']} "
          f"({'PASS — never simultaneous' if report['max_concurrent_charging'] <= 1 else 'FAIL'})")
    for name, soc in report["final_soc"].items():
        print(f"  {name}: {soc}% (target {args.target}%)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tcc",
        description="Tesla Charge Coordinator — never charge two Teslas at once.",
    )
    p.add_argument("--version", action="version", version=f"tcc {__version__}")
    p.add_argument("--env-file", default=None, help="Path to .env file (default: ./.env)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("auth", help="Authorize with your Tesla account (one-time)")
    sub.add_parser("pairing", help="Show virtual-key pairing instructions")
    sub.add_parser("status", help="Show battery/charging state of all vehicles")
    run_p = sub.add_parser("run", help="Run the coordination loop (daemon)")
    run_p.add_argument("--once", action="store_true", help="Single pass, then exit")
    sim_p = sub.add_parser("simulate", help="Demo against a fake fleet (no API calls)")
    sim_p.add_argument("--soc1", type=float, default=20.0)
    sim_p.add_argument("--soc2", type=float, default=55.0)
    sim_p.add_argument("--target", type=int, default=80)
    sim_p.add_argument("--hours", type=float, default=12.0)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    load_dotenv(args.env_file)  # loads ./.env by default; never committed
    try:
        settings = load_settings(require_auth=args.command in ("auth", "status", "run"))
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    commands = {
        "auth": cmd_auth,
        "pairing": cmd_pairing,
        "status": cmd_status,
        "run": cmd_run,
        "simulate": cmd_simulate,
    }
    return commands[args.command](args, settings)


if __name__ == "__main__":
    raise SystemExit(main())
