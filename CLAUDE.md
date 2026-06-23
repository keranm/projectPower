# McNutty Energy Manager — Project Context

Rules-based energy management service connecting a **Growatt SPH hybrid inverter** (15 kWh battery, 5 kW solar) to the **Amber Energy API** (real-time wholesale electricity prices) on a **Raspberry Pi 4**. Replaces Amber's SmartShift, which doesn't support Growatt hardware.

**Location:** 26 Stoneleigh Avenue, Mount Barker SA 5251 (Adelaide timezone: `Australia/Adelaide`)

---

## Environment

| | Mac (dev) | Pi (prod) |
|---|---|---|
| Path | `/Users/keran/Development/projectPower/mcnutty-energy-manager` | `/home/mcnutty/projectPower` |
| SSH | — | `ssh mcnutty@192.168.0.10` |
| Python | 3.11 (venv) | 3.13 (venv) |
| Web UI | `http://localhost:8080` | `http://192.168.0.10:8080` |

Pi hostname: `McMinecraft`. SSH key at `~/.ssh/id_ed25519` is already authorised.

---

## Running the system

Two independent processes — either run manually for dev or via systemd on the Pi.

```bash
# Scheduler (polls every 5 min, writes state.json + history.db)
source venv/bin/activate && python scheduler.py

# Web server (serves dashboard + history UI)
venv/bin/uvicorn web:app --host 0.0.0.0 --port 8080
```

**Pi systemd services** (already installed and enabled):
```bash
sudo systemctl status mcnutty-scheduler mcnutty-web
sudo journalctl -u mcnutty-scheduler -f      # follow scheduler logs
sudo systemctl restart mcnutty-scheduler     # after deploying changes
```

**Deploy changes to Pi:**
```bash
# From Mac — commit and push, then on Pi:
ssh mcnutty@192.168.0.10 "cd ~/projectPower && git pull && sudo systemctl restart mcnutty-scheduler mcnutty-web"
```

---

## Architecture

```
scheduler.py  ──→  growatt_client.py   (Growatt OpenAPI v1)
              ──→  amber_client.py     (Amber wholesale prices)
              ──→  weather_client.py   (Open-Meteo / BOM ACCESS model)
              ──→  decision_engine.py  (rules engine)
              ──→  state_store.py      (writes state.json)
              ──→  history.py          (writes history.db SQLite)

web.py        ──→  reads state.json, history.db (never writes)
              ──→  serves templates/dashboard.html + templates/history.html
              ──→  POST /override  →  writes override.json
```

State is shared via files, not in-process — independent failure domains. The scheduler and web server can restart independently.

---

## Key files

| File | Purpose |
|---|---|
| `config.py` | All config via env vars, `cfg` singleton |
| `growatt_client.py` | Growatt OpenAPI wrapper, `InverterState` dataclass |
| `amber_client.py` | Amber API wrapper, `PriceInterval` dataclass |
| `weather_client.py` | Open-Meteo weather, 20-min cache, `WeatherState` dataclass |
| `decision_engine.py` | Rules engine → `Decision(action, reason, priority)` |
| `scheduler.py` | Main loop: fetch → decide → write state → write history → apply |
| `state_store.py` | Reads/writes `state.json` and `override.json` |
| `history.py` | SQLite `readings` + `decisions` tables, auto-downsampling for queries |
| `web.py` | FastAPI, serves `/` and `/history`, `GET /api/state`, `GET /api/history`, `POST /override` |
| `templates/dashboard.html` | Live dashboard — battery, weather, power flow, price, override |
| `templates/history.html` | Chart.js history page — SOC, power, price charts + decision log |
| `static/style.css` | Compiled Tailwind CSS (generated, but committed so Pi doesn't need Node) |

**Runtime files (gitignored, never commit):**
- `.env` — secrets
- `state.json` — current inverter/price/decision snapshot
- `override.json` — active manual override
- `history.db` — SQLite history

---

## .env variables

```
GROWATT_TOKEN=...           # Growatt OpenAPI token
GROWATT_SERVER=https://openapi-au.growatt.com/
GROWATT_SPH_SERIAL=QHM0E1303B
GROWATT_PLANT_ID=2783677
AMBER_TOKEN=...             # Amber API token
ADDRESS=26 Stoneleigh Avenue, Mount Barker, 5251, South Australia
DRY_RUN=false               # Live writes via TOU segment API (verified working)
POLL_INTERVAL=300
GRID_CHARGE_MAX_PRICE=10    # c/kWh — grid charge only below this
PRECHARGE_MAX_PRICE=40      # c/kWh — heating pre-charge cap (relative ratio also applies)
```

---

## Decision engine logic (priority order)

Lookahead engine — uses 12h price forecast (`next=144`) and dynamic SOC floor.

1. **Spike protection** — current or forecast spike → `set_grid_first` (dispatch)
2. **Negative feed-in** — Amber would charge us to export → `none`
3. **Pre-charge for heating** — temp < 18°C, heating within 6h, est. SOC < target, price ≤ 40c AND < 1.5× forecast peak → `enable_ac_charge` (target_soc = morning/evening target)
4. **Cheap grid charge** — price ≤ 10c and SOC < 90% → `set_battery_first` (rarely fires in SA)
5. **Dispatch for profit** — sell_price > forecast_peak_6h × 0.9 (future value), OR sell attractive + no high prices forecast → `set_grid_first` if SOC > dynamic_floor
6. **Preserve** — prices high now or in 6h forecast → `set_battery_first` (self-consume)
7. **Solar window** (10:00–16:00) → `none`
8. **Default** → `set_load_first`

**Dynamic SOC floor**: overnight when morning heating < 8h away, floor = max(40%, heating_reserve_soc). heating_reserve_soc ≈ 52% for 3.5kW heater × 2h on 15kWh battery.

**Inverter control via TOU segments** (not mix_energy_priority which is broken):
- `set_grid_first` → `sph_write_ac_discharge_times` window for now+12min, stop_soc=40%
- `enable_ac_charge` → `sph_write_ac_charge_times` window for now+12min, stop_soc=target
- All other actions → clear TOU windows, inverter operates in Battery First base mode

---

## Critical library quirks

### growattServer==2.1.0 (PINNED — do not upgrade)
- `2.2.0` uses Python 3.12 `type X = ...` syntax, breaks on 3.11
- Init quirk: must set `api.server_url` AND `api.api_url` manually after construction
- `sph_read_ac_discharge_times()` has a bug on this model — reads discharge schedule from `sph_detail` fields directly instead
- **Write methods (`set_priority`, `set_ac_charge_times`, `set_discharge_times`) are NOT yet verified** against the real API. Signatures need confirming before enabling live writes.

### amberelectric (Amber API)
- Each interval is wrapped in a oneOf container — must use `wrapper.actual_instance`
- `channel_type` and `descriptor` are **enums** — use `.value` to get the string
- Fetches `next=36` intervals (~3 hours of 5-min forecast)

### DRY_RUN mode
- `DRY_RUN=true` in `.env` on both Mac and Pi — **do not disable** until write API calls are confirmed working and decision log has been reviewed
- All `apply_decision()` calls log `[DRY-RUN] Would execute: <action>` instead of writing to inverter

---

## Tailwind CSS

Not CDN — compiled locally and committed.

```bash
npm run build-css    # regenerate static/style.css after editing HTML
npm run watch-css    # auto-rebuild during development
```

Run on Mac only. Commit `static/style.css`. Pi does not need Node.js.

---

## History / SQLite

`history.db` has two tables:
- `readings` — one row per poll cycle (SOC, solar, load, grid, battery, price, weather)
- `decisions` — one row per cycle (action, reason, priority, was_override)

Query API: `GET /api/history?hours=24` — auto-downsamples (raw for ≤24h, 30-min avg for 7d, 4h avg for 30d).

---

## Pending work

- **Amber daily cost** — integrate Amber's usage API for actual billing cost in Today card
- **Pi disk space** — at 87% used; plan to add USB drive and move history.db + logs there
- **Phase 2: learn heating load** — replace static HEATING_LOAD_W/HEATING_DURATION_HOURS with values derived from history.db after 4+ weeks of data
- **Phase 2: summer cooling** — reverse heating reserve logic when temp > threshold (cooling load instead)
