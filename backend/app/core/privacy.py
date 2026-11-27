"""The privacy notice shown at first sign-in (phone and dashboard).

Built from the campaign config so the retention periods it states are always the ones
the server actually enforces. Plain language, short sections, no legal jargon.
"""

from typing import Any

from app.core.defaults import merged_config


def privacy_notice(config: dict[str, Any] | None) -> dict[str, Any]:
    cfg = merged_config(config)
    p, r = cfg["privacy"], cfg["retention"]
    sections = [
        (
            "What RAGOH records",
            "While your shift is on: your phone's location (every few seconds when you are "
            "walking, every minute or two when you are still), battery level and network "
            "type, the places you stop and for how long, and the surveys you fill with the "
            "time and place they were filled.",
        ),
        (
            "When",
            "Only during a shift you start yourself. Nothing is recorded when your shift is "
            "off, and the app shows a notice while it is recording.",
        ),
        (
            "Why",
            "To plan your walking routes, confirm that visits happened, support you in the "
            "field, and check the quality of the data collected.",
        ),
        (
            "Who can see it",
            "Your supervisor and the programme's managers for your area, and system "
            "administrators. Every time someone views your route or exports data, it is "
            "recorded.",
        ),
        (
            "How long it is kept",
            f"Detailed location points are deleted after {r['raw_gps_days']} days. After that "
            "only a summary is kept (where you stopped and for how long). Surveys are kept as "
            "part of the programme's data.",
        ),
        (
            "Your rights",
            "You can ask to see the data held about you, or to correct it. Contact "
            f"{p['contact']}. The data controller is {p['controller']}.",
        ),
    ]
    return {
        "version": str(p["notice_version"]),
        "title": "Your location and privacy",
        "sections": [{"heading": h, "body": b} for h, b in sections],
        "raw_gps_days": r["raw_gps_days"],
    }
