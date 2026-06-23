from dataclasses import dataclass

import growattServer

from config import cfg
from logger import log

PRIORITY_LOAD = 0
PRIORITY_BATTERY = 1
PRIORITY_GRID = 2


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


class GrowattClient:
    def __init__(self):
        self._api = growattServer.OpenApiV1(token=cfg.growatt_token)
        self._api.server_url = cfg.growatt_server
        self._api.api_url = cfg.growatt_server + "v1/"
        self._serial = cfg.sph_serial

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

    def set_priority(self, priority: int) -> None:
        # TODO: confirm the correct OpenApiV1 method for setting priorityChoose.
        # Candidates: check growattServer source for sph_set_priority or similar.
        # The TOU schedule write calls below may be sufficient for controlling behaviour.
        raise NotImplementedError(
            "set_priority write call not yet confirmed — check growattServer OpenApiV1 source"
        )

    def set_ac_charge_times(
        self,
        start1: str, stop1: str, enabled1: bool,
        start2: str = "00:00", stop2: str = "00:00", enabled2: bool = False,
        start3: str = "00:00", stop3: str = "00:00", enabled3: bool = False,
    ) -> None:
        # TODO: verify exact parameter order against growattServer source before first live use
        self._api.sph_write_ac_charge_times(
            self._serial,
            start1, stop1, "1" if enabled1 else "0",
            start2, stop2, "1" if enabled2 else "0",
            start3, stop3, "1" if enabled3 else "0",
        )
        log.info("Growatt AC charge times written: %s-%s enabled=%s", start1, stop1, enabled1)

    def set_discharge_times(
        self,
        start1: str, stop1: str, enabled1: bool,
        start2: str = "00:00", stop2: str = "00:00", enabled2: bool = False,
        start3: str = "00:00", stop3: str = "00:00", enabled3: bool = False,
    ) -> None:
        # TODO: verify exact parameter order against growattServer source before first live use
        self._api.sph_write_ac_discharge_times(
            self._serial,
            start1, stop1, "1" if enabled1 else "0",
            start2, stop2, "1" if enabled2 else "0",
            start3, stop3, "1" if enabled3 else "0",
        )
        log.info("Growatt discharge times written: %s-%s enabled=%s", start1, stop1, enabled1)
