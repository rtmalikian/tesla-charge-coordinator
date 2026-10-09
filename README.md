# Tesla Charge Coordinator — Smart Multi-Tesla Charging (Never Charge Two Teslas at Once)

**Open-source app that coordinates charging across multiple Tesla vehicles so they never charge simultaneously.** Built on the official [Tesla Fleet API](https://developer.tesla.com/docs/fleet-api) — sequential smart charging, off-peak scheduling, and priority-based handoff for multi-Tesla households.

If you own two (or more) Teslas — Model 3, Model Y, Model S, Model X, or Cybertruck — and you want them to take turns charging instead of pulling power at the same time, this is for you.

## Why?

- **Avoid overloading your home electrical panel** when two EVs charge at once
- **Stay inside off-peak / time-of-use (TOU) electricity windows** automatically
- **Stop babysitting charge schedules** — the app hands the charge session from one car to the next the moment the first finishes, instead of waiting for a fixed timer
- **Free for personal use** — Tesla's Fleet API includes a $10/month developer credit that covers a two-car coordination app many times over

## How it works

```
22:00  Model Y (20%) starts charging  ─┐
       Model 3 (55%) waits             │  only ONE car
02:20  Model Y hits 80% → session     │  charges at
       handed to Model 3               │  any moment
04:10  Model 3 hits 80% → all done    ─┘
```

Every few minutes the coordinator:

1. Reads each car's battery level and charging state via the Fleet API
2. Picks the highest-priority car that still needs charge
3. Sends `charge_stop` to any car that shouldn't be charging and `charge_start` to the winner
4. Repeats — so a car that finishes early immediately hands off to the next one

Smart behaviors included:

- **Priority queue** — list cars in priority order; the daily driver charges first
- **Off-peak window** — optionally restrict all charging to cheap-electricity hours (e.g. 22:00–06:00)
- **Manual override respect** — if you start charging from the Tesla app or the car's screen, the coordinator backs off instead of fighting you
- **Unplugged cars are skipped** — a car that isn't plugged in never blocks the others
- **Dry-run mode** — log exactly what *would* happen without sending any commands

## Quickstart

```bash
git clone https://github.com/rtmalikian/tesla-charge-coordinator.git
cd tesla-charge-coordinator
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 1. Configure (your secrets stay local — .env is git-ignored)
cp .env.example .env
nano .env   # add TESLA_CLIENT_ID, TESLA_CLIENT_SECRET, your VINs

# 2. Authorize with your Tesla account (one-time)
tcc auth

# 3. Install the virtual key on each car (one-time, see below)
tcc pairing

# 4. Try it safely first — simulate a full night with fake cars
tcc simulate

# 5. Single safe pass against your real cars (dry-run recommended first)
TCC_DRY_RUN=true tcc run --once

# 6. Run the daemon
tcc run
```

## Tesla developer setup (one-time, ~15 minutes)

**1. Register a developer app** at [developer.tesla.com](https://developer.tesla.com) and note your
Client ID and Client Secret. Request these scopes:

- `vehicle_device_data` — read battery / charging state
- `vehicle_cmds` — wake and command vehicles
- `vehicle_charging_cmds` — start/stop charging

**2. Run `tcc auth`** and sign in with the Tesla account that owns the cars.
Tokens are stored at `~/.tcc/tokens.json` (mode 600) and refreshed automatically.

**3. Install the app's virtual key on each car** (`tcc pairing` prints the steps):

```bash
# Generate a keypair and host the public key on your domain:
openssl ecparam -genkey -name prime256v1 -noout | openssl ec -out private-key.pem
openssl ec -in private-key.pem -pubout | openssl pkey -pubin -outform DER | \
  openssl base64 -A > public-key.txt
# Serve public-key.txt at https://<your-domain>/.well-known/appspecific/com.tesla.3p.public-key.pem
```

Then register the key (`POST /api/1/partner_accounts {"domain": "<your-domain>"}`) and,
in each Tesla, visit `https://www.tesla.com/_ak/<your-domain>` and approve it on the
touchscreen. Until the key is approved, `charge_start` / `charge_stop` are rejected.

## Configuration (`.env` reference)

| Variable | Required | Default | What it does |
|---|---|---|---|
| `TESLA_CLIENT_ID` / `TESLA_CLIENT_SECRET` | yes | — | From developer.tesla.com |
| `TESLA_REGION` | no | `na` | `na`, `eu`, or `cn` |
| `TCC_VEHICLES` | yes | — | `name=VIN:priority,...` — lower priority charges first |
| `TCC_TARGET_SOC` | no | `80` | Target battery % for every car |
| `TCC_POLL_INTERVAL_SECONDS` | no | `300` | How often to check the fleet |
| `TCC_OFFPEAK_START` / `TCC_OFFPEAK_END` | no | — | `HH:MM` cheap-electricity window |
| `TCC_RESPECT_MANUAL_OVERRIDE` | no | `true` | Back off if you start charging manually |
| `TCC_DRY_RUN` | no | `false` | Log actions without sending commands |

Find a VIN in the Tesla app under *Controls → Software*, or via `tcc status` after auth.

## Try before you connect: `tcc simulate`

No Tesla account needed. Simulates a full night with two virtual cars and prints the
handoff timeline, verifying the core guarantee — **max one car charging at any moment**:

```
22:00   Model Y         20.0      55.0      Charge session handed to Model Y
02:20   Model 3         81.1      55.0      Charge session handed to Model 3
04:10   —               81.1      80.9      No vehicles need charging

Max vehicles charging at once: 1 (PASS — never simultaneous)
```

## FAQ

**How is this different from Tesla Wall Connector load sharing?**
Wall Connector power sharing splits available current between cars *simultaneously* — both
charge at reduced speed. This app enforces *strictly sequential* charging: one car at full
speed, then the next. Pick whichever fits your panel and your utility rates.

**How many cars are supported?**
Any number — the coordinator generalizes the priority queue to N vehicles.

**What does it cost to run?**
Tesla's Fleet API is pay-per-use, but every developer account gets a $10/month credit.
Tesla's own estimate: that covers data streaming, ~100 commands, and 2 wakes/day for two
vehicles — this app's usage fits comfortably inside it.

**Does it work with solar / Powerwall?**
Not yet — the coordinator currently optimizes for panel capacity and off-peak rates.
Solar-aware charging (charge the car with excess solar) is a planned feature; PRs welcome.

**Is my Tesla account safe?**
Tokens are stored locally with file mode 600 and never leave your machine except to
Tesla's own API. The `.env` file holding your API keys is git-ignored by default.

## Development

```bash
pip install pytest
PYTHONPATH=src python -m pytest tests/ -q   # 21 tests, incl. full-fleet simulation
```

## License

MIT — see [LICENSE](LICENSE).
