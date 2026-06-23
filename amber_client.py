from dataclasses import dataclass
from typing import List

import amberelectric

from config import cfg

SPIKE_DESCRIPTORS = {"spike"}
HIGH_DESCRIPTORS = {"high", "spike"}
CHEAP_DESCRIPTORS = {"extremely_low", "very_low"}


@dataclass
class PriceInterval:
    channel: str          # "general" (buy) or "feed_in" (sell/export tariff)
    descriptor: str
    per_kwh: float        # cents/kWh
    start_time: object    # datetime
    end_time: object      # datetime
    is_forecast: bool


class AmberClient:
    def __init__(self):
        config = amberelectric.Configuration(access_token=cfg.amber_token)
        self._api_client = amberelectric.ApiClient(config)
        self._api = amberelectric.AmberApi(self._api_client)
        self._site_id: str | None = None

    def _get_site_id(self) -> str:
        if not self._site_id:
            sites = self._api.get_sites()
            self._site_id = sites[0].id
        return self._site_id

    def get_prices(self) -> List[PriceInterval]:
        """Returns current interval plus up to 12 hours of 5-min forecast intervals."""
        raw = self._api.get_current_prices(self._get_site_id(), next=144)  # 144 × 5-min = 12 hours
        intervals = []
        for wrapper in raw:
            r = wrapper.actual_instance
            if r is None:
                continue
            # channel_type is an enum; .value gives the string
            channel_raw = getattr(r.channel_type, "value", r.channel_type)
            # Amber SDK uses camelCase "feedIn"; normalise to snake_case internally
            channel = "feed_in" if channel_raw == "feedIn" else channel_raw
            if channel not in ("general", "feed_in"):
                continue
            descriptor = getattr(r.descriptor, "value", str(r.descriptor)).lower()
            # Amber API returns feed-in per_kwh as negative when you receive credit
            # (it's an outflow from Amber's perspective). Normalise so positive = credit.
            raw_kwh = float(r.per_kwh)
            per_kwh = -raw_kwh if channel == "feed_in" else raw_kwh
            intervals.append(PriceInterval(
                channel=channel,
                descriptor=descriptor,
                per_kwh=per_kwh,
                start_time=r.start_time,
                end_time=r.end_time,
                is_forecast=getattr(r, "type", "") == "ForecastInterval",
            ))
        return intervals

    def close(self) -> None:
        self._api_client.close()
