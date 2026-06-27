"""Tests for the approvals module (PendingApproval CRUD, lifecycle, expiry)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.util.dt import utcnow

from custom_components.atm.approvals import (
    PendingApproval,
    PendingApprovalCapacityError,
    STATUS_APPROVED,
    STATUS_CANCELLED,
    STATUS_EXPIRED,
    STATUS_PENDING,
    STATUS_REJECTED,
    cancel_approvals_for_token,
    create_pending_approval,
    expire_overdue_approval_records,
    expire_overdue_approvals,
    get_approval,
    list_approvals,
    update_approval_status,
)
from custom_components.atm.approvals import create_approval_notification
from custom_components.atm.const import DOMAIN, MAX_PENDING_APPROVALS_PER_TOKEN
from custom_components.atm.token_store import GlobalSettings


class _FakeStore:
    """Minimal store-like object for tests."""

    def __init__(self) -> None:
        self._pending: list[dict] = []
        self.async_save = AsyncMock()
        self.async_lock = asyncio.Lock()

    def get_pending_approvals(self) -> list[dict]:
        return self._pending

    def set_pending_approvals(self, approvals: list[dict]) -> None:
        self._pending = approvals


@pytest.fixture
def store() -> _FakeStore:
    return _FakeStore()


# --- create_pending_approval --------------------------------------------------


class TestCreate:
    @pytest.mark.asyncio
    async def test_returns_record_with_pending_status(self, store):
        record = await create_pending_approval(
            store,
            token_id="t1",
            token_name="alice",
            tool_name="restart_ha",
            cap_name="cap_restart",
            args={},
            diff={"kind": "system_action", "summary": "Restart"},
            request_id="rid-1",
        )
        assert record.status == STATUS_PENDING
        assert record.token_id == "t1"
        assert record.tool_name == "restart_ha"
        assert record.id.startswith("appr_")

    @pytest.mark.asyncio
    async def test_persists_to_storage(self, store):
        await create_pending_approval(
            store,
            token_id="t1", token_name="alice", tool_name="restart_ha",
            cap_name="cap_restart", args={}, diff={}, request_id="rid",
        )
        assert len(store.get_pending_approvals()) == 1
        store.async_save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_expires_at_in_future(self, store):
        record = await create_pending_approval(
            store, token_id="t1", token_name="a", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r",
            ttl_seconds=60,
        )
        assert record.expires_at > utcnow()
        delta = record.expires_at - record.created_at
        assert 55 <= delta.total_seconds() <= 65

    @pytest.mark.asyncio
    async def test_per_token_capacity_enforced(self, store):
        for i in range(MAX_PENDING_APPROVALS_PER_TOKEN):
            await create_pending_approval(
                store, token_id="t1", token_name="a", tool_name="x",
                cap_name="cap_restart", args={"i": i}, diff={}, request_id=f"r{i}",
            )
        with pytest.raises(PendingApprovalCapacityError):
            await create_pending_approval(
                store, token_id="t1", token_name="a", tool_name="x",
                cap_name="cap_restart", args={}, diff={}, request_id="overflow",
            )

    @pytest.mark.asyncio
    async def test_capacity_is_per_token(self, store):
        # Fill t1 to capacity, then verify t2 can still create.
        for i in range(MAX_PENDING_APPROVALS_PER_TOKEN):
            await create_pending_approval(
                store, token_id="t1", token_name="a", tool_name="x",
                cap_name="cap_restart", args={}, diff={}, request_id=f"r{i}",
            )
        record = await create_pending_approval(
            store, token_id="t2", token_name="b", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r-other",
        )
        assert record.token_id == "t2"


# --- get / list ---------------------------------------------------------------


class TestGetAndList:
    @pytest.mark.asyncio
    async def test_get_returns_record(self, store):
        record = await create_pending_approval(
            store, token_id="t1", token_name="a", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r",
        )
        fetched = get_approval(store, record.id)
        assert fetched is not None
        assert fetched.id == record.id

    def test_get_returns_none_for_missing(self, store):
        assert get_approval(store, "appr_does_not_exist") is None

    @pytest.mark.asyncio
    async def test_list_filters_by_status(self, store):
        a = await create_pending_approval(
            store, token_id="t1", token_name="a", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r1",
        )
        b = await create_pending_approval(
            store, token_id="t1", token_name="a", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r2",
        )
        async with store.async_lock:
            await update_approval_status(store, b.id, status=STATUS_APPROVED)
        pending = list_approvals(store, status=STATUS_PENDING)
        approved = list_approvals(store, status=STATUS_APPROVED)
        assert [r.id for r in pending] == [a.id]
        assert [r.id for r in approved] == [b.id]

    @pytest.mark.asyncio
    async def test_list_filters_by_token(self, store):
        a = await create_pending_approval(
            store, token_id="t1", token_name="a", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r1",
        )
        await create_pending_approval(
            store, token_id="t2", token_name="b", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r2",
        )
        result = list_approvals(store, token_id="t1")
        assert [r.id for r in result] == [a.id]


# --- update_approval_status ---------------------------------------------------


class TestUpdateStatus:
    @pytest.mark.asyncio
    async def test_approves_pending(self, store):
        record = await create_pending_approval(
            store, token_id="t1", token_name="a", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r",
        )
        async with store.async_lock:
            updated = await update_approval_status(
                store, record.id,
                status=STATUS_APPROVED,
                approved_by_user_id="user1",
                result={"ok": True},
            )
        assert updated.status == STATUS_APPROVED
        assert updated.approved_by_user_id == "user1"
        assert updated.result == {"ok": True}
        assert updated.resolved_at is not None

    @pytest.mark.asyncio
    async def test_idempotent_on_terminal(self, store):
        record = await create_pending_approval(
            store, token_id="t1", token_name="a", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r",
        )
        async with store.async_lock:
            await update_approval_status(store, record.id, status=STATUS_APPROVED)
            second = await update_approval_status(
                store, record.id, status=STATUS_REJECTED,
                rejected_reason="too_late",
            )
        # Second call observes the existing terminal state without overwriting.
        assert second.status == STATUS_APPROVED
        assert second.rejected_reason is None

    @pytest.mark.asyncio
    async def test_returns_none_for_missing(self, store):
        async with store.async_lock:
            result = await update_approval_status(store, "appr_missing", status=STATUS_REJECTED)
        assert result is None


# --- cancel_approvals_for_token ----------------------------------------------


class TestCancelForToken:
    @pytest.mark.asyncio
    async def test_cancels_only_pending(self, store):
        a = await create_pending_approval(
            store, token_id="t1", token_name="a", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r1",
        )
        b = await create_pending_approval(
            store, token_id="t1", token_name="a", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r2",
        )
        async with store.async_lock:
            await update_approval_status(store, a.id, status=STATUS_APPROVED)
        n = await cancel_approvals_for_token(store, "t1", "token_revoked")
        assert n == 1
        assert get_approval(store, b.id).status == STATUS_CANCELLED
        assert get_approval(store, a.id).status == STATUS_APPROVED

    @pytest.mark.asyncio
    async def test_does_not_touch_other_tokens(self, store):
        await create_pending_approval(
            store, token_id="t1", token_name="a", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r1",
        )
        b = await create_pending_approval(
            store, token_id="t2", token_name="b", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r2",
        )
        await cancel_approvals_for_token(store, "t1", "token_revoked")
        assert get_approval(store, b.id).status == STATUS_PENDING


# --- expire_overdue_approvals -------------------------------------------------


class TestExpire:
    @pytest.mark.asyncio
    async def test_expires_overdue(self, store):
        record = await create_pending_approval(
            store, token_id="t1", token_name="a", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r",
            ttl_seconds=60,
        )
        # Backdate the record so it appears overdue.
        raw = store.get_pending_approvals()
        raw[0]["expires_at"] = (utcnow() - timedelta(minutes=5)).isoformat()
        store.set_pending_approvals(raw)
        n = await expire_overdue_approvals(store)
        assert n == 1
        assert get_approval(store, record.id).status == STATUS_EXPIRED

    @pytest.mark.asyncio
    async def test_does_not_expire_in_window(self, store):
        record = await create_pending_approval(
            store, token_id="t1", token_name="a", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r",
            ttl_seconds=60,
        )
        n = await expire_overdue_approvals(store)
        assert n == 0
        assert get_approval(store, record.id).status == STATUS_PENDING

    @pytest.mark.asyncio
    async def test_does_not_re_expire_terminal(self, store):
        record = await create_pending_approval(
            store, token_id="t1", token_name="a", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r",
            ttl_seconds=60,
        )
        async with store.async_lock:
            await update_approval_status(store, record.id, status=STATUS_APPROVED)
        # Now backdate — should not flip approved->expired.
        raw = store.get_pending_approvals()
        raw[0]["expires_at"] = (utcnow() - timedelta(minutes=5)).isoformat()
        store.set_pending_approvals(raw)
        n = await expire_overdue_approvals(store)
        assert n == 0
        assert get_approval(store, record.id).status == STATUS_APPROVED

    @pytest.mark.asyncio
    async def test_skips_in_progress_ids(self, store):
        record = await create_pending_approval(
            store, token_id="t1", token_name="a", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r",
            ttl_seconds=60,
        )
        raw = store.get_pending_approvals()
        raw[0]["expires_at"] = (utcnow() - timedelta(minutes=5)).isoformat()
        store.set_pending_approvals(raw)

        n = await expire_overdue_approvals(store, skip_ids={record.id})

        assert n == 0
        assert get_approval(store, record.id).status == STATUS_PENDING

    @pytest.mark.asyncio
    async def test_returns_expired_records(self, store):
        record = await create_pending_approval(
            store, token_id="t1", token_name="a", tool_name="x",
            cap_name="cap_restart", args={}, diff={}, request_id="r",
            ttl_seconds=60,
        )
        raw = store.get_pending_approvals()
        raw[0]["expires_at"] = (utcnow() - timedelta(minutes=5)).isoformat()
        store.set_pending_approvals(raw)

        expired = await expire_overdue_approval_records(store)

        assert [approval.id for approval in expired] == [record.id]
        assert expired[0].status == STATUS_EXPIRED


# --- to_dict / from_dict round trip -----------------------------------------


class TestRecordSerialization:
    def test_round_trip(self):
        original = PendingApproval(
            id="appr_test",
            token_id="t1",
            token_name="alice",
            tool_name="restart_ha",
            cap_name="cap_restart",
            args={"k": "v"},
            diff={"kind": "system_action", "summary": "Restart"},
            status=STATUS_PENDING,
            created_at=utcnow(),
            expires_at=utcnow() + timedelta(seconds=60),
            request_id="rid",
        )
        roundtrip = PendingApproval.from_dict(original.to_dict())
        assert roundtrip.id == original.id
        assert roundtrip.tool_name == original.tool_name
        assert roundtrip.args == original.args
        assert roundtrip.diff == original.diff

    def test_is_terminal(self):
        for status in (STATUS_APPROVED, STATUS_REJECTED, STATUS_EXPIRED, STATUS_CANCELLED):
            r = PendingApproval(
                id="x", token_id="t", token_name="n", tool_name="x",
                cap_name="c", args={}, diff={}, status=status,
                created_at=utcnow(), expires_at=utcnow(), request_id="r",
            )
            assert r.is_terminal() is True
        r = PendingApproval(
            id="x", token_id="t", token_name="n", tool_name="x",
            cap_name="c", args={}, diff={}, status=STATUS_PENDING,
            created_at=utcnow(), expires_at=utcnow(), request_id="r",
        )
        assert r.is_terminal() is False


class TestNotificationGating:
    """create_approval_notification honours the notify_on_approval setting."""

    def _approval(self) -> PendingApproval:
        return PendingApproval(
            id="appr_x", token_id="t", token_name="codex", tool_name="call_service",
            cap_name="cap_physical_control", args={}, diff={}, status=STATUS_PENDING,
            created_at=utcnow(), expires_at=utcnow(), request_id="r",
        )

    def _data(self, *, notify: bool) -> MagicMock:
        data = MagicMock()
        data.store.get_settings = MagicMock(return_value=GlobalSettings(notify_on_approval=notify))
        return data

    def test_suppressed_when_disabled(self, hass):
        hass.data[DOMAIN] = self._data(notify=False)
        with patch("homeassistant.components.persistent_notification.async_create") as m:
            create_approval_notification(hass, self._approval())
            m.assert_not_called()

    def test_fired_when_enabled(self, hass):
        hass.data[DOMAIN] = self._data(notify=True)
        with patch("homeassistant.components.persistent_notification.async_create") as m:
            create_approval_notification(hass, self._approval())
            m.assert_called_once()


class TestArgsRedaction:
    """Approval args are redacted in the admin-facing serialisation but kept raw
    on the persistence path so the approved-action executor can re-run them."""

    def _approval(self) -> PendingApproval:
        now = utcnow()
        return PendingApproval(
            id="a1", token_id="t", token_name="n", tool_name="write_file",
            cap_name="cap_filesystem",
            args={"path": "/config/secrets.yaml", "content": "api_key: abc123secret"},
            diff={}, status=STATUS_PENDING,
            created_at=now, expires_at=now, request_id="r",
        )

    def test_default_to_dict_redacts_args(self):
        d = self._approval().to_dict()
        assert "abc123secret" not in d["args"]["content"]
        assert d["args"]["path"] == "/config/secrets.yaml"

    def test_persistence_to_dict_keeps_raw_args(self):
        d = self._approval().to_dict(redact_args=False)
        assert d["args"]["content"] == "api_key: abc123secret"

    @pytest.mark.asyncio
    async def test_created_approval_persists_raw_args(self, store):
        await create_pending_approval(
            store, token_id="t", token_name="n", tool_name="write_file",
            cap_name="cap_filesystem",
            args={"content": "api_key: abc123secret"}, diff={}, request_id="r",
        )
        stored = store.get_pending_approvals()[0]
        assert stored["args"]["content"] == "api_key: abc123secret"
