"""Tests for audit.py."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from homeassistant.util.dt import utcnow

from custom_components.atm.audit import (
    MAX_QUERY_LIMIT,
    AuditEntry,
    AuditLog,
    generate_request_id,
)
from custom_components.atm.const import AUDIT_LOG_MAXLEN
from custom_components.atm.token_store import GlobalSettings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings(**overrides) -> GlobalSettings:
    defaults = dict(
        disable_all_logging=False,
        log_allowed=True,
        log_denied=True,
        log_rate_limited=True,
        log_entity_names=True,
        log_client_ip=True,
    )
    defaults.update(overrides)
    return GlobalSettings(**defaults)


def _record(
    log: AuditLog,
    *,
    outcome: str = "allowed",
    token_id: str = "tok-1",
    token_name: str = "mytoken",
    method: str = "GET",
    resource: str = "light.kitchen",
    client_ip: str = "192.168.1.1",
    pass_through: bool = False,
    settings: GlobalSettings | None = None,
    timestamp: datetime | None = None,
) -> None:
    log.record(
        request_id=generate_request_id(),
        token_id=token_id,
        token_name=token_name,
        method=method,
        resource=resource,
        outcome=outcome,
        client_ip=client_ip,
        pass_through=pass_through,
        settings=settings or _settings(),
        timestamp=timestamp,
    )


# ---------------------------------------------------------------------------
# generate_request_id()
# ---------------------------------------------------------------------------


class TestGenerateRequestId:
    def test_returns_string(self):
        assert isinstance(generate_request_id(), str)

    def test_is_valid_uuid(self):
        rid = generate_request_id()
        parsed = uuid.UUID(rid)
        assert str(parsed) == rid

    def test_each_call_returns_unique_value(self):
        ids = {generate_request_id() for _ in range(100)}
        assert len(ids) == 100


# ---------------------------------------------------------------------------
# AuditEntry
# ---------------------------------------------------------------------------


class TestAuditEntry:
    def _entry(self, **kwargs) -> AuditEntry:
        defaults = dict(
            request_id="req-1",
            timestamp=utcnow(),
            token_id="tok-1",
            token_name="mytoken",
            method="GET",
            resource="light.kitchen",
            outcome="allowed",
            client_ip="10.0.0.1",
            pass_through=False,
        )
        defaults.update(kwargs)
        return AuditEntry(**defaults)

    def test_to_dict_contains_all_fields(self):
        entry = self._entry()
        d = entry.to_dict()
        assert "request_id" in d
        assert "timestamp" in d
        assert "token_id" in d
        assert "token_name" in d
        assert "method" in d
        assert "resource" in d
        assert "outcome" in d
        assert "client_ip" in d
        assert "pass_through" in d

    def test_to_dict_timestamp_is_string(self):
        entry = self._entry()
        assert isinstance(entry.to_dict()["timestamp"], str)

    def test_to_dict_values_match(self):
        ts = utcnow()
        entry = self._entry(
            request_id="req-x",
            token_id="tok-x",
            token_name="token_x",
            method="POST",
            resource="light.living",
            outcome="denied",
            client_ip="1.2.3.4",
            pass_through=True,
            timestamp=ts,
        )
        d = entry.to_dict()
        assert d["request_id"] == "req-x"
        assert d["token_id"] == "tok-x"
        assert d["outcome"] == "denied"
        assert d["pass_through"] is True
        assert d["timestamp"] == ts.isoformat()

    def test_pass_through_defaults_to_false(self):
        entry = AuditEntry(
            request_id="r", timestamp=utcnow(), token_id="t", token_name="n",
            method="GET", resource="x", outcome="allowed", client_ip="1.1.1.1"
        )
        assert entry.pass_through is False

    def test_mesa_advisory_defaults_false_and_omitted(self):
        entry = AuditEntry(
            request_id="r", timestamp=utcnow(), token_id="t", token_name="n",
            method="GET", resource="x", outcome="allowed", client_ip="1.1.1.1"
        )
        assert entry.mesa_advisory is False
        assert "mesa_advisory" not in entry.to_dict()  # omitted when false

    def test_mesa_advisory_recorded_and_serialized(self):
        log = AuditLog()
        log.record(
            request_id="r", token_id="t", token_name="n", method="call_service",
            resource="light.kitchen", outcome="allowed", client_ip="1.1.1.1",
            settings=_settings(), mesa_advisory=True,
        )
        entry = log.query(limit=1)[0]
        assert entry.mesa_advisory is True
        assert entry.to_dict()["mesa_advisory"] is True


# ---------------------------------------------------------------------------
# AuditLog - basic recording
# ---------------------------------------------------------------------------


class TestAuditLogRecord:
    def test_record_adds_entry(self):
        log = AuditLog()
        _record(log)
        assert len(log) == 1

    def test_record_stores_correct_fields(self):
        log = AuditLog()
        ts = utcnow()
        log.record(
            request_id="req-1",
            token_id="tok-1",
            token_name="mytoken",
            method="GET",
            resource="light.kitchen",
            outcome="allowed",
            client_ip="10.0.0.1",
            pass_through=True,
            settings=_settings(),
            timestamp=ts,
        )
        entry = list(log._log)[0]
        assert entry.request_id == "req-1"
        assert entry.token_id == "tok-1"
        assert entry.token_name == "mytoken"
        assert entry.method == "GET"
        assert entry.resource == "light.kitchen"
        assert entry.outcome == "allowed"
        assert entry.client_ip == "10.0.0.1"
        assert entry.pass_through is True
        assert entry.timestamp == ts

    def test_record_uses_utcnow_when_no_timestamp(self):
        log = AuditLog()
        before = utcnow()
        _record(log)
        after = utcnow()
        entry = list(log._log)[0]
        assert before <= entry.timestamp <= after

    def test_multiple_records_accumulate(self):
        log = AuditLog()
        for _ in range(10):
            _record(log)
        assert len(log) == 10

    def test_all_four_outcomes_recordable(self):
        log = AuditLog()
        for outcome in ("allowed", "denied", "not_found", "rate_limited"):
            _record(log, outcome=outcome)
        assert len(log) == 4


# ---------------------------------------------------------------------------
# AuditLog - logging settings
# ---------------------------------------------------------------------------


class TestLoggingSettings:
    def test_disable_all_logging_blocks_all_outcomes(self):
        log = AuditLog()
        s = _settings(disable_all_logging=True)
        for outcome in ("allowed", "denied", "not_found", "rate_limited"):
            _record(log, outcome=outcome, settings=s)
        assert len(log) == 0

    def test_disable_all_logging_overrides_individual_toggles(self):
        """Even if individual toggles are on, master switch wins."""
        log = AuditLog()
        s = _settings(
            disable_all_logging=True,
            log_allowed=True,
            log_denied=True,
            log_rate_limited=True,
        )
        _record(log, outcome="allowed", settings=s)
        assert len(log) == 0

    def test_log_allowed_false_skips_allowed(self):
        log = AuditLog()
        s = _settings(log_allowed=False)
        _record(log, outcome="allowed", settings=s)
        assert len(log) == 0

    def test_log_allowed_false_still_records_denied(self):
        log = AuditLog()
        s = _settings(log_allowed=False)
        _record(log, outcome="denied", settings=s)
        assert len(log) == 1

    def test_log_denied_false_skips_denied(self):
        log = AuditLog()
        s = _settings(log_denied=False)
        _record(log, outcome="denied", settings=s)
        assert len(log) == 0

    def test_log_denied_false_skips_not_found(self):
        """not_found is controlled by the same log_denied toggle."""
        log = AuditLog()
        s = _settings(log_denied=False)
        _record(log, outcome="not_found", settings=s)
        assert len(log) == 0

    def test_log_denied_false_still_records_allowed(self):
        log = AuditLog()
        s = _settings(log_denied=False)
        _record(log, outcome="allowed", settings=s)
        assert len(log) == 1

    def test_log_rate_limited_false_skips_rate_limited(self):
        log = AuditLog()
        s = _settings(log_rate_limited=False)
        _record(log, outcome="rate_limited", settings=s)
        assert len(log) == 0

    def test_log_rate_limited_false_still_records_allowed(self):
        log = AuditLog()
        s = _settings(log_rate_limited=False)
        _record(log, outcome="allowed", settings=s)
        assert len(log) == 1

    def test_individual_toggles_independent(self):
        """Disabling one toggle does not affect the others."""
        log = AuditLog()
        s = _settings(log_allowed=False, log_denied=True, log_rate_limited=True)
        _record(log, outcome="allowed", settings=s)
        _record(log, outcome="denied", settings=s)
        _record(log, outcome="rate_limited", settings=s)
        assert len(log) == 2


# ---------------------------------------------------------------------------
# AuditLog - redaction settings
# ---------------------------------------------------------------------------


class TestRedactionSettings:
    def test_log_entity_names_false_redacts_resource(self):
        log = AuditLog()
        s = _settings(log_entity_names=False)
        _record(log, resource="light.kitchen", settings=s)
        entry = list(log._log)[0]
        assert entry.resource == "[redacted]"

    def test_log_entity_names_true_preserves_resource(self):
        log = AuditLog()
        s = _settings(log_entity_names=True)
        _record(log, resource="light.kitchen", settings=s)
        entry = list(log._log)[0]
        assert entry.resource == "light.kitchen"

    def test_log_client_ip_false_redacts_ip(self):
        log = AuditLog()
        s = _settings(log_client_ip=False)
        _record(log, client_ip="192.168.1.100", settings=s)
        entry = list(log._log)[0]
        assert entry.client_ip == "[redacted]"

    def test_log_client_ip_true_preserves_ip(self):
        log = AuditLog()
        s = _settings(log_client_ip=True)
        _record(log, client_ip="192.168.1.100", settings=s)
        entry = list(log._log)[0]
        assert entry.client_ip == "192.168.1.100"

    def test_both_redacted_simultaneously(self):
        log = AuditLog()
        s = _settings(log_entity_names=False, log_client_ip=False)
        _record(log, resource="sensor.temp", client_ip="10.0.0.1", settings=s)
        entry = list(log._log)[0]
        assert entry.resource == "[redacted]"
        assert entry.client_ip == "[redacted]"

    def test_redaction_applied_before_storage(self):
        """Original values must not be recoverable from the log after redaction."""
        log = AuditLog()
        s = _settings(log_entity_names=False)
        _record(log, resource="lock.front_door", settings=s)
        raw_entries = list(log._log)
        assert all(e.resource != "lock.front_door" for e in raw_entries)


# ---------------------------------------------------------------------------
# AuditLog - circular buffer behaviour
# ---------------------------------------------------------------------------


class TestCircularBuffer:
    def test_maxlen_is_audit_log_maxlen(self):
        log = AuditLog()
        assert log._log.maxlen == AUDIT_LOG_MAXLEN

    def test_custom_maxlen_respected(self):
        log = AuditLog(maxlen=5)
        assert log._log.maxlen == 5

    def test_overflow_evicts_oldest_entry(self):
        log = AuditLog(maxlen=3)
        for i in range(5):
            log.record(
                request_id=f"req-{i}",
                token_id="tok",
                token_name="t",
                method="GET",
                resource=f"entity.{i}",
                outcome="allowed",
                client_ip="1.1.1.1",
                settings=_settings(),
            )
        assert len(log) == 3
        entries = list(log._log)
        resources = [e.resource for e in entries]
        assert "entity.0" not in resources
        assert "entity.1" not in resources
        assert "entity.2" in resources
        assert "entity.3" in resources
        assert "entity.4" in resources

    def test_len_does_not_exceed_maxlen(self):
        log = AuditLog(maxlen=10)
        for _ in range(25):
            _record(log)
        assert len(log) == 10

    def test_clear_empties_log(self):
        log = AuditLog()
        for _ in range(5):
            _record(log)
        log.clear()
        assert len(log) == 0

    def test_clear_allows_new_records_after(self):
        log = AuditLog()
        for _ in range(5):
            _record(log)
        log.clear()
        _record(log)
        assert len(log) == 1


# ---------------------------------------------------------------------------
# AuditLog - query()
# ---------------------------------------------------------------------------


class TestQuery:
    def _populate(self, log: AuditLog) -> None:
        entries = [
            {"token_id": "tok-a", "outcome": "allowed",      "client_ip": "10.0.0.1"},
            {"token_id": "tok-a", "outcome": "denied",       "client_ip": "10.0.0.1"},
            {"token_id": "tok-b", "outcome": "allowed",      "client_ip": "10.0.0.2"},
            {"token_id": "tok-b", "outcome": "rate_limited", "client_ip": "10.0.0.2"},
            {"token_id": "tok-a", "outcome": "not_found",    "client_ip": "10.0.0.3"},
        ]
        for kwargs in entries:
            _record(log, **kwargs)

    def test_no_filters_returns_all_newest_first(self):
        log = AuditLog()
        self._populate(log)
        results = log.query()
        assert len(results) == 5
        # Newest first: last recorded entry is first in results
        assert results[0].outcome == "not_found"
        assert results[-1].outcome == "allowed"

    def test_filter_by_token_id(self):
        log = AuditLog()
        self._populate(log)
        results = log.query(token_id="tok-a")
        assert len(results) == 3
        assert all(e.token_id == "tok-a" for e in results)

    def test_filter_by_outcome(self):
        log = AuditLog()
        self._populate(log)
        results = log.query(outcome="allowed")
        assert len(results) == 2
        assert all(e.outcome == "allowed" for e in results)

    def test_filter_by_client_ip(self):
        log = AuditLog()
        self._populate(log)
        results = log.query(client_ip="10.0.0.2")
        assert len(results) == 2
        assert all(e.client_ip == "10.0.0.2" for e in results)

    def test_pending_approval_is_recordable_and_queryable(self):
        """pending_approval is a real emitted outcome (a tool gated for confirm),
        so it must be queryable rather than rejected as an unknown outcome."""
        log = AuditLog()
        _record(log, outcome="pending_approval")
        results = log.query(outcome="pending_approval")
        assert results is not None
        assert len(results) == 1
        assert results[0].outcome == "pending_approval"

    def test_combined_filters(self):
        log = AuditLog()
        self._populate(log)
        results = log.query(token_id="tok-a", outcome="allowed")
        assert len(results) == 1
        assert results[0].token_id == "tok-a"
        assert results[0].outcome == "allowed"

    def test_filter_with_no_matches_returns_empty(self):
        log = AuditLog()
        self._populate(log)
        results = log.query(token_id="tok-z")
        assert results == []

    def test_pagination_limit(self):
        log = AuditLog()
        for _ in range(10):
            _record(log)
        results = log.query(limit=3)
        assert len(results) == 3

    def test_pagination_offset(self):
        log = AuditLog()
        for i in range(5):
            _record(log, resource=f"light.{i}")
        all_results = log.query()
        offset_results = log.query(offset=2)
        assert offset_results == all_results[2:]

    def test_limit_capped_at_max_query_limit(self):
        log = AuditLog(maxlen=10000)
        for _ in range(600):
            _record(log)
        results = log.query(limit=600)
        assert len(results) == MAX_QUERY_LIMIT

    def test_empty_log_returns_empty_list(self):
        log = AuditLog()
        assert log.query() == []

    def test_query_does_not_modify_log(self):
        log = AuditLog()
        for _ in range(5):
            _record(log)
        log.query(token_id="tok-1", outcome="allowed")
        assert len(log) == 5

    def test_results_are_newest_first(self):
        log = AuditLog()
        t1 = datetime(2026, 4, 10, 10, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 4, 10, 11, 0, 0, tzinfo=timezone.utc)
        t3 = datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)
        _record(log, timestamp=t1)
        _record(log, timestamp=t2)
        _record(log, timestamp=t3)
        results = log.query()
        assert results[0].timestamp == t3
        assert results[1].timestamp == t2
        assert results[2].timestamp == t1

    def test_pagination_offset_zero_same_as_no_offset(self):
        log = AuditLog()
        for _ in range(5):
            _record(log)
        assert log.query(offset=0) == log.query()


class TestPayloadRedaction:
    """Recorded payloads must not persist secrets verbatim (redact_structure)."""

    def test_sensitive_key_value_redacted(self):
        log = AuditLog()
        log.record(
            request_id=generate_request_id(), token_id="t", token_name="n",
            method="create_helper", resource="helper", outcome="allowed",
            client_ip="1.2.3.4", settings=_settings(),
            payload={"name": "create_helper", "arguments": {"api_key": "supersecret123"}},
        )
        stored = log.query()[0].payload
        assert "supersecret123" not in stored
        assert "<redacted>" in stored

    def test_embedded_secret_line_scrubbed(self):
        log = AuditLog()
        log.record(
            request_id=generate_request_id(), token_id="t", token_name="n",
            method="set_yaml_config", resource="yaml", outcome="allowed",
            client_ip="1.2.3.4", settings=_settings(),
            payload={"name": "set_yaml_config", "arguments": {"content": "password: hunter2"}},
        )
        stored = log.query()[0].payload
        assert "hunter2" not in stored

    def test_innocuous_payload_preserved(self):
        log = AuditLog()
        log.record(
            request_id=generate_request_id(), token_id="t", token_name="n",
            method="HassTurnOn", resource="light", outcome="allowed",
            client_ip="1.2.3.4", settings=_settings(),
            payload={"name": "HassTurnOn", "arguments": {"entity_id": "light.kitchen", "brightness": 50}},
        )
        stored = log.query()[0].payload
        assert "light.kitchen" in stored
        assert "50" in stored
