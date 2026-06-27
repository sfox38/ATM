"""Tests for version_store.py."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from custom_components.atm.const import (
    MAX_VERSIONS_PER_RESOURCE,
    VERSION_STORAGE_KEY,
    VERSION_STORAGE_VERSION,
)
from custom_components.atm.version_store import VersionRecord, VersionStore


async def _rec(
    store: VersionStore,
    *,
    rt: str = "automation",
    rid: str = "a",
    action: str = "edit",
    before: dict | None = None,
    after: dict | None = None,
    **kw,
) -> VersionRecord:
    return await store.record(
        resource_type=rt, resource_id=rid, action=action,
        before=before, after=after, **kw,
    )


# ---------------------------------------------------------------------------
# Record shape and lookup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_captures_fields_and_assigns_id_and_timestamp():
    store = VersionStore()
    rec = await _rec(
        store, action="create", before=None, after={"alias": "Foo"},
        alias="Foo", token_id="tok1", token_name="Agent", request_id="req-1",
    )
    assert rec.id and len(rec.id) == 32  # uuid4 hex
    assert rec.resource_type == "automation"
    assert rec.action == "create"
    assert rec.before is None
    assert rec.after == {"alias": "Foo"}
    assert rec.alias == "Foo"
    assert rec.token_id == "tok1"
    assert rec.token_name == "Agent"
    assert rec.request_id == "req-1"
    assert rec.approved_by_user_id is None
    assert rec.timestamp  # iso string set
    assert len(store) == 1


@pytest.mark.asyncio
async def test_create_edit_delete_before_after_semantics():
    store = VersionStore()
    create = await _rec(store, action="create", before=None, after={"v": 1})
    edit = await _rec(store, action="edit", before={"v": 1}, after={"v": 2})
    delete = await _rec(store, action="delete", before={"v": 2}, after=None)
    assert create.before is None and create.after == {"v": 1}
    assert edit.before == {"v": 1} and edit.after == {"v": 2}
    assert delete.before == {"v": 2} and delete.after is None


@pytest.mark.asyncio
async def test_get_returns_record_or_none():
    store = VersionStore()
    rec = await _rec(store)
    assert store.get(rec.id) is rec
    assert store.get("does-not-exist") is None


@pytest.mark.asyncio
async def test_list_for_is_newest_first_and_scoped():
    store = VersionStore()
    a1 = await _rec(store, rt="automation", rid="a", after={"n": 1})
    b1 = await _rec(store, rt="script", rid="a", after={"n": 2})
    a2 = await _rec(store, rt="automation", rid="a", after={"n": 3})
    listed = store.list_for("automation", "a")
    assert [r.id for r in listed] == [a2.id, a1.id]  # newest first
    assert b1.id not in {r.id for r in listed}  # different resource_type
    assert store.list_for("scene", "a") == []  # no records


@pytest.mark.asyncio
async def test_list_recent_is_global_newest_first_and_capped():
    store = VersionStore()
    await _rec(store, rt="automation", rid="a", after={"n": 1})
    await _rec(store, rt="script", rid="b", after={"n": 2})
    await _rec(store, rt="scene", rid="c", after={"n": 3})
    recent = store.list_recent()
    assert [(r.resource_type, r.resource_id) for r in recent] == [
        ("scene", "c"), ("script", "b"), ("automation", "a")]
    capped = store.list_recent(2)
    assert len(capped) == 2 and capped[0].resource_id == "c"  # newest first, limited


@pytest.mark.asyncio
async def test_stored_config_is_decoupled_from_caller_dict():
    store = VersionStore()
    payload = {"alias": "Foo", "nested": {"x": 1}}
    rec = await _rec(store, action="create", after=payload)
    payload["nested"]["x"] = 999  # mutate the caller's dict after recording
    assert rec.after == {"alias": "Foo", "nested": {"x": 1}}


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_resource_fifo_eviction():
    store = VersionStore()
    ids = []
    for i in range(MAX_VERSIONS_PER_RESOURCE + 3):
        rec = await _rec(store, rid="a", after={"n": i})
        ids.append(rec.id)
    assert len(store) == MAX_VERSIONS_PER_RESOURCE
    # The three oldest were evicted; the newest cap-worth remain, newest first.
    assert [r.id for r in store.list_for("automation", "a")] == list(reversed(ids[3:]))
    for gone in ids[:3]:
        assert store.get(gone) is None


@pytest.mark.asyncio
async def test_eviction_is_per_resource():
    store = VersionStore()
    for i in range(MAX_VERSIONS_PER_RESOURCE):
        await _rec(store, rid="a", after={"n": i})
    b1 = await _rec(store, rid="b", after={"n": 0})
    b2 = await _rec(store, rid="b", after={"n": 1})
    assert len(store.list_for("automation", "a")) == MAX_VERSIONS_PER_RESOURCE
    assert [r.id for r in store.list_for("automation", "b")] == [b2.id, b1.id]
    # One more on "a" evicts only "a"'s oldest; "b" is untouched.
    await _rec(store, rid="a", after={"n": 99})
    assert len(store.list_for("automation", "a")) == MAX_VERSIONS_PER_RESOURCE
    assert len(store.list_for("automation", "b")) == 2


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_resource_type_raises():
    store = VersionStore()
    with pytest.raises(ValueError):
        await _rec(store, rt="widget")
    assert len(store) == 0


@pytest.mark.asyncio
async def test_unknown_action_raises():
    store = VersionStore()
    with pytest.raises(ValueError):
        await _rec(store, action="frobnicate")
    assert len(store) == 0


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_round_trips_through_store(hass: HomeAssistant):
    store = VersionStore(Store(hass, VERSION_STORAGE_VERSION, VERSION_STORAGE_KEY))
    a = await _rec(store, rid="a", action="create", after={"alias": "A"}, alias="A")
    b = await _rec(store, rt="helper", rid="input_boolean.x", action="edit",
                   before={"name": "x"}, after={"name": "y"})

    reloaded = VersionStore(Store(hass, VERSION_STORAGE_VERSION, VERSION_STORAGE_KEY))
    await reloaded.async_load()
    assert len(reloaded) == 2
    assert reloaded.get(a.id).after == {"alias": "A"}
    helper_versions = reloaded.list_for("helper", "input_boolean.x")
    assert [r.id for r in helper_versions] == [b.id]
    assert helper_versions[0].before == {"name": "x"}


@pytest.mark.asyncio
async def test_storage_version_mismatch_discards(hass: HomeAssistant):
    backing = Store(hass, VERSION_STORAGE_VERSION, VERSION_STORAGE_KEY)
    await backing.async_save({"version": 999, "versions": [{"id": "x"}]})
    store = VersionStore(Store(hass, VERSION_STORAGE_VERSION, VERSION_STORAGE_KEY))
    await store.async_load()
    assert len(store) == 0


@pytest.mark.asyncio
async def test_corrupt_record_is_skipped(hass: HomeAssistant):
    backing = Store(hass, VERSION_STORAGE_VERSION, VERSION_STORAGE_KEY)
    good = {
        "id": "good", "resource_type": "automation", "resource_id": "a",
        "alias": None, "action": "edit", "before": None, "after": {"v": 1},
        "token_id": None, "token_name": None, "request_id": None,
        "approved_by_user_id": None, "timestamp": "2026-06-18T00:00:00+00:00",
    }
    await backing.async_save({
        "version": VERSION_STORAGE_VERSION,
        "versions": [{"id": "bad"}, good],  # first lacks required keys
    })
    store = VersionStore(Store(hass, VERSION_STORAGE_VERSION, VERSION_STORAGE_KEY))
    await store.async_load()
    assert len(store) == 1
    assert store.get("good") is not None
    assert store.get("bad") is None


@pytest.mark.asyncio
async def test_wipe_clears_memory_and_disk(hass: HomeAssistant):
    store = VersionStore(Store(hass, VERSION_STORAGE_VERSION, VERSION_STORAGE_KEY))
    await _rec(store, rid="a")
    await store.async_wipe()
    assert len(store) == 0
    reloaded = VersionStore(Store(hass, VERSION_STORAGE_VERSION, VERSION_STORAGE_KEY))
    await reloaded.async_load()
    assert len(reloaded) == 0
