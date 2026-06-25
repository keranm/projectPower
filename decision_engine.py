from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import List

from amber_client import HIGH_DESCRIPTORS, SPIKE_DESCRIPTORS, PriceInterval
from config import cfg
from growatt_client import InverterState


@dataclass
class Decision:
    action: str       # 'none' | 'set_load_first' | 'set_battery_first' | 'set_grid_first' | 'enable_ac_charge'
    reason: str
    priority: int     # 1-4 matching brief priorities; 0 = default
    target_soc: int = 0  # SOC% to charge to; only used when action=enable_ac_charge


def _parse_time(t: str) -> time:
    h, m = t.split(":")
    return time(int(h), int(m))


def _in_window(now: time, start: str, end: str) -> bool:
    return _parse_time(start) <= now < _parse_time(end)


def _hours_until(now: datetime, window_start: str) -> float:
    """Hours until the next occurrence of a daily time (e.g. '06:40')."""
    h, m = map(int, window_start.split(":"))
    candidate = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return (candidate - now).total_seconds() / 3600


def _forecast_peak_buy(general: List[PriceInterval], hours: float) -> float:
    """Highest buy price in the next N hours (N × 12 five-minute forecast intervals)."""
    n = int(hours * 12)
    candidates = [p.per_kwh for p in general[1:n + 1]]
    return max(candidates) if candidates else 0.0


def _heating_reserve_soc(cfg) -> float:
    """SOC% consumed by one full heating window (heater + baseline load) from battery alone."""
    wh_needed = (cfg.heating_load_w + cfg.baseline_load_w) * cfg.heating_duration_hours
    return (wh_needed / (cfg.battery_capacity_kwh * 1000)) * 100


def _estimated_soc_at_heating(state: InverterState, cfg, hours_until: float) -> float:
    """Projected SOC at heating window start, assuming baseline drain only (no active loads)."""
    drain_pct = (cfg.baseline_load_w * hours_until / (cfg.battery_capacity_kwh * 1000)) * 100
    return max(0.0, state.soc - drain_pct)


def _solar_will_refill(weather, window_start: str) -> bool:
    """True if solar window (10-16) precedes the heating window and solar is forecast strong.
    Morning heating (06:40) is before solar — solar can't pre-fill the battery before heating.
    Evening heating (17:00) is after solar — check if radiation forecast is good.
    """
    if not weather or not weather.hourly_radiation:
        return False
    if _parse_time(window_start) <= _parse_time(cfg.solar_window_end):
        return False  # Heating before or at end of solar window — solar won't pre-fill
    return weather.is_sunny and any(r > 200 for r in weather.hourly_radiation)


def evaluate(state: InverterState, prices: List[PriceInterval], weather=None) -> Decision:
    now = datetime.now()
    current_time = now.time()

    general = [p for p in prices if p.channel == "general"]
    feedin  = [p for p in prices if p.channel == "feed_in"]

    current_price      = general[0] if general else None
    current_feedin     = feedin[0] if feedin else None
    current_descriptor = current_price.descriptor if current_price else "neutral"

    # Spike check over all 12h of forecast; high-price check limited to 6h window
    forecast_spike = bool({p.descriptor for p in general[1:]} & SPIKE_DESCRIPTORS)
    forecast_peak_6h = _forecast_peak_buy(general, cfg.forecast_horizon_hours)
    # Floor forecast_peak so sparse data doesn't make the future look cheaper than present
    if current_price:
        forecast_peak_6h = max(forecast_peak_6h, current_price.per_kwh)
    forecast_high_6h = forecast_peak_6h > 60.0  # "high" territory in SA wholesale market

    # ── Lookahead preamble ───────────────────────────────────────────────────────
    heating_active = bool(weather and weather.temperature < cfg.heating_temp_threshold)
    hours_until_morning = _hours_until(now, cfg.morning_heating_start)

    # Dynamic SOC floor: overnight (heating within 8h, no solar), raise floor to reserve for heating
    floor_elevated = (
        heating_active
        and hours_until_morning < 8
        and not _in_window(current_time, cfg.solar_window_start, cfg.solar_window_end)
    )
    dynamic_floor = (
        max(cfg.export_soc_min, _heating_reserve_soc(cfg)) if floor_elevated else cfg.export_soc_min
    )
    # What 1 kWh of battery is worth if held for self-consumption (avoids future import at peak price)
    future_value = forecast_peak_6h * cfg.battery_efficiency

    # ── Priority 1: Spike protection ─────────────────────────────────────────────
    is_spike = current_descriptor in SPIKE_DESCRIPTORS
    if is_spike or forecast_spike:
        feedin_str = f", sell {current_feedin.per_kwh:.0f}c" if current_feedin else ""
        buy_str = f"buy {current_price.per_kwh:.0f} c/kWh" if current_price else ""
        return Decision(
            action="set_grid_first",
            reason=f"price spike {'active' if is_spike else 'forecast'} ({buy_str}{feedin_str})",
            priority=1,
        )

    # ── Priority 2: Negative feed-in — do not export ─────────────────────────────
    if current_feedin and current_feedin.per_kwh < 0:
        return Decision(
            action="none",
            reason=f"negative feed-in {current_feedin.per_kwh:.1f}c — not exporting",
            priority=0,
        )

    # ── Priority 3: Pre-charge for heating windows ───────────────────────────────
    if heating_active and current_price:
        for label, window_start, soc_target in [
            ("morning", cfg.morning_heating_start, cfg.morning_soc_target),
            ("evening", cfg.evening_heating_start, cfg.evening_soc_target),
        ]:
            hours_away = _hours_until(now, window_start)
            if hours_away > cfg.precharge_lead_hours:
                continue
            est_soc = _estimated_soc_at_heating(state, cfg, hours_away)
            drain_pct = (cfg.baseline_load_w * hours_away / (cfg.battery_capacity_kwh * 1000)) * 100
            charge_target = min(90, int(soc_target + drain_pct))
            if (
                est_soc < soc_target
                and not _solar_will_refill(weather, window_start)
                and current_price.per_kwh <= cfg.precharge_max_price
                and current_price.per_kwh * cfg.precharge_price_ratio < forecast_peak_6h
            ):
                return Decision(
                    action="enable_ac_charge",
                    reason=(
                        f"{label} heating in {hours_away:.1f}h, est. SOC {est_soc:.0f}% < {soc_target}% target, "
                        f"charging to {charge_target}% (target + {drain_pct:.0f}% drain) "
                        f"at {current_price.per_kwh:.0f}c (forecast peak {forecast_peak_6h:.0f}c)"
                    ),
                    priority=2,
                    target_soc=charge_target,
                )

    # ── Priority 4: Opportunistic cheap grid charge ───────────────────────────────
    if current_price and current_price.per_kwh <= cfg.grid_charge_max_price and state.soc < cfg.cheap_charge_soc_max:
        return Decision(
            action="set_battery_first",
            reason=f"grid price {current_price.per_kwh:.1f}c <= {cfg.grid_charge_max_price:.0f}c threshold, SOC {state.soc}%",
            priority=3,
        )

    # ── Priority 5: Dispatch for profit ──────────────────────────────────────────
    feedin_positive = not current_feedin or current_feedin.per_kwh >= 0
    # sell_profitable: sell now returns more than holding 1 kWh to avoid future import
    sell_profitable = bool(
        current_feedin and feedin_positive and current_feedin.per_kwh > future_value
    )
    # sell_attractive: sell meets minimum threshold and no high prices forecast in 6h window
    sell_attractive = bool(
        current_feedin and feedin_positive
        and current_feedin.per_kwh >= cfg.export_feedin_min
        and not forecast_high_6h
    )
    if (sell_profitable or sell_attractive) and state.soc > dynamic_floor:
        if sell_profitable:
            reason = (
                f"dispatch: sell {current_feedin.per_kwh:.0f}c > future value {future_value:.0f}c "
                f"(forecast {forecast_peak_6h:.0f}c × {cfg.battery_efficiency:.2f}), SOC {state.soc}%"
            )
        else:
            reason = (
                f"dispatch: sell {current_feedin.per_kwh:.0f}c attractive, "
                f"no high prices in 6h forecast, SOC {state.soc}%"
            )
        return Decision(action="set_grid_first", reason=reason, priority=4)

    # ── Priority 6: Preserve — high prices, hold battery for self-consumption ────
    prices_high_now_or_soon = current_descriptor in HIGH_DESCRIPTORS or forecast_high_6h
    if prices_high_now_or_soon and feedin_positive:
        descriptor_str = (
            f"current {current_descriptor}" if current_descriptor in HIGH_DESCRIPTORS
            else f"forecast peak {forecast_peak_6h:.0f}c"
        )
        return Decision(
            action="set_battery_first",
            reason=(
                f"preserve: {descriptor_str}, future value {future_value:.0f}c, "
                f"SOC {state.soc}% (floor {dynamic_floor:.0f}%)"
            ),
            priority=0,
        )

    # ── Solar window: do nothing, Battery First is already preserving charge ──────
    if _in_window(current_time, cfg.solar_window_start, cfg.solar_window_end):
        return Decision(
            action="none",
            reason=f"solar window {cfg.solar_window_start}-{cfg.solar_window_end}, preserving Battery First",
            priority=0,
        )

    return Decision(action="set_load_first", reason="no active condition", priority=0)
