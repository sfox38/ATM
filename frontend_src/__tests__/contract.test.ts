import { describe, it, expect } from "vitest";
import type { TokenRecord } from "../types";
import contract from "../../tests/contract/token_record_keys.json";

// Contract drift guard between the frontend TokenRecord type and the Python
// serializer. The shared fixture is generated from TokenRecord.to_dict (see
// tests/test_frontend_contract.py). This map is typed `satisfies
// Record<keyof TokenRecord, true>`, so tsc fails if a TokenRecord field is added
// or removed without updating it; the runtime assertion then checks those keys
// equal the Python-generated fixture, catching drift on either side.
const TOKEN_RECORD_KEYS = {
  id: true,
  name: true,
  created_at: true,
  created_by: true,
  expires_at: true,
  revoked: true,
  last_used_at: true,
  updated_at: true,
  pass_through: true,
  use_assist_exposure: true,
  announce_all_tools: true,
  persona: true,
  rate_limit_requests: true,
  rate_limit_burst: true,
  permissions: true,
  cap_config_read: true,
  cap_template_render: true,
  cap_log_read: true,
  cap_search: true,
  cap_registry_read: true,
  cap_traces: true,
  cap_diagnostics: true,
  cap_broadcast: true,
  cap_service_response: true,
  cap_automation_write: true,
  cap_script_write: true,
  cap_scene_write: true,
  cap_helper_write: true,
  cap_physical_control: true,
  cap_restart: true,
  cap_integration_write: true,
  cap_lovelace_write: true,
  cap_registry_write: true,
  cap_backup: true,
  cap_filesystem: true,
  cap_yaml_edit: true,
} satisfies Record<keyof TokenRecord, true>;

describe("frontend/backend TokenRecord contract", () => {
  it("the TS type's keys match the Python serializer fixture", () => {
    expect(Object.keys(TOKEN_RECORD_KEYS).sort()).toEqual(
      [...contract.token_record_keys].sort(),
    );
  });
});
