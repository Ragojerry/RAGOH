"""Default campaign configuration. Every tunable lives here, never inline in code.

Campaign.config (jsonb) overrides any of these keys.
"""

from typing import Any

DEFAULT_CAMPAIGN_CONFIG: dict[str, Any] = {
    "geofence_m": {
        "household": 50,
        "settlement": 150,
        "facility": 100,
        "school": 100,
        "other": 75,
    },
    "tracking": {
        "moving_interval_s": 10,
        "stationary_interval_s": 90,
        "max_accuracy_m": 50,
        "heartbeat_interval_s": 180,
        "max_shift_hours": 12,
    },
    "stops": {"radius_m": 50, "min_duration_s": 180, "merge_gap_s": 120},
    "routing": {"mode": "foot", "walking_speed_kmh": 4.0},
    "offline_days": 7,
    "retention": {"raw_gps_days": 90},
}


def merged_config(overrides: dict[str, Any] | None) -> dict[str, Any]:
    """Deep-merge campaign overrides onto the defaults."""

    def merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
        out = dict(base)
        for key, value in extra.items():
            if isinstance(value, dict) and isinstance(out.get(key), dict):
                out[key] = merge(out[key], value)
            else:
                out[key] = value
        return out

    return merge(DEFAULT_CAMPAIGN_CONFIG, overrides or {})


def geofence_for(point_type: str, config: dict[str, Any] | None = None) -> int:
    cfg = merged_config(config)
    fences: dict[str, int] = cfg["geofence_m"]
    return int(fences.get(point_type, fences["other"]))
