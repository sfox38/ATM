"""Canonical tag registry and tag format validation (Spec Appendix A, Section 22)."""

from __future__ import annotations

import re

CANONICAL_TAGS: frozenset[str] = frozenset(
    {
        # Lighting
        "lighting.ambient",
        "lighting.task",
        "lighting.accent",
        "lighting.security",
        "lighting.circadian",
        "lighting.dimming",
        "lighting.colour",
        "lighting.scene",
        # Climate
        "climate.heating",
        "climate.cooling",
        "climate.humidity_control",
        "climate.energy_optimization",
        "climate.comfort_optimization",
        "climate.zone_control",
        "climate.air_quality",
        # Media
        "media.multiroom",
        "media.lossless",
        "media.hardware_sync",
        "media.voice_control",
        "media.casting",
        "media.local_only",
        # Security
        "security.entry_monitoring",
        "security.motion_detection",
        "security.perimeter",
        "security.alarm",
        "security.access_control",
        "security.camera",
        # Presence and occupancy
        "presence.occupancy",
        "presence.person_identification",
        "presence.sleep_tracking",
        "presence.arrival_departure",
        # Energy and power
        "energy.monitoring",
        "energy.high_consumption",
        "energy.solar",
        "energy.battery",
        "energy.grid_aware",
        # Space
        "space.sleeping",
        "space.working",
        "space.media",
        "space.entertainment",
        "space.utility",
        "space.transition",
        "space.outdoor",
        # Diagnostics and causality
        "diagnostic.causality",
        "diagnostic.confidence_scored",
        "diagnostic.history_log",
        "diagnostic.read_only",
        "diagnostic.drift_prone",
        # Performance
        "latency.low",
        "latency.medium",
        "latency.high",
        "sync.hardware_precise",
        "reliability.high",
        "reliability.poll_dependent",
        # Audio
        "audio.high_fidelity",
        "audio.multiroom",
        "audio.synchronised_playback",
        "audio.voice_optimised",
        # TTS and speech
        "tts.multilingual",
        "tts.expressive_voices",
        "tts.auto_chunking",
        # Automation intent
        "automation.lighting",
        "automation.climate",
        "automation.security",
        "automation.energy_conservation",
        "automation.presence_response",
        "automation.schedule_based",
        "automation.notification",
        "automation.media",
        "automation.maintenance",
        # Scene intent
        "scene.lighting",
        "scene.climate",
        "scene.media",
        "scene.security",
        "scene.away",
        "scene.sleep",
        "scene.arrival",
        "scene.guest",
        # Helpers
        "helper.mode_flag",
        "helper.config_parameter",
        "helper.coordination_signal",
        "helper.counter_metric",
        "helper.timer_control",
        "helper.user_preference",
        "helper.dashboard_only",
        "helper.high_impact",
        # Zones
        "zone.home",
        "zone.workplace",
        "zone.school",
        "zone.healthcare",
        "zone.child_associated",
        "zone.high_sensitivity",
        # People
        "person.primary_resident",
        "person.secondary_resident",
        "person.child",
        "person.guest",
        "person.caregiver",
        # Assist
        "assist.local",
        "assist.cloud",
        "assist.satellite",
        "assist.always_on",
        "assist.announcement_capable",
        # UI structural
        "ui.glanceable",
        "ui.accessible",
        # Resource
        "resource.high_power",
        "resource.cloud_dependent",
        "resource.local_only",
        "resource.grid_aware",
        "resource.battery_powered",
    }
)

CANONICAL_ROOTS: frozenset[str] = frozenset(tag.split(".", 1)[0] for tag in CANONICAL_TAGS)

# Spec Section 22: all lowercase, dot notation, no hyphens in the namespace root.
_TAG_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")


def check_tag(tag: str) -> str | None:
    """Validate one tag. Returns an error message, or None when the tag is valid.

    Valid tags are either canonical (Appendix A) or vendor-namespaced
    (``vendorname.qualifier`` where the root is not a canonical root).
    """
    if tag in CANONICAL_TAGS:
        return None
    if not _TAG_PATTERN.match(tag):
        return (
            f"invalid tag format: {tag!r} (tags are lowercase dot notation, "
            "no hyphens in the namespace root)"
        )
    root = tag.split(".", 1)[0]
    if root in CANONICAL_ROOTS:
        return (
            f"unknown tag {tag!r} uses canonical namespace root {root!r}; "
            "vendor tags MUST NOT reuse canonical roots (Spec Section 22)"
        )
    return None  # well-formed vendor namespace tag; treated as opaque


def check_tags(tags: list[str]) -> list[str]:
    """Validate a list of tags, returning all error messages."""
    return [err for tag in tags if (err := check_tag(tag)) is not None]
