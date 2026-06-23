from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import List

from amber_client import HIGH_DESCRIPTORS, SPIKE_DESCRIPTORS, PriceInterval
from config import cfg
from growatt_client import InverterState, PRIORITY_BATTERY, PRIORITY_GRID, PRIORITY_LOAD


@dataclass
class Decision:
    action: str    # 'none' | 'set_load_first' | 'set_battery_first' | 'set_grid_first' | 'enable_ac_charge'
    reason: str
    priority: int  # 1-4 matching brief priorities; 0 = default


def _parse_time(t: str) -> time:
    h, m = t.split(":")
    return time(int(h), int(m))


def _in_window(now: time, start: str, end: str) -> bool:
    return _parse_time(start) <= now < _parse_time(end)


def _hours_until(now: datetime, window_start: str) -> float:
    h, m = map(int, window_start.split(":"))
    candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return (candidate - now).total_seconds() / 3600


def evaluate(state: InverterState, prices: List[PriceInterval], weather=None) -> Decision:
    now = datetime.now()
    current_time = now.time()

    if weather:
        from logger import log
        log.info(
            "Weather: %s %.1f°C cloud=%d%% solar=%.0fW/m² sunny=%s | %s",
            weather.weather_desc, weather.temperature, weather.cloud_cover,
            weather.solar_radiation, weather.is_sunny, weather.trend,
        )

    general = [p for p in prices if p.channel == "general"]
    feedin  = [p for p in prices if p.channel == "feed_in"]

    current_price = general[0] if general else None
    current_feedin = feedin[0] if feedin else None
    current_descriptor = current_price.descriptor if current_price else "neutral"
    forecast_descriptors = {p.descriptor for p in general[1:]}

    # --- Priority 1: Spike protection ---
    is_spike = current_descriptor in SPIKE_DESCRIPTORS
    forecast_spike = bool(forecast_descriptors & SPIKE_DESCRIPTORS)

    if is_spike or forecast_spike:
        feedin_str = f", sell {current_feedin.per_kwh:.0f}c" if current_feedin else ""
        reason = (
            f"price spike {'active' if is_spike else 'forecast'} "
            f"(buy {current_price.per_kwh:.0f} c/kWh{feedin_str})" if current_price else "spike detected"
        )
        return Decision(action="set_grid_first", reason=reason, priority=1)

    # --- Priority 2: Protect heating windows ---
    heating_windows = [
        ("morning", cfg.morning_heating_start, cfg.morning_soc_target),
        ("evening", cfg.evening_heating_start, cfg.evening_soc_target),
    ]
    for label, window_start, soc_target in heating_windows:
        hours_away = _hours_until(now, window_start)
        if 0 < hours_away <= cfg.precharge_lead_hours and state.soc < soc_target:
            if current_price and current_price.per_kwh <= cfg.precharge_max_price:
                return Decision(
                    action="enable_ac_charge",
                    reason=(
                        f"{label} heating starts in {hours_away:.1f}h, "
                        f"SOC {state.soc}% < target {soc_target}%, "
                        f"price {current_price.per_kwh:.1f} c/kWh <= {cfg.precharge_max_price:.0f}c threshold"
                    ),
                    priority=2,
                )

    # --- Priority 3: Cheap grid charging ---
    if current_price and current_price.per_kwh <= cfg.grid_charge_max_price and state.soc < cfg.cheap_charge_soc_max:
        return Decision(
            action="set_battery_first",
            reason=f"grid price {current_price.per_kwh:.1f} c/kWh <= {cfg.grid_charge_max_price:.0f}c threshold, SOC {state.soc}%",
            priority=3,
        )

    # --- Priority 4: Export for profit ---
    # Never export when sell price is negative — we'd pay to push power into the grid
    feedin_positive = not current_feedin or current_feedin.per_kwh >= 0
    # Don't export on sell price alone if high prices are forecast — hold battery for self-consumption
    forecast_high = bool(forecast_descriptors & HIGH_DESCRIPTORS)
    feedin_attractive = (current_feedin and current_feedin.per_kwh >= cfg.export_feedin_min
                         and not forecast_high)
    can_export = (current_descriptor in HIGH_DESCRIPTORS and feedin_positive) or feedin_attractive
    if can_export and state.soc > cfg.export_soc_min:
        fi_str = f", sell {current_feedin.per_kwh:.0f}c" if current_feedin else ""
        return Decision(
            action="set_grid_first",
            reason=f"high price ({current_descriptor}, buy {current_price.per_kwh:.0f} c/kWh{fi_str}), SOC {state.soc}%",
            priority=4,
        )

    # --- Priority 5: High price, SOC below export floor --- hold battery for self-consumption
    # SOC is too low to export, but prices are still high — stay in Battery First so the
    # battery powers the house rather than importing from the grid at peak rates.
    if current_descriptor in HIGH_DESCRIPTORS and feedin_positive:
        return Decision(
            action="set_battery_first",
            reason=f"high price (buy {current_price.per_kwh:.0f}c) but SOC {state.soc}% at/below export floor — conserving battery",
            priority=0,
        )

    # --- Default: preserve solar window, otherwise load first ---
    if _in_window(current_time, cfg.solar_window_start, cfg.solar_window_end):
        return Decision(
            action="none",
            reason=f"solar window {cfg.solar_window_start}-{cfg.solar_window_end}, preserving Battery First",
            priority=0,
        )

    return Decision(action="set_load_first", reason="no active condition", priority=0)
