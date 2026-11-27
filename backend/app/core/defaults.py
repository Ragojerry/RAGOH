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
    # Abnormal-behaviour rules. Every rule can be switched off or re-tuned per campaign.
    "flags": {
        "R1": {"enabled": True, "severity": "high", "tolerance_m": 20},  # survey outside geofence
        "R2": {"enabled": True, "severity": "medium", "min_duration_s": 180},  # too-short interview
        "R3": {"enabled": True, "severity": "low", "max_duration_s": 3600},  # too-long interview
        "R4": {
            "enabled": True,
            "severity": "high",
            "max_count": 4,
            "window_min": 15,
        },  # too many too fast
        "R5": {
            "enabled": True,
            "severity": "medium",
            "min_dwell_s": 300,
        },  # short stop at completed point
        "R6": {"enabled": True, "severity": "high"},  # completed without visiting
        "R7": {"enabled": True, "severity": "medium", "min_jumps": 1},  # impossible movement
        "R8": {"enabled": True, "severity": "high"},  # mock location
        "R9": {"enabled": True, "severity": "medium", "max_idle_s": 3600},  # long idle elsewhere
        "R10": {"enabled": True, "severity": "medium", "min_gap_s": 1200},  # tracking gaps
        "R11": {
            "enabled": True,
            "severity": "low",
            "max_outside_share": 0.2,
        },  # outside assigned area
        "R12": {
            "enabled": True,
            "severity": "low",
            "start": "07:00",
            "end": "18:00",
        },  # outside hours
        "R13": {"enabled": True, "severity": "medium", "max_offset_s": 300},  # phone clock wrong
        "R14": {
            "enabled": True,
            "severity": "medium",
            "min_submissions": 3,
            "same_answers_share": 0.8,
            "refusal_share": 0.5,
            "min_for_refusal": 4,
        },  # data-quality patterns
        "R15": {
            "enabled": True,
            "severity": "low",
            "share_of_median": 0.5,
            "min_team": 3,
            "min_median": 4,
        },
        "S1": {
            "enabled": True,
            "severity": "medium",
            "days": 7,
        },  # collector not supervised recently
        "S2": {
            "enabled": True,
            "severity": "medium",
            "hours": 24,
        },  # high flag left without follow-up
    },
    "risk": {"weights": {"low": 1, "medium": 3, "high": 8}, "max": 100},
    "offline_days": 7,
    "retention": {
        "raw_gps_days": 90,  # then only stops and daily summaries are kept
        "device_events_days": 180,
        "notifications_days": 60,
        "audit_days": 730,  # who viewed/exported tracking data: kept two years
    },
    # Shown to every user at first sign-in (NDPA 2023). Bump notice_version when the
    # wording or a retention period changes, so everyone sees and accepts it again.
    "privacy": {
        "notice_version": "2",
        "controller": "the programme running this campaign",
        "contact": "your supervisor or the programme's data protection officer",
    },
    # Live supervision (Phase 6)
    "live": {
        "heartbeat_every_s": 180,  # phone pings this often while on shift
        "offline_after_min": 10,  # no heartbeat for this long -> shown as offline
        "idle_after_min": 30,  # on shift, not moved this far in this long -> idle
        "idle_radius_m": 50,
        "at_point_m": 40,  # within this of an assigned point -> at point
    },
    "alerts": {
        "push_high_flags": True,  # notify supervisors when a high-severity flag appears
        "offline_during_shift_min": 30,  # notify if a collector on shift is silent this long
        "shift_start_by": "09:00",  # notify if a collector with work today hasn't started
        "quiet_after": "20:00",  # don't send alerts after this local time
    },
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
