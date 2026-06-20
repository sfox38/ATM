"""ProfileStore: the central profile storage interface (Module Proposal 4.2).

Key scheme: entity profiles are stored under their entity ID. Domain- and
area-level profiles and deployment defaults use reserved ``__``-prefixed keys,
which never collide with HA entity IDs.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from custom_components.atm.mesa_core.backends import StorageBackend
from custom_components.atm.mesa_core.exceptions import InvalidCursorError, MesaValidationError
from custom_components.atm.mesa_core.profile import (
    ORIGIN_AUTHORITY,
    ControlMode,
    MetadataOrigin,
    SemanticProfile,
    TriggersAutomations,
    baseline_triggers_automations,
)

if TYPE_CHECKING:
    from custom_components.atm.mesa_core.inheritance import InheritanceResolver

_DEPLOYMENT_DEFAULTS_KEY = "__deployment_defaults__"
_DOMAIN_PREFIX = "__domain__:"
_INTEGRATION_PREFIX = "__integration__:"
_AREA_PREFIX = "__area__:"

MAX_PAGE_SIZE = 200


@dataclass
class DeploymentDefaults:
    """Operator-configured defaults for unprofiled entities (Spec 5.8)."""

    default_control_mode: ControlMode = ControlMode.CONFIRM
    triggers_automations_domains: list[str] = field(default_factory=list)
    domain_overrides: dict[str, dict[str, str]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeploymentDefaults:
        inner = data.get("deployment_defaults", data)
        return cls(
            default_control_mode=ControlMode(inner.get("default_control_mode", "confirm")),
            triggers_automations_domains=list(inner.get("triggers_automations_domains") or []),
            domain_overrides=dict(inner.get("domain_overrides") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "deployment_defaults": {
                "default_control_mode": self.default_control_mode.value,
                "triggers_automations_domains": list(self.triggers_automations_domains),
                "domain_overrides": dict(self.domain_overrides),
            }
        }

    def control_mode_for(self, domain: str) -> ControlMode:
        override = self.domain_overrides.get(domain, {})
        if "control_mode" in override:
            return ControlMode(override["control_mode"])
        return self.default_control_mode

    def triggers_for(self, domain: str) -> TriggersAutomations:
        override = self.domain_overrides.get(domain, {})
        if "triggers_automations" in override:
            return TriggersAutomations(override["triggers_automations"])
        if domain in self.triggers_automations_domains:
            return TriggersAutomations.LIKELY
        return baseline_triggers_automations(domain)


@dataclass
class QueryRow:
    """One query match: the stored profile and its resolved effective profile.

    ``effective`` is populated for every returned row; it is None only for
    intermediate matches that were filtered out before the page was resolved.
    """

    entity_id: str
    stored: SemanticProfile
    effective: SemanticProfile | None = None


@dataclass
class ProfileQueryResult:
    rows: list[QueryRow]
    total_matched: int
    has_more: bool
    next_cursor: str | None
    limit: int = 50
    warnings: list[str] = field(default_factory=list)

    @property
    def profiles(self) -> list[SemanticProfile]:
        """The effective profile of each returned row (stored if unresolved)."""
        return [row.effective or row.stored for row in self.rows]


def _encode_cursor(offset: int, fingerprint: str) -> str:
    payload = json.dumps({"o": offset, "f": fingerprint})
    return base64.urlsafe_b64encode(payload.encode()).decode()


def _decode_cursor(cursor: str, fingerprint: str) -> int:
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        offset = int(payload["o"])
        cursor_fp = str(payload["f"])
    except Exception as err:
        raise InvalidCursorError(f"malformed cursor: {cursor!r}") from err
    if cursor_fp != fingerprint:
        # Profile data changed since the cursor was issued (Spec 9.2): restart pagination.
        raise InvalidCursorError("cursor invalidated by profile changes")
    if offset < 0:
        raise InvalidCursorError("malformed cursor offset")
    return offset


class ProfileStore:
    """Read/write MESA profiles keyed by entity, domain, or area identifiers."""

    def __init__(
        self,
        backend: StorageBackend,
        *,
        get_entity_area: Callable[[str], str | None] | None = None,
        get_entity_integration: Callable[[str], str | None] | None = None,
    ) -> None:
        self.backend = backend
        self.get_entity_area = get_entity_area
        self.get_entity_integration = get_entity_integration
        self._resolver: InheritanceResolver | None = None

    # -- entity profiles ------------------------------------------------------

    @staticmethod
    def _stamped_doc(profile: SemanticProfile) -> dict[str, Any]:
        """Serialise a profile, stamping metadata_origin when the document lacks it.

        Level 2 implementations MUST include metadata_origin in everything they
        write (Spec Section 2); without this, a location-defaulted origin (e.g.
        developer, from a sidecar import) would degrade to unknown on reload.
        """
        doc = profile.to_dict()
        sp = doc.setdefault("semantic_profile", {})
        if "metadata_origin" not in sp:
            sp["metadata_origin"] = {"source": profile.metadata.source.value}
        return doc

    def get(self, entity_id: str) -> SemanticProfile | None:
        data = self.backend.read(entity_id)
        if data is None:
            return None
        return SemanticProfile.from_dict(entity_id, data)

    def set(self, entity_id: str, profile: SemanticProfile) -> None:
        self.backend.write(entity_id, self._stamped_doc(profile))

    def delete(self, entity_id: str) -> None:
        self.backend.delete(entity_id)

    def set_many(self, profiles: dict[str, SemanticProfile]) -> None:
        for entity_id, profile in profiles.items():
            self.set(entity_id, profile)

    def delete_many(self, entity_ids: list[str]) -> None:
        for entity_id in entity_ids:
            self.delete(entity_id)

    # -- domain / area profiles -----------------------------------------------

    def get_domain_profile(self, domain: str) -> SemanticProfile | None:
        data = self.backend.read(f"{_DOMAIN_PREFIX}{domain}")
        if data is None:
            return None
        profile = SemanticProfile.from_dict(domain, data)
        profile.inheritance_scope = "domain"
        return profile

    def set_domain_profile(self, domain: str, profile: SemanticProfile) -> None:
        self.backend.write(f"{_DOMAIN_PREFIX}{domain}", self._stamped_doc(profile))

    def delete_domain_profile(self, domain: str) -> None:
        self.backend.delete(f"{_DOMAIN_PREFIX}{domain}")

    def get_integration_profile(self, integration: str) -> SemanticProfile | None:
        data = self.backend.read(f"{_INTEGRATION_PREFIX}{integration}")
        if data is None:
            return None
        profile = SemanticProfile.from_dict(integration, data)
        profile.inheritance_scope = "integration"
        return profile

    def set_integration_profile(self, integration: str, profile: SemanticProfile) -> None:
        self.backend.write(f"{_INTEGRATION_PREFIX}{integration}", self._stamped_doc(profile))

    def delete_integration_profile(self, integration: str) -> None:
        self.backend.delete(f"{_INTEGRATION_PREFIX}{integration}")

    def get_area_profile(self, area_id: str) -> SemanticProfile | None:
        data = self.backend.read(f"{_AREA_PREFIX}{area_id}")
        if data is None:
            return None
        profile = SemanticProfile.from_dict(area_id, data)
        profile.inheritance_scope = "area"
        return profile

    def set_area_profile(self, area_id: str, profile: SemanticProfile) -> None:
        self.backend.write(f"{_AREA_PREFIX}{area_id}", self._stamped_doc(profile))

    def delete_area_profile(self, area_id: str) -> None:
        self.backend.delete(f"{_AREA_PREFIX}{area_id}")

    # -- deployment defaults ----------------------------------------------------

    def get_deployment_defaults(self) -> DeploymentDefaults | None:
        data = self.backend.read(_DEPLOYMENT_DEFAULTS_KEY)
        return DeploymentDefaults.from_dict(data) if data is not None else None

    def set_deployment_defaults(self, defaults: DeploymentDefaults | dict[str, Any]) -> None:
        if isinstance(defaults, dict):
            defaults = DeploymentDefaults.from_dict(defaults)
        self.backend.write(_DEPLOYMENT_DEFAULTS_KEY, defaults.to_dict())

    # -- queries ----------------------------------------------------------------

    def entity_keys(self) -> list[str]:
        return [k for k in self.backend.list_keys() if not k.startswith("__")]

    def domain_keys(self) -> list[str]:
        """Domain names that have a domain-level profile stored."""
        return [k[len(_DOMAIN_PREFIX) :] for k in self.backend.list_keys(_DOMAIN_PREFIX)]

    def integration_keys(self) -> list[str]:
        """Integration names that have an integration-level profile stored."""
        return [k[len(_INTEGRATION_PREFIX) :] for k in self.backend.list_keys(_INTEGRATION_PREFIX)]

    def area_keys(self) -> list[str]:
        """Area IDs that have an area-level profile stored."""
        return [k[len(_AREA_PREFIX) :] for k in self.backend.list_keys(_AREA_PREFIX)]

    def find_orphans(self, known_entity_ids: Iterable[str]) -> list[str]:
        """Stored entity profile keys absent from the deployment's entity registry.

        Hosts SHOULD run this at startup and on entity registry updates and
        surface results to the operator (Spec 5.5, entity renames).
        """
        known = set(known_entity_ids)
        return [k for k in self.entity_keys() if k not in known]

    def _fingerprint(self) -> str:
        digest = hashlib.sha256("|".join(self.entity_keys()).encode())
        return digest.hexdigest()[:16]

    def query(
        self,
        *,
        domains: list[str] | None = None,
        tags: list[str] | None = None,
        tags_match: str = "any",
        areas: list[str] | None = None,
        intents: list[str] | None = None,
        include_inferred: bool = False,
        origin: str | None = None,
        min_origin_authority: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
        resolver: InheritanceResolver | None = None,
    ) -> ProfileQueryResult:
        """Query entity profiles with filtering and pagination (Spec 9.2).

        Tag and intent filters match the effective (resolved) tag set per Spec
        9.2, so resolution is used; the resolver defaults to this store's. Cheap
        attribute filters (domain, origin, area) run first and resolution is
        deferred to the survivors, or to just the returned page when neither a
        tag nor an intent filter is present.

        ``include_inferred=False`` excludes ``inferred_ai`` and ``unknown``
        origins (Spec 5.4 Rule 5) unless an explicit ``origin`` filter asks for
        them. Raises ValueError for malformed filter arguments and
        InvalidCursorError for a stale or malformed cursor.
        """
        if tags_match not in ("any", "all"):
            raise ValueError(f"invalid tags_match: {tags_match!r}")
        origin_filter = MetadataOrigin(origin) if origin is not None else None
        min_authority: int | None = None
        if min_origin_authority is not None:
            try:
                min_authority = ORIGIN_AUTHORITY[MetadataOrigin(min_origin_authority)]
            except ValueError as err:
                raise ValueError(
                    f"invalid min_origin_authority: {min_origin_authority!r}"
                ) from err

        resolver = resolver or self._default_resolver()
        get_area = resolver.get_entity_area
        if areas and get_area is None:
            raise ValueError("areas filter requires the get_entity_area callback")

        limit = max(1, min(limit, MAX_PAGE_SIZE))
        filter_needs_resolution = bool(tags or intents)
        warnings: list[str] = []
        matched: list[QueryRow] = []
        for key in self.entity_keys():
            try:
                stored = self.get(key)
            except MesaValidationError as err:
                warnings.append(f"skipped malformed profile {key}: {err}")
                continue
            if stored is None:
                continue
            if domains and stored.domain not in domains:
                continue
            source = stored.metadata.source
            if origin_filter is not None:
                if source != origin_filter:
                    continue
            elif not include_inferred and source in (
                MetadataOrigin.INFERRED_AI,
                MetadataOrigin.UNKNOWN,
            ):
                continue
            if min_authority is not None and ORIGIN_AUTHORITY[source] < min_authority:
                continue
            if areas and get_area is not None and get_area(key) not in areas:
                continue
            effective: SemanticProfile | None = None
            if filter_needs_resolution:
                effective = resolver.resolve(key, entity_profile=stored)
                effective_tags = set(effective.semantic_tags)
                if tags:
                    if tags_match == "all" and not set(tags) <= effective_tags:
                        continue
                    if tags_match == "any" and not set(tags) & effective_tags:
                        continue
                if intents:
                    routing = (
                        stored.raw.get("semantic_profile", {}).get("semantic_routing", {}) or {}
                    )
                    intent_tags = effective_tags | set(routing.get("intent_tags", []))
                    if not set(intents) & intent_tags:
                        continue
            matched.append(QueryRow(entity_id=key, stored=stored, effective=effective))

        matched.sort(key=lambda row: row.entity_id)
        fingerprint = self._fingerprint()
        offset = _decode_cursor(cursor, fingerprint) if cursor else 0
        page = matched[offset : offset + limit]
        has_more = offset + limit < len(matched)
        # OPT: resolve only the returned page when filtering did not already,
        # reusing the stored profile each row already holds.
        for row in page:
            if row.effective is None:
                row.effective = resolver.resolve(row.entity_id, entity_profile=row.stored)
        next_cursor = _encode_cursor(offset + limit, fingerprint) if has_more else None
        return ProfileQueryResult(
            rows=page,
            total_matched=len(matched),
            has_more=has_more,
            next_cursor=next_cursor,
            limit=limit,
            warnings=warnings,
        )

    # -- effective profiles -------------------------------------------------------

    def attach_resolver(self, resolver: InheritanceResolver) -> None:
        self._resolver = resolver

    def _default_resolver(self) -> InheritanceResolver:
        if self._resolver is None:
            from custom_components.atm.mesa_core.inheritance import InheritanceResolver

            self._resolver = InheritanceResolver(store=self)
        return self._resolver

    def get_effective(self, entity_id: str) -> SemanticProfile:
        """Resolve the effective profile (inheritance + conflict rules, Spec 5.6/5.7).

        Without an attached resolver, a default resolver over this store is used:
        domain inheritance derives from the entity ID prefix and area inheritance
        requires the host's ``get_entity_area`` callback.
        """
        return self._default_resolver().resolve(entity_id)

    # -- async variants -------------------------------------------------------------

    async def aget(self, entity_id: str) -> SemanticProfile | None:
        return await asyncio.to_thread(self.get, entity_id)

    async def aset(self, entity_id: str, profile: SemanticProfile) -> None:
        await asyncio.to_thread(self.set, entity_id, profile)

    async def adelete(self, entity_id: str) -> None:
        await asyncio.to_thread(self.delete, entity_id)

    async def adelete_domain_profile(self, domain: str) -> None:
        await asyncio.to_thread(self.delete_domain_profile, domain)

    async def adelete_area_profile(self, area_id: str) -> None:
        await asyncio.to_thread(self.delete_area_profile, area_id)

    async def aset_many(self, profiles: dict[str, SemanticProfile]) -> None:
        await asyncio.to_thread(self.set_many, profiles)

    async def adelete_many(self, entity_ids: list[str]) -> None:
        await asyncio.to_thread(self.delete_many, entity_ids)

    async def aquery(self, **kwargs: Any) -> ProfileQueryResult:
        return await asyncio.to_thread(lambda: self.query(**kwargs))

    async def aget_effective(self, entity_id: str) -> SemanticProfile:
        return await asyncio.to_thread(self.get_effective, entity_id)

    async def afind_orphans(self, known_entity_ids: Iterable[str]) -> list[str]:
        return await asyncio.to_thread(self.find_orphans, known_entity_ids)
