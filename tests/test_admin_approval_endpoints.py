"""Tests for the admin approval HTTP endpoints in admin_view.py."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.util.dt import utcnow

from custom_components.atm.admin_view import (
    ATMAdminApprovalApproveView,
    ATMAdminApprovalRejectView,
    ATMAdminApprovalView,
    ATMAdminApprovalsView,
)
from custom_components.atm.approvals import (
    STATUS_APPROVED,
    STATUS_CANCELLED,
    STATUS_PENDING,
    STATUS_REJECTED,
)
from custom_components.atm.audit import AuditLog
from custom_components.atm.const import DOMAIN, TOKEN_PREFIX
from custom_components.atm.data import ATMData
from custom_components.atm.rate_limiter import RateLimiter
from custom_components.atm.token_store import GlobalSettings, TokenRecord, TokenStore


# ---- helpers -----------------------------------------------------------------


def _make_token(token_id: str = "tok-1", name: str = "alice", **caps) -> TokenRecord:
    raw = TOKEN_PREFIX + secrets.token_hex(32)
    return TokenRecord(
        id=token_id,
        name=name,
        token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        created_at=utcnow(),
        created_by="admin",
        **caps,
    )


def _make_pending(approval_id: str, token_id: str = "tok-1", **kwargs) -> dict:
    now = utcnow()
    base = {
        "id": approval_id,
        "token_id": token_id,
        "token_name": "alice",
        "tool_name": "restart_ha",
        "cap_name": "cap_restart",
        "args": {},
        "diff": {"kind": "system_action", "summary": "Restart"},
        "status": STATUS_PENDING,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=3600)).isoformat(),
        "resolved_at": None,
        "approved_by_user_id": None,
        "rejected_reason": None,
        "result": None,
        "request_id": "rid-1",
        "client_ip": None,
    }
    base.update(kwargs)
    return base


def _make_store(pending: list[dict] | None = None, tokens: list[TokenRecord] | None = None,
                kill_switch: bool = False) -> MagicMock:
    store = MagicMock(spec=TokenStore)
    store._pending = list(pending or [])
    store.async_save = AsyncMock()
    store.async_lock = asyncio.Lock()
    store.get_pending_approvals = MagicMock(side_effect=lambda: store._pending)
    store.set_pending_approvals = MagicMock(side_effect=lambda lst: setattr(store, "_pending", lst))
    settings = GlobalSettings(kill_switch=kill_switch)
    store.get_settings = MagicMock(return_value=settings)
    by_id = {t.id: t for t in (tokens or [])}
    store.get_token_by_id = MagicMock(side_effect=lambda i: by_id.get(i))
    return store


def _make_data(store: MagicMock) -> ATMData:
    rate_limiter = MagicMock(spec=RateLimiter)
    audit = MagicMock(spec=AuditLog)
    audit.record = MagicMock()
    return ATMData(
        store=store,
        rate_limiter=rate_limiter,
        audit=audit,
    )


def _make_admin_request(body: bytes = b"", query: dict | None = None) -> MagicMock:
    from homeassistant.components.http.const import KEY_AUTHENTICATED, KEY_HASS_USER

    user = MagicMock()
    user.is_admin = True
    user.id = "admin-user"

    def _get(k, default=None):
        if k == KEY_HASS_USER:
            return user
        if k == KEY_AUTHENTICATED:
            return True
        return default

    rid = "test-rid"
    state: dict = {KEY_HASS_USER: user, KEY_AUTHENTICATED: True, "atm_rid": rid}

    request = MagicMock()
    request.query = query or {}
    request.read = AsyncMock(return_value=body)
    request.content_length = len(body)
    request.content = MagicMock()
    request.content.read = AsyncMock(return_value=body)
    request.__getitem__ = MagicMock(side_effect=lambda k: state.get(k))
    request.get = MagicMock(side_effect=_get)
    return request


def _make_hass(data: ATMData) -> MagicMock:
    hass = MagicMock()
    hass.data = {DOMAIN: data}
    hass.bus = MagicMock()
    hass.bus.async_fire = MagicMock()
    return hass


# ---- list view ---------------------------------------------------------------


class TestApprovalsList:
    @pytest.mark.asyncio
    async def test_returns_all_when_no_filter(self):
        store = _make_store(pending=[
            _make_pending("appr_a"),
            _make_pending("appr_b", status=STATUS_APPROVED),
        ])
        data = _make_data(store)
        hass = _make_hass(data)
        view = ATMAdminApprovalsView()
        view.hass = hass
        request = _make_admin_request()

        with patch("custom_components.atm.admin_view.require_admin", lambda f: f):
            resp = await view.get(request)

        body = json.loads(resp.text)
        assert body["total"] == 2

    @pytest.mark.asyncio
    async def test_filters_by_status(self):
        store = _make_store(pending=[
            _make_pending("appr_a"),
            _make_pending("appr_b", status=STATUS_APPROVED),
        ])
        data = _make_data(store)
        hass = _make_hass(data)
        view = ATMAdminApprovalsView()
        view.hass = hass
        request = _make_admin_request(query={"status": STATUS_PENDING})

        with patch("custom_components.atm.admin_view.require_admin", lambda f: f):
            resp = await view.get(request)

        body = json.loads(resp.text)
        assert body["total"] == 1
        assert body["approvals"][0]["id"] == "appr_a"

    @pytest.mark.asyncio
    async def test_filters_by_token(self):
        store = _make_store(pending=[
            _make_pending("appr_a", token_id="tok-1"),
            _make_pending("appr_b", token_id="tok-2"),
        ])
        data = _make_data(store)
        hass = _make_hass(data)
        view = ATMAdminApprovalsView()
        view.hass = hass
        request = _make_admin_request(query={"token_id": "tok-1"})

        with patch("custom_components.atm.admin_view.require_admin", lambda f: f):
            resp = await view.get(request)

        body = json.loads(resp.text)
        assert body["total"] == 1
        assert body["approvals"][0]["id"] == "appr_a"


# ---- detail view -------------------------------------------------------------


class TestApprovalDetail:
    @pytest.mark.asyncio
    async def test_returns_record(self):
        store = _make_store(pending=[_make_pending("appr_a")])
        data = _make_data(store)
        hass = _make_hass(data)
        view = ATMAdminApprovalView()
        view.hass = hass
        request = _make_admin_request()

        with patch("custom_components.atm.admin_view.require_admin", lambda f: f):
            resp = await view.get(request, approval_id="appr_a")

        body = json.loads(resp.text)
        assert body["id"] == "appr_a"
        assert body["tool_name"] == "restart_ha"

    @pytest.mark.asyncio
    async def test_returns_404_for_missing(self):
        store = _make_store(pending=[])
        data = _make_data(store)
        hass = _make_hass(data)
        view = ATMAdminApprovalView()
        view.hass = hass
        request = _make_admin_request()

        with patch("custom_components.atm.admin_view.require_admin", lambda f: f):
            resp = await view.get(request, approval_id="appr_missing")

        assert resp.status == 404


# ---- delete (cancel alias) ---------------------------------------------------


class TestApprovalDelete:
    @pytest.mark.asyncio
    async def test_delete_cancels_pending(self):
        store = _make_store(pending=[_make_pending("appr_a")])
        data = _make_data(store)
        hass = _make_hass(data)
        view = ATMAdminApprovalView()
        view.hass = hass
        request = _make_admin_request()

        with patch("custom_components.atm.admin_view.require_admin", lambda f: f), \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await view.delete(request, approval_id="appr_a")

        body = json.loads(resp.text)
        assert body["status"] == STATUS_CANCELLED
        assert body["rejected_reason"] == "admin_cancelled"

    @pytest.mark.asyncio
    async def test_delete_idempotent_on_terminal(self):
        store = _make_store(pending=[_make_pending("appr_a", status=STATUS_APPROVED)])
        data = _make_data(store)
        hass = _make_hass(data)
        view = ATMAdminApprovalView()
        view.hass = hass
        request = _make_admin_request()

        with patch("custom_components.atm.admin_view.require_admin", lambda f: f):
            resp = await view.delete(request, approval_id="appr_a")

        body = json.loads(resp.text)
        # Idempotent: already-terminal record returns its current state, not cancelled.
        assert body["status"] == STATUS_APPROVED

    @pytest.mark.asyncio
    async def test_delete_rejected_when_already_in_progress(self):
        store = _make_store(pending=[_make_pending("appr_a")])
        data = _make_data(store)
        data.approvals_in_progress.add("appr_a")
        hass = _make_hass(data)
        view = ATMAdminApprovalView()
        view.hass = hass
        request = _make_admin_request()

        with patch("custom_components.atm.admin_view.require_admin", lambda f: f):
            resp = await view.delete(request, approval_id="appr_a")

        assert resp.status == 409
        assert next(r for r in store._pending if r["id"] == "appr_a")["status"] == STATUS_PENDING


# ---- reject view -------------------------------------------------------------


class TestApprovalReject:
    @pytest.mark.asyncio
    async def test_reject_with_reason(self):
        store = _make_store(pending=[_make_pending("appr_a")])
        data = _make_data(store)
        hass = _make_hass(data)
        view = ATMAdminApprovalRejectView()
        view.hass = hass
        body = json.dumps({"reason": "not_safe"}).encode()
        request = _make_admin_request(body=body)

        with patch("custom_components.atm.admin_view.require_admin", lambda f: f), \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await view.post(request, approval_id="appr_a")

        out = json.loads(resp.text)
        assert out["status"] == STATUS_REJECTED
        assert out["rejected_reason"] == "not_safe"

    @pytest.mark.asyncio
    async def test_reject_without_body(self):
        store = _make_store(pending=[_make_pending("appr_a")])
        data = _make_data(store)
        hass = _make_hass(data)
        view = ATMAdminApprovalRejectView()
        view.hass = hass
        request = _make_admin_request(body=b"{}")

        with patch("custom_components.atm.admin_view.require_admin", lambda f: f), \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await view.post(request, approval_id="appr_a")

        out = json.loads(resp.text)
        assert out["status"] == STATUS_REJECTED
        assert out["rejected_reason"] is None

    @pytest.mark.asyncio
    async def test_reject_rejected_when_already_in_progress(self):
        store = _make_store(pending=[_make_pending("appr_a")])
        data = _make_data(store)
        data.approvals_in_progress.add("appr_a")
        hass = _make_hass(data)
        view = ATMAdminApprovalRejectView()
        view.hass = hass
        request = _make_admin_request(body=b"{}")

        with patch("custom_components.atm.admin_view.require_admin", lambda f: f):
            resp = await view.post(request, approval_id="appr_a")

        assert resp.status == 409
        assert next(r for r in store._pending if r["id"] == "appr_a")["status"] == STATUS_PENDING


# ---- approve view ------------------------------------------------------------


class TestApprovalApprove:
    @pytest.mark.asyncio
    async def test_approve_runs_executor_and_marks_approved(self):
        token = _make_token("tok-1", cap_restart="confirm")
        store = _make_store(pending=[_make_pending("appr_a", token_id="tok-1")], tokens=[token])
        data = _make_data(store)
        hass = _make_hass(data)
        view = ATMAdminApprovalApproveView()
        view.hass = hass
        request = _make_admin_request(body=b"{}")

        async def _fake_executor(name, args, tok, hass, data):
            return ({"content": [{"type": "text", "text": '{"success": true}'}]}, "allowed", "restart_ha")

        with patch("custom_components.atm.admin_view.require_admin", lambda f: f), \
             patch("custom_components.atm.mcp_view.execute_approved_tool", side_effect=_fake_executor), \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await view.post(request, approval_id="appr_a")

        out = json.loads(resp.text)
        assert out["status"] == STATUS_APPROVED
        assert out["result"]["outcome"] == "allowed"
        assert out["approved_by_user_id"] == "admin-user"

    @pytest.mark.asyncio
    async def test_approve_rejected_when_already_in_progress(self):
        # Double-run race guard: an approve whose id is already claimed by a
        # concurrent in-flight approve returns 409 and never runs the executor.
        token = _make_token("tok-1", cap_restart="confirm")
        store = _make_store(pending=[_make_pending("appr_a", token_id="tok-1")], tokens=[token])
        data = _make_data(store)
        data.approvals_in_progress.add("appr_a")
        hass = _make_hass(data)
        view = ATMAdminApprovalApproveView()
        view.hass = hass
        request = _make_admin_request(body=b"{}")

        executor = AsyncMock()
        with patch("custom_components.atm.admin_view.require_admin", lambda f: f), \
             patch("custom_components.atm.mcp_view.execute_approved_tool", executor), \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await view.post(request, approval_id="appr_a")

        assert resp.status == 409
        executor.assert_not_called()
        # Untouched: still pending for the in-flight request to finalize.
        assert next(r for r in store._pending if r["id"] == "appr_a")["status"] == STATUS_PENDING

    @pytest.mark.asyncio
    async def test_approve_releases_in_progress_claim(self):
        # The claim is released after execution so the id is not stuck.
        token = _make_token("tok-1", cap_restart="confirm")
        store = _make_store(pending=[_make_pending("appr_a", token_id="tok-1")], tokens=[token])
        data = _make_data(store)
        hass = _make_hass(data)
        view = ATMAdminApprovalApproveView()
        view.hass = hass
        request = _make_admin_request(body=b"{}")

        async def _fake_executor(name, args, tok, hass, data):
            return ({"content": [{"type": "text", "text": '{"success": true}'}]}, "allowed", "restart_ha")

        with patch("custom_components.atm.admin_view.require_admin", lambda f: f), \
             patch("custom_components.atm.mcp_view.execute_approved_tool", side_effect=_fake_executor), \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await view.post(request, approval_id="appr_a")

        assert json.loads(resp.text)["status"] == STATUS_APPROVED
        assert "appr_a" not in data.approvals_in_progress

    @pytest.mark.asyncio
    async def test_approve_conflicts_if_record_resolved_during_execution(self):
        token = _make_token("tok-1", cap_restart="confirm")
        store = _make_store(pending=[_make_pending("appr_a", token_id="tok-1")], tokens=[token])
        data = _make_data(store)
        hass = _make_hass(data)
        view = ATMAdminApprovalApproveView()
        view.hass = hass
        request = _make_admin_request(body=b"{}")

        async def _executor_that_resolves_elsewhere(name, args, tok, hass, data):
            store._pending[0]["status"] = STATUS_REJECTED
            store._pending[0]["rejected_reason"] = "admin_cancelled"
            return ({"content": [{"type": "text", "text": '{"success": true}'}]}, "allowed", "restart_ha")

        with patch("custom_components.atm.admin_view.require_admin", lambda f: f), \
             patch("custom_components.atm.mcp_view.execute_approved_tool", side_effect=_executor_that_resolves_elsewhere), \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await view.post(request, approval_id="appr_a")

        assert resp.status == 409
        assert store._pending[0]["status"] == STATUS_REJECTED
        data.audit.record.assert_called_once()
        audit_kwargs = data.audit.record.call_args.kwargs
        assert audit_kwargs["method"] == f"approval/{STATUS_APPROVED}"
        assert audit_kwargs["outcome"] == "allowed"
        assert audit_kwargs["payload"] == {
            "finalization": "conflict",
            "stored_status": STATUS_REJECTED,
            "executor_outcome": "allowed",
        }
        assert "appr_a" not in data.approvals_in_progress

    @pytest.mark.asyncio
    async def test_approve_rejected_when_executor_returns_error(self):
        token = _make_token("tok-1", cap_restart="confirm")
        store = _make_store(pending=[_make_pending("appr_a", token_id="tok-1")], tokens=[token])
        data = _make_data(store)
        hass = _make_hass(data)
        view = ATMAdminApprovalApproveView()
        view.hass = hass
        request = _make_admin_request(body=b"{}")

        async def _failing_executor(name, args, tok, hass, data):
            return ({"content": [{"type": "text", "text": "Restart failed."}], "isError": True}, "denied", "restart_ha")

        with patch("custom_components.atm.admin_view.require_admin", lambda f: f), \
             patch("custom_components.atm.mcp_view.execute_approved_tool", side_effect=_failing_executor), \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await view.post(request, approval_id="appr_a")

        out = json.loads(resp.text)
        assert out["status"] == STATUS_REJECTED
        assert out["rejected_reason"] == "execution_failed"

    @pytest.mark.asyncio
    async def test_approve_cancels_when_token_revoked(self):
        # No token in store -> get_token_by_id returns None.
        store = _make_store(pending=[_make_pending("appr_a", token_id="tok-1")], tokens=[])
        data = _make_data(store)
        hass = _make_hass(data)
        view = ATMAdminApprovalApproveView()
        view.hass = hass
        request = _make_admin_request(body=b"{}")

        with patch("custom_components.atm.admin_view.require_admin", lambda f: f), \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await view.post(request, approval_id="appr_a")

        assert resp.status == 409
        # Storage should now show the approval cancelled.
        cancelled = next(r for r in store._pending if r["id"] == "appr_a")
        assert cancelled["status"] == STATUS_CANCELLED
        assert cancelled["rejected_reason"] == "token_inactive"

    @pytest.mark.asyncio
    async def test_approve_rejected_when_cap_now_deny(self):
        token = _make_token("tok-1", cap_restart="deny")
        store = _make_store(pending=[_make_pending("appr_a", token_id="tok-1")], tokens=[token])
        data = _make_data(store)
        hass = _make_hass(data)
        view = ATMAdminApprovalApproveView()
        view.hass = hass
        request = _make_admin_request(body=b"{}")

        with patch("custom_components.atm.admin_view.require_admin", lambda f: f), \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await view.post(request, approval_id="appr_a")

        assert resp.status == 409
        rejected = next(r for r in store._pending if r["id"] == "appr_a")
        assert rejected["status"] == STATUS_REJECTED
        assert rejected["rejected_reason"] == "capability_denied"

    @pytest.mark.asyncio
    async def test_approve_mesa_sentinel_cap_skips_effective_cap_recheck(self):
        from custom_components.atm.const import MESA_APPROVED_EXECUTOR, MESA_CONFIRM_CAP

        # The MESA sentinel cap is not a real token capability, so effective_cap
        # would auto-deny it. The approve path must skip that recheck and execute
        # (MESA re-validation lives inside the executor instead).
        token = _make_token("tok-1")  # no mesa cap; effective_cap would be deny
        pending = _make_pending(
            "appr_m",
            token_id="tok-1",
            cap_name=MESA_CONFIRM_CAP,
            tool_name=MESA_APPROVED_EXECUTOR,
            args={"domain": "light", "service": "turn_on", "entity_id": ["light.a"]},
        )
        store = _make_store(pending=[pending], tokens=[token])
        data = _make_data(store)
        hass = _make_hass(data)
        view = ATMAdminApprovalApproveView()
        view.hass = hass
        request = _make_admin_request(body=b"{}")

        async def _fake_executor(name, args, tok, hass, data):
            assert name == MESA_APPROVED_EXECUTOR
            return ({"content": [{"type": "text", "text": '{"success": true}'}]}, "allowed", "svc")

        with patch("custom_components.atm.admin_view.require_admin", lambda f: f), \
             patch("custom_components.atm.mcp_view.execute_approved_tool", side_effect=_fake_executor), \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await view.post(request, approval_id="appr_m")

        out = json.loads(resp.text)
        assert out["status"] == STATUS_APPROVED

    @pytest.mark.asyncio
    async def test_approve_cancels_when_kill_switch_engaged(self):
        token = _make_token("tok-1", cap_restart="confirm")
        store = _make_store(
            pending=[_make_pending("appr_a", token_id="tok-1")],
            tokens=[token],
            kill_switch=True,
        )
        data = _make_data(store)
        hass = _make_hass(data)
        view = ATMAdminApprovalApproveView()
        view.hass = hass
        request = _make_admin_request(body=b"{}")

        with patch("custom_components.atm.admin_view.require_admin", lambda f: f), \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await view.post(request, approval_id="appr_a")

        assert resp.status == 503
        cancelled = next(r for r in store._pending if r["id"] == "appr_a")
        assert cancelled["status"] == STATUS_CANCELLED
        assert cancelled["rejected_reason"] == "kill_switch"

    @pytest.mark.asyncio
    async def test_approve_idempotent_on_terminal(self):
        token = _make_token("tok-1", cap_restart="confirm")
        store = _make_store(
            pending=[_make_pending("appr_a", token_id="tok-1", status=STATUS_APPROVED)],
            tokens=[token],
        )
        data = _make_data(store)
        hass = _make_hass(data)
        view = ATMAdminApprovalApproveView()
        view.hass = hass
        request = _make_admin_request(body=b"{}")

        with patch("custom_components.atm.admin_view.require_admin", lambda f: f):
            resp = await view.post(request, approval_id="appr_a")

        out = json.loads(resp.text)
        assert out["status"] == STATUS_APPROVED  # already terminal; returned as-is

    @pytest.mark.asyncio
    async def test_approve_404_for_missing(self):
        store = _make_store(pending=[])
        data = _make_data(store)
        hass = _make_hass(data)
        view = ATMAdminApprovalApproveView()
        view.hass = hass
        request = _make_admin_request(body=b"{}")

        with patch("custom_components.atm.admin_view.require_admin", lambda f: f):
            resp = await view.post(request, approval_id="appr_missing")

        assert resp.status == 404


# ---- audit logging on resolution (regression) --------------------------------


def _make_data_real_audit(store: MagicMock) -> ATMData:
    """Like _make_data but with a real AuditLog so a malformed record() call
    (missing settings=, or an outcome outside _VALID_OUTCOMES) is exercised
    rather than swallowed by a MagicMock."""
    rate_limiter = MagicMock(spec=RateLimiter)
    audit = AuditLog(store=None, maxlen=100)
    return ATMData(
        store=store,
        rate_limiter=rate_limiter,
        audit=audit,
    )


class TestApprovalAuditLogging:
    @pytest.mark.asyncio
    async def test_reject_writes_queryable_audit_entry(self):
        store = _make_store(pending=[_make_pending("appr_a")])
        data = _make_data_real_audit(store)
        hass = _make_hass(data)
        view = ATMAdminApprovalRejectView()
        view.hass = hass
        request = _make_admin_request(body=json.dumps({"reason": "not_safe"}).encode())

        with patch("custom_components.atm.admin_view.require_admin", lambda f: f), \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await view.post(request, approval_id="appr_a")

        assert resp.status == 200
        # The entry exists and is filterable by a canonical outcome (would be
        # None if the recorded outcome were outside _VALID_OUTCOMES).
        denied = data.audit.query(outcome="denied")
        assert denied is not None
        assert any("appr_a" in e.resource for e in denied)

    @pytest.mark.asyncio
    async def test_approve_writes_allowed_audit_entry(self):
        token = _make_token("tok-1", cap_restart="confirm")
        store = _make_store(pending=[_make_pending("appr_a", token_id="tok-1")], tokens=[token])
        data = _make_data_real_audit(store)
        hass = _make_hass(data)
        view = ATMAdminApprovalApproveView()
        view.hass = hass
        request = _make_admin_request(body=b"{}")

        async def _fake_executor(name, args, tok, hass, data):
            return ({"content": [{"type": "text", "text": '{"success": true}'}]}, "allowed", "restart_ha")

        with patch("custom_components.atm.admin_view.require_admin", lambda f: f), \
             patch("custom_components.atm.mcp_view.execute_approved_tool", side_effect=_fake_executor), \
             patch("homeassistant.components.persistent_notification.async_dismiss"):
            resp = await view.post(request, approval_id="appr_a")

        assert resp.status == 200
        allowed = data.audit.query(outcome="allowed")
        assert allowed is not None
        assert any("appr_a" in e.resource for e in allowed)
