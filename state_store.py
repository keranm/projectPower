import json
from datetime import datetime
from pathlib import Path

_DIR = Path(__file__).parent
STATE_FILE = _DIR / "state.json"
OVERRIDE_FILE = _DIR / "override.json"


def write_state(state, prices, decision, weather=None) -> None:
    data = {
        "updated": datetime.now().isoformat(),
        "inverter": {
            "soc": state.soc,
            "ppv": state.ppv,
            "pac": state.pac,
            "pcharge1": state.pcharge1,
            "pdischarge1": state.pdischarge1,
            "plocal_load": state.plocal_load,
            "status_text": state.status_text,
            "bms_soh": state.bms_soh,
            "priority": state.priority,
            "ac_charge_enabled": state.ac_charge_enabled,
            "export_limit_pct": state.export_limit_pct,
            "epv_today":      state.epv_today,
            "eload_today":    state.eload_today,
            "eimport_today":  state.eimport_today,
            "eexport_today":  state.eexport_today,
            "echarge_today":  state.echarge_today,
            "edischarge_today": state.edischarge_today,
        },
        "prices": [
            {
                "channel": p.channel,
                "descriptor": p.descriptor,
                "per_kwh": p.per_kwh,
                "start_time": p.start_time.isoformat(),
                "end_time": p.end_time.isoformat(),
                "is_forecast": p.is_forecast,
            }
            for p in prices
        ],
        "decision": {
            "action": decision.action,
            "reason": decision.reason,
            "priority": decision.priority,
        },
        "weather": {
            "place_name":       weather.place_name,
            "temperature":      weather.temperature,
            "feels_like":       weather.feels_like,
            "weather_code":     weather.weather_code,
            "weather_desc":     weather.weather_desc,
            "cloud_cover":      weather.cloud_cover,
            "solar_radiation":  weather.solar_radiation,
            "is_sunny":         weather.is_sunny,
            "trend":            weather.trend,
            "hourly_codes":     weather.hourly_codes,
            "hourly_clouds":    weather.hourly_clouds,
            "hourly_radiation": weather.hourly_radiation,
            "hourly_times":     weather.hourly_times,
        } if weather else None,
    }
    STATE_FILE.write_text(json.dumps(data, indent=2))


def read_state() -> dict | None:
    if not STATE_FILE.exists():
        return None
    return json.loads(STATE_FILE.read_text())


def write_override(action: str, expires_iso: str | None = None) -> None:
    OVERRIDE_FILE.write_text(json.dumps({
        "action": action,
        "set_at": datetime.now().isoformat(),
        "expires": expires_iso,
    }))


def read_override() -> dict | None:
    if not OVERRIDE_FILE.exists():
        return None
    data = json.loads(OVERRIDE_FILE.read_text())
    if data.get("expires") and datetime.fromisoformat(data["expires"]) < datetime.now():
        OVERRIDE_FILE.unlink()
        return None
    return data


def clear_override() -> None:
    if OVERRIDE_FILE.exists():
        OVERRIDE_FILE.unlink()
