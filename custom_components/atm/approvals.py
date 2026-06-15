"""Pending-approval queue and lifecycle for the admin-confirmation gate.

When a token invokes a capability set to "confirm" mode, the request is
recorded as a PendingApproval rather than executing immediately. The admin
reviews the diff in the panel and approves or rejects. Approved actions
re-validate the token's current state before executing; rejected and
expired actions never run.

State transitions out of "pending" are terminal and serialized under
TokenStore.async_lock. See CAPABILITY_REDESIGN_PLAN.md for the full state
machine and race-condition resolution.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.util.dt import parse_datetime, utcnow

from .const import (
    APPROVAL_DEFAULT_TTL_SECONDS,
    DOMAIN,
    MAX_PENDING_APPROVALS_PER_TOKEN,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .token_store import TokenStore

_LOGGER = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_EXPIRED = "expired"
STATUS_CANCELLED = "cancelled"

TERMINAL_STATUSES = frozenset({
    STATUS_APPROVED,
    STATUS_REJECTED,
    STATUS_EXPIRED,
    STATUS_CANCELLED,
})

REASON_TOKEN_INACTIVE = "token_inactive"
REASON_CAPABILITY_DENIED = "capability_denied"
REASON_TARGET_OUT_OF_SCOPE = "target_out_of_scope"
REASON_TARGET_MISSING = "target_missing"
REASON_RATE_LIMITED = "rate_limited_at_execution"
REASON_KILL_SWITCH = "kill_switch"
REASON_ADMIN_CANCELLED = "admin_cancelled"
REASON_REVOKED = "token_revoked"


class PendingApprovalCapacityError(Exception):
    """Raised when a token already holds MAX_PENDING_APPROVALS_PER_TOKEN entries."""


@dataclass
class PendingApproval:
    """One queued approval request awaiting admin decision."""

    id: str
    token_id: str
    token_name: str
    tool_name: str
    cap_name: str
    args: dict
    diff: dict
    status: str
    created_at: datetime
    expires_at: datetime
    request_id: str
    client_ip: str | None = None
    resolved_at: datetime | None = None
    approved_by_user_id: str | None = None
    rejected_reason: str | None = None
    result: Any = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "token_id": self.token_id,
            "token_name": self.token_name,
            "tool_name": self.tool_name,
            "cap_name": self.cap_name,
            "args": self.args,
            "diff": self.diff,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "approved_by_user_id": self.approved_by_user_id,
            "rejected_reason": self.rejected_reason,
            "result": self.result,
            "request_id": self.request_id,
            "client_ip": self.client_ip,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PendingApproval:
        return cls(
            id=data["id"],
            token_id=data["token_id"],
            token_name=data.get("token_name", ""),
            tool_name=data["tool_name"],
            cap_name=data.get("cap_name", ""),
            args=data.get("args", {}),
            diff=data.get("diff", {}),
            status=data.get("status", STATUS_PENDING),
            created_at=parse_datetime(data["created_at"]) or utcnow(),
            expires_at=parse_datetime(data["expires_at"]) or utcnow(),
            resolved_at=parse_datetime(data["resolved_at"]) if data.get("resolved_at") else None,
            approved_by_user_id=data.get("approved_by_user_id"),
            rejected_reason=data.get("rejected_reason"),
            result=data.get("result"),
            request_id=data.get("request_id", ""),
            client_ip=data.get("client_ip"),
        )

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


def _new_approval_id() -> str:
    return f"appr_{uuid.uuid4().hex[:16]}"


async def create_pending_approval(
    store: TokenStore,
    *,
    token_id: str,
    token_name: str,
    tool_name: str,
    cap_name: str,
    args: dict,
    diff: dict,
    request_id: str,
    client_ip: str | None = None,
    ttl_seconds: int = APPROVAL_DEFAULT_TTL_SECONDS,
) -> PendingApproval:
    """Add a new pending approval to storage and return the record.

    Caller must hold store.async_lock if creating from a multi-step path.
    Raises PendingApprovalCapacityError if the token is at the per-token cap.
    """
    raw = store.get_pending_approvals()
    pending_for_token = sum(
        1 for entry in raw
        if entry.get("token_id") == token_id and entry.get("status") == STATUS_PENDING
    )
    if pending_for_token >= MAX_PENDING_APPROVALS_PER_TOKEN:
        raise PendingApprovalCapacityError(
            f"token {token_id} already has {pending_for_token} pending approvals"
        )
    now = utcnow()
    approval = PendingApproval(
        id=_new_approval_id(),
        token_id=token_id,
        token_name=token_name,
        tool_name=tool_name,
        cap_name=cap_name,
        args=args,
        diff=diff,
        status=STATUS_PENDING,
        created_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
        request_id=request_id,
        client_ip=client_ip,
    )
    raw.append(approval.to_dict())
    store.set_pending_approvals(raw)
    await store.async_save()
    return approval


def get_approval(store: TokenStore, approval_id: str) -> PendingApproval | None:
    """Return the approval record for an ID, or None if not found."""
    for entry in store.get_pending_approvals():
        if entry.get("id") == approval_id:
            try:
                return PendingApproval.from_dict(entry)
            except (KeyError, TypeError, ValueError) as exc:
                _LOGGER.warning("Skipping corrupt approval record %r: %s", approval_id, exc)
                return None
    return None


def list_approvals(
    store: TokenStore,
    *,
    status: str | None = None,
    token_id: str | None = None,
) -> list[PendingApproval]:
    """Return approvals matching optional filters, newest first."""
    out: list[PendingApproval] = []
    for entry in store.get_pending_approvals():
        if status is not None and entry.get("status") != status:
            continue
        if token_id is not None and entry.get("token_id") != token_id:
            continue
        try:
            out.append(PendingApproval.from_dict(entry))
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda a: a.created_at, reverse=True)
    return out


async def update_approval_status(
    store: TokenStore,
    approval_id: str,
    *,
    status: str,
    approved_by_user_id: str | None = None,
    rejected_reason: str | None = None,
    result: Any = None,
) -> PendingApproval | None:
    """Transition an approval to a terminal state and persist.

    Returns the updated record. Returns None if the approval is missing.
    Caller must hold store.async_lock.
    """
    raw = store.get_pending_approvals()
    for entry in raw:
        if entry.get("id") != approval_id:
            continue
        if entry.get("status") != STATUS_PENDING:
            return PendingApproval.from_dict(entry)
        entry["status"] = status
        entry["resolved_at"] = utcnow().isoformat()
        if approved_by_user_id is not None:
            entry["approved_by_user_id"] = approved_by_user_id
        if rejected_reason is not None:
            entry["rejected_reason"] = rejected_reason
        if result is not None:
            entry["result"] = result
        store.set_pending_approvals(raw)
        await store.async_save()
        return PendingApproval.from_dict(entry)
    return None


async def cancel_approvals_for_token(
    store: TokenStore,
    token_id: str,
    reason: str,
) -> int:
    """Mark every pending approval for a token as cancelled. Returns count."""
    raw = store.get_pending_approvals()
    changed = 0
    now_iso = utcnow().isoformat()
    for entry in raw:
        if entry.get("token_id") != token_id:
            continue
        if entry.get("status") != STATUS_PENDING:
            continue
        entry["status"] = STATUS_CANCELLED
        entry["resolved_at"] = now_iso
        entry["rejected_reason"] = reason
        changed += 1
    if changed:
        store.set_pending_approvals(raw)
        await store.async_save()
    return changed


async def expire_overdue_approvals(store: TokenStore) -> int:
    """Move all pending approvals past their expires_at to status=expired.

    Returns the number of records expired. Caller must hold async_lock.
    """
    raw = store.get_pending_approvals()
    now = utcnow()
    now_iso = now.isoformat()
    changed = 0
    for entry in raw:
        if entry.get("status") != STATUS_PENDING:
            continue
        try:
            expires = parse_datetime(entry.get("expires_at", ""))
        except (TypeError, ValueError):
            expires = None
        if expires is None or expires > now:
            continue
        entry["status"] = STATUS_EXPIRED
        entry["resolved_at"] = now_iso
        changed += 1
    if changed:
        store.set_pending_approvals(raw)
        await store.async_save()
    return changed


def fire_approval_requested_event(hass: HomeAssistant, approval: PendingApproval) -> None:
    """Fire an HA event when a new approval is queued."""
    hass.bus.async_fire(
        f"{DOMAIN}_approval_requested",
        {
            "approval_id": approval.id,
            "token_id": approval.token_id,
            "token_name": approval.token_name,
            "tool_name": approval.tool_name,
            "cap_name": approval.cap_name,
            "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
            "timestamp": utcnow().isoformat(),
        },
    )


def fire_approval_resolved_event(hass: HomeAssistant, approval: PendingApproval) -> None:
    """Fire an HA event when an approval reaches a terminal state."""
    hass.bus.async_fire(
        f"{DOMAIN}_approval_resolved",
        {
            "approval_id": approval.id,
            "token_id": approval.token_id,
            "token_name": approval.token_name,
            "tool_name": approval.tool_name,
            "status": approval.status,
            "rejected_reason": approval.rejected_reason,
            "approved_by_user_id": approval.approved_by_user_id,
            "timestamp": utcnow().isoformat(),
        },
    )


def notification_id_for_approval(approval_id: str) -> str:
    """Return the persistent_notification ID used for an approval."""
    return f"{DOMAIN}_approval_{approval_id}"


def create_approval_notification(hass: HomeAssistant, approval: PendingApproval) -> None:
    """Fire an HA persistent notification for a new pending approval.

    Suppressed when the admin has turned off approval notifications
    (settings.notify_on_approval). The in-panel Approvals badge still updates.
    """
    data = hass.data.get(DOMAIN)
    if data is not None and not data.store.get_settings().notify_on_approval:
        return

    from homeassistant.components import persistent_notification  # noqa: PLC0415

    persistent_notification.async_create(
        hass,
        message=(
            f"Token '{approval.token_name}' requested approval.\n\n"
            f"[Review in ATM](/atm#approvals/{approval.id})"
        ),
        title="ATM",
        notification_id=notification_id_for_approval(approval.id),
    )


def dismiss_approval_notification(hass: HomeAssistant, approval_id: str) -> None:
    """Dismiss the persistent notification for an approval after resolution."""
    from homeassistant.components import persistent_notification  # noqa: PLC0415

    persistent_notification.async_dismiss(
        hass,
        notification_id=notification_id_for_approval(approval_id),
    )
