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
    "stops": {
        "radius_m": 50,
        "min_duration_s": 180,
        "merge_gap_s": 120,
        # A run of points is "near" a visit point within this multiple of its geofence.
        "near_factor": 2.0,
        # Places with long stops on several days (e.g. home) are classified as "base".
        "base_min_dwell_s": 1800,
        "base_min_days": 2,
        "base_radius_m": 100,
    },
    "gps": {
        # Points worse than this are kept but ignored for stops and distance.
        "max_accuracy_m": 50,
        # No points for this long during a shift = tracking gap.
        "gap_s": 600,
        # Faster than this between two points = impossible movement (ignored for distance).
        "max_speed_mps": 40,
    },
    "routing": {
        "mode": "foot",
        "walking_speed_kmh": 4.0,
        # Time planned at each visit when ordering the day.
        "service_s": 600,
        "return_to_start": False,
    },
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
