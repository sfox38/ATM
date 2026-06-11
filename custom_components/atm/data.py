"""Runtime data container for the ATM integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Callable

from .audit import AuditLog
from .rate_limiter import RateLimiter
from .token_store import TokenStore


@dataclass
class ATMData:
    """Runtime state stored in hass.data[DOMAIN]. Not persisted across HA restarts.

    All mutable shared state (SSE connections, counters, caches) lives here so
    it is accessible from views, sensors, and __init__ callbacks without globals.
    """

    store: TokenStore
    rate_limiter: RateLimiter
    audit: AuditLog
    sse_connections: dict[str, set[asyncio.Queue]]
    # Tracks the monotonic time of the last rate-limit notification per token
    # to enforce the one-per-minute throttle on atm_rate_limited bus events.
    rate_limit_notified: dict[str, float] = field(default_factory=dict)
    # In-memory request/denied/rate-limit counters keyed by token ID.
    token_counters: dict[str, dict[str, int]] = field(default_factory=dict)
    entity_tree_cache: dict | None = None
    entity_tree_cache_valid: bool = False
    entity_tree_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    sse_connect_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Keyed by token name slug; values are the list of ATMTokenSensor instances.
    platform_entities: dict[str, list] = field(default_factory=dict)
    # Keyed by token ID for fast sensor lookup during counter updates.
    token_id_sensors: dict[str, list] = field(default_factory=dict)
    async_add_entities_cb: Callable | None = None
    # session_id -> (queue, token_id)
    mcp_sessions: dict[str, tuple[asyncio.Queue, str]] = field(default_factory=dict)
    # Per-token expiry timers. Values are cancel callbacks from hass.async_call_later.
    expiry_timers: dict[str, Callable] = field(default_factory=dict)
    # Callbacks wired by __init__.py to decouple sensor lifecycle from views.
    async_on_token_created: Callable | None = None
    async_on_token_archived: Callable | None = None
    # Set to True once proxy/MCP routes have been registered; prevents duplicate registration.
    routes_registered: bool = False
    # Called by the admin settings PATCH when the kill switch is deactivated.
    async_register_routes: Callable | None = None
    # Incremented on each wipe so ghost SSE sessions can detect they outlived a wipe.
    wipe_epoch: int = 0
    # Set to True by async_unload_entry. Views check this before accessing store/audit
    # to avoid KeyError 500s after unload (HA does not expose a view unregister API).
    shutting_down: bool = False
