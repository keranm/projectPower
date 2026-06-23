from dataclasses import dataclass
from datetime import datetime, time as dt_time, timedelta

import growattServer

from config import cfg
from logger import log


@dataclass
class InverterState:
    soc: int
    ppv: float           # solar watts (ppv1 + ppv2 total)
    pac: float           # inverter AC output watts (NOT grid — use pac_to_grid/pac_to_user)
    pac_to_grid: float   # watts flowing TO grid (positive = exporting)
    pac_to_user: float   # watts flowing FROM grid to user (positive = importing)
    pcharge1: float      # battery charging watts
    pdischarge1: float   # battery discharging watts
    plocal_load: float   # house consumption watts — unreliable (0) on this firmware; use derived
    status_text: str
    bms_soh: int
    priority: int        # 0=load first, 1=battery first, 2=grid first
    ac_charge_enabled: bool
    export_limit_pct: int
    # Read from sph_detail directly — sph_read_ac_discharge_times() has a library bug on this model
    discharge_start: str
    discharge_stop: str
    discharge_enabled: bool
    # Daily totals from sph_energy
    epv_today: float
    eload_today: float
    eimport_today: float
    eexport_today: float
    echarge_today: float
    edischarge_today: float


_EMPTY_PERIODS = [
    {"start_time": dt_time(0, 0), "end_time": dt_time(0, 0), "enabled": False},
    {"start_time": dt_time(0, 0), "end_time": dt_time(0, 0), "enabled": False},
    {"start_time": dt_time(0, 0), "end_time": dt_time(0, 0), "enabled": False},
]


def _tou_window(minutes: int = 12):
    """Return (start, end) as datetime.time for now → now+minutes. Caps at 23:59 on midnight rollover."""
    now = datetime.now()
    end_dt = now + timedelta(minutes=minutes)
    start = dt_time(now.hour, now.minute)
    end = dt_time(23, 59) if end_dt.date() > now.date() else dt_time(end_dt.hour, end_dt.minute)
    return start, end


class GrowattClient:
    def __init__(self):
        self._api = growattServer.OpenApiV1(token=cfg.growatt_token)
        self._api.server_url = cfg.growatt_server
        self._api.api_url = cfg.growatt_server + "v1/"
        self._serial = cfg.sph_serial
        self._tou_state = None  # None = unknown (post-startup) | "clear" | "dispatch" | "charge"

    def get_state(self) -> InverterState:
        detail = self._api.sph_detail(self._serial)
        energy = self._api.sph_energy(self._serial)
        return InverterState(
            soc=int(float(energy.get("soc", 0))),
            ppv=float(energy.get("ppv", 0)),
            pac=float(energy.get("pac", 0)),
            pac_to_grid=float(energy.get("pacToGridTotal", 0)),
            pac_to_user=float(energy.get("pacToUserTotal", 0)),
            pcharge1=float(energy.get("pcharge1", 0)),
            pdischarge1=float(energy.get("pdischarge1", 0)),
            plocal_load=float(energy.get("plocalLoadTotal", 0)),
            status_text=energy.get("statusText", ""),
            bms_soh=int(float(energy.get("bmsSOH", 0))),
            priority=int(detail.get("priorityChoose", 0)),
            ac_charge_enabled=str(detail.get("acChargeEnable", "0")) == "1",
            export_limit_pct=int(detail.get("exportLimitPowerRate", 25)),
            discharge_start=detail.get("forcedDischargeTimeStart1", "00:00"),
            discharge_stop=detail.get("forcedDischargeTimeStop1", "00:00"),
            discharge_enabled=str(detail.get("forcedDischargeStopSwitch1", "0")) == "1",
            epv_today=float(energy.get("epvtoday", 0)),
            eload_today=float(energy.get("elocalLoadToday", 0)),
            eimport_today=float(energy.get("etoUserToday", 0)),
            eexport_today=float(energy.get("etoGridToday", 0)),
            echarge_today=float(energy.get("echarge1Today", 0)),
            edischarge_today=float(energy.get("edischarge1Today", 0)),
        )

    def set_tou_dispatch(self, stop_soc: int = 40, power_pct: int = 100) -> None:
        """Enable battery→grid discharge for the next poll window (~12 min)."""
        start, end = _tou_window(minutes=12)
        periods = [
            {"start_time": start, "end_time": end, "enabled": True},
            _EMPTY_PERIODS[1], _EMPTY_PERIODS[2],
        ]
        result = self._api.sph_write_ac_discharge_times(
            self._serial, power_pct, stop_soc, periods
        )
        log.info("TOU dispatch %s–%s stop_soc=%d%% response=%s", start, end, stop_soc, result)
        if self._tou_state == "charge":
            self._api.sph_write_ac_charge_times(self._serial, 100, 100, False, _EMPTY_PERIODS)
            log.info("TOU charge window cleared (switching to dispatch)")
        self._tou_state = "dispatch"

    def set_tou_charge(self, stop_soc: int = 60, power_pct: int = 100) -> None:
        """Enable grid→battery charge for the next poll window (~12 min)."""
        start, end = _tou_window(minutes=12)
        periods = [
            {"start_time": start, "end_time": end, "enabled": True},
            _EMPTY_PERIODS[1], _EMPTY_PERIODS[2],
        ]
        result = self._api.sph_write_ac_charge_times(
            self._serial, power_pct, stop_soc, True, periods
        )
        log.info("TOU charge %s–%s stop_soc=%d%% response=%s", start, end, stop_soc, result)
        if self._tou_state == "dispatch":
            self._api.sph_write_ac_discharge_times(self._serial, 100, 10, _EMPTY_PERIODS)
            log.info("TOU discharge window cleared (switching to charge)")
        self._tou_state = "charge"

    def clear_tou(self) -> None:
        """Disable all TOU windows — inverter operates in Battery First base mode."""
        if self._tou_state == "clear":
            return  # Already confirmed clear, skip API calls
        if self._tou_state is None:
            # Unknown state post-startup — clear both tables in case anything is active
            r1 = self._api.sph_write_ac_discharge_times(self._serial, 100, 10, _EMPTY_PERIODS)
            r2 = self._api.sph_write_ac_charge_times(self._serial, 100, 100, False, _EMPTY_PERIODS)
            log.info("TOU cleared (prior state unknown) discharge=%s charge=%s", r1, r2)
        elif self._tou_state == "dispatch":
            result = self._api.sph_write_ac_discharge_times(self._serial, 100, 10, _EMPTY_PERIODS)
            log.info("TOU dispatch cleared response=%s", result)
        elif self._tou_state == "charge":
            result = self._api.sph_write_ac_charge_times(self._serial, 100, 100, False, _EMPTY_PERIODS)
            log.info("TOU charge cleared response=%s", result)
        self._tou_state = "clear"
