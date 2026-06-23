import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    growatt_token: str = field(default_factory=lambda: os.environ["GROWATT_TOKEN"])
    growatt_server: str = field(default_factory=lambda: os.getenv("GROWATT_SERVER", "https://openapi-au.growatt.com/"))
    sph_serial: str = field(default_factory=lambda: os.environ["GROWATT_SPH_SERIAL"])
    plant_id: str = field(default_factory=lambda: os.environ["GROWATT_PLANT_ID"])

    amber_token: str = field(default_factory=lambda: os.environ["AMBER_TOKEN"])
    address: str = field(default_factory=lambda: os.getenv("ADDRESS", ""))

    dry_run: bool = field(default_factory=lambda: os.getenv("DRY_RUN", "true").lower() == "true")
    poll_interval: int = field(default_factory=lambda: int(os.getenv("POLL_INTERVAL", "300")))

    morning_soc_target: int = field(default_factory=lambda: int(os.getenv("MORNING_SOC_TARGET", "60")))
    evening_soc_target: int = field(default_factory=lambda: int(os.getenv("EVENING_SOC_TARGET", "70")))
    precharge_lead_hours: int = field(default_factory=lambda: int(os.getenv("PRECHARGE_LEAD_HOURS", "2")))

    cheap_charge_soc_max: int = 90
    export_soc_min: int = 40
    grid_charge_max_price: float = field(default_factory=lambda: float(os.getenv("GRID_CHARGE_MAX_PRICE", "10")))
    precharge_max_price: float = field(default_factory=lambda: float(os.getenv("PRECHARGE_MAX_PRICE", "20")))
    export_feedin_min: float = field(default_factory=lambda: float(os.getenv("EXPORT_FEEDIN_MIN", "20")))

    morning_heating_start: str = "06:00"
    morning_heating_end: str = "09:00"
    evening_heating_start: str = "17:00"
    evening_heating_end: str = "21:00"

    solar_window_start: str = "10:00"
    solar_window_end: str = "16:00"

    log_dir: str = field(default_factory=lambda: os.getenv("LOG_DIR", "logs"))
    log_max_bytes: int = field(default_factory=lambda: int(os.getenv("LOG_MAX_BYTES", str(5 * 1024 * 1024))))
    log_backup_count: int = field(default_factory=lambda: int(os.getenv("LOG_BACKUP_COUNT", "5")))


cfg = Config()
