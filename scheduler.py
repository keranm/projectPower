import time
import sys

from amber_client import AmberClient
from config import cfg
from decision_engine import evaluate, Decision
from growatt_client import GrowattClient, PRIORITY_BATTERY, PRIORITY_GRID, PRIORITY_LOAD
from logger import log
from weather_client import WeatherClient
import history
import state_store


def apply_decision(decision: Decision, growatt: GrowattClient) -> None:
    action = decision.action

    if action == "none":
        return

    if cfg.dry_run:
        log.info("[DRY-RUN] Would execute: %s", action)
        return

    if action == "set_load_first":
        growatt.set_priority(PRIORITY_LOAD)
    elif action == "set_battery_first":
        growatt.set_priority(PRIORITY_BATTERY)
    elif action == "set_grid_first":
        growatt.set_priority(PRIORITY_GRID)
    elif action == "enable_ac_charge":
        # TODO: calculate appropriate charge window start/stop before implementing
        log.warning("enable_ac_charge action reached — window times not yet implemented")
    else:
        log.warning("Unknown action: %s", action)


def run_once(growatt: GrowattClient, amber: AmberClient, weather_client: WeatherClient) -> None:
    try:
        state = growatt.get_state()
        log.info(
            "Inverter: SOC=%d%% ppv=%.0fW pac=%.0fW priority=%d ac_charge=%s status=%s",
            state.soc, state.ppv, state.pac, state.priority,
            state.ac_charge_enabled, state.status_text,
        )
    except Exception as e:
        log.error("Growatt read failed: %s", e)
        return

    try:
        prices = amber.get_prices()
        if prices:
            general = [p for p in prices if p.channel == "general"]
            feedin  = [p for p in prices if p.channel == "feed_in"]
            log.info(
                "Amber: buy=%s %.0f c/kWh sell=%.0f c/kWh, forecast=%s",
                general[0].descriptor if general else "?",
                general[0].per_kwh if general else 0,
                feedin[0].per_kwh if feedin else 0,
                [p.descriptor for p in general[1:4]],
            )
    except Exception as e:
        log.error("Amber read failed — skipping decision cycle: %s", e)
        return

    weather = None
    if cfg.address:
        try:
            weather = weather_client.get_weather()
        except Exception as e:
            log.warning("Weather fetch failed (non-fatal): %s", e)

    decision = evaluate(state, prices, weather)
    log.info(
        "Decision: action=%s priority=%d reason=%s",
        decision.action, decision.priority, decision.reason,
    )

    state_store.write_state(state, prices, decision, weather)
    history.write_reading(state, prices, weather)

    override = state_store.read_override()
    was_override = override is not None
    if was_override:
        log.info("Manual override active: %s (expires: %s)", override["action"], override.get("expires", "never"))
        apply_decision(
            Decision(action=override["action"], reason="manual override", priority=-1),
            growatt,
        )
    else:
        apply_decision(decision, growatt)

    history.write_decision(decision, was_override=was_override)


def main() -> None:
    log.info("McNutty Energy Manager starting — dry_run=%s poll_interval=%ds", cfg.dry_run, cfg.poll_interval)
    if cfg.dry_run:
        log.info("DRY-RUN mode active: no writes will be sent to Growatt API")

    history.init_db()
    growatt = GrowattClient()
    amber = AmberClient()
    weather_client = WeatherClient()

    try:
        while True:
            run_once(growatt, amber, weather_client)
            time.sleep(cfg.poll_interval)
    except KeyboardInterrupt:
        log.info("Shutting down")
    finally:
        amber.close()


if __name__ == "__main__":
    main()
