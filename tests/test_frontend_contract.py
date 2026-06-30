"""Frontend/backend contract drift guard for the admin token response.

The frontend's TokenRecord type (frontend_src/types.ts) is hand-mirrored from the
Python TokenRecord serializer. There was no guard, so the two could drift silently.
This test pins the serialized key set against a shared committed fixture
(tests/contract/token_record_keys.json); the matching frontend test
(frontend_src/__tests__/contract.test.ts) asserts the TS type covers the same keys.

If TokenRecord.to_dict gains or loses a field, this test fails. To resolve:
  1. regenerate tests/contract/token_record_keys.json from the serializer, and
  2. update frontend_src/types.ts + contract.test.ts to match.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from custom_components.atm.token_store import TokenRecord

_CONTRACT = os.path.join(os.path.dirname(__file__), "contract", "token_record_keys.json")


def _sample_token() -> TokenRecord:
    # pass_through=True so the conditional use_assist_exposure field is emitted,
    # giving the full superset of serialized keys.
    return TokenRecord(
        id="x", name="n", token_hash="h",
        created_at=datetime.now(timezone.utc), created_by="u", pass_through=True,
    )


def test_token_record_serializer_matches_contract_fixture():
    live_keys = sorted(_sample_token().to_dict().keys())
    with open(_CONTRACT, encoding="utf-8") as f:
        expected = sorted(json.load(f)["token_record_keys"])
    assert live_keys == expected, (
        "TokenRecord.to_dict shape drifted from the frontend contract fixture. "
        "Regenerate tests/contract/token_record_keys.json and update "
        "frontend_src/types.ts + contract.test.ts."
    )


def test_contract_fixture_covers_every_capability():
    # The cap_* fields are the most drift-prone (each new capability touches both
    # sides); assert the fixture carries the full canonical capability set.
    from custom_components.atm.const import CAPABILITY_NAMES

    with open(_CONTRACT, encoding="utf-8") as f:
        keys = set(json.load(f)["token_record_keys"])
    assert set(CAPABILITY_NAMES) <= keys
