"""The frontend's model types must match the models the API actually returns.

`interface Node` was declared five times across the components, `BackupHistory`
three times, `TaskLog` three times — each a hand-copied subset frozen at
whatever the server returned the day it was written. They had drifted: only two
of the five knew about `next_retry_at`, and none carried `repo_size_bytes`, so
a field the fleet list renders arrived as `any`.

They are one module now, `frontend/src/types.ts`. Hand-written, because a
generated file that nothing regenerates drifts exactly as quietly as a copied
one — so this test is what keeps them honest instead. It reads the OpenAPI
document FastAPI emits and compares field names.

Names only, not types: TypeScript and JSON Schema disagree about too much for a
type comparison to be anything but noise, and the failure that actually
happened was a field appearing on one side and not the other.
"""
import pathlib
import re

import pytest

from main import app

TYPES_TS = pathlib.Path(__file__).resolve().parent.parent.parent / "frontend" / "src" / "types.ts"

#: TypeScript interface -> the Pydantic model it mirrors.
MIRRORS = {
    "Node": "NodeResponse",
    "BackupHistory": "BackupHistoryResponse",
    "TaskLog": "TaskLogResponse",
    "TaskLogSummary": "TaskLogSummaryResponse",
    "BackupGroup": "BackupGroupResponse",
    "RetentionPolicy": "RetentionPolicySchema",
    "Exclusion": "ExclusionSchema",
    "ShardCapacity": "ShardCapacity",
    "RepositoryPeak": "RepositoryPeak",
    "NodeCapacity": "NodeCapacity",
    "StorageCeiling": "StorageCeiling",
    "RepositoryExpansion": "RepositoryExpansion",
    "RepositoryCapacity": "RepositoryCapacityResponse",
}

pytestmark = pytest.mark.skipif(
    not TYPES_TS.exists(), reason="frontend sources not present"
)


def _typescript_interfaces():
    """Field names per interface. A deliberately small parser: these are plain
    field lists, and pulling in a TS parser to read them would be worse."""
    source = TYPES_TS.read_text(encoding="utf-8")
    interfaces = {}
    for match in re.finditer(r"export interface (\w+) \{(.*?)\n\}", source, re.S):
        name, body = match.group(1), match.group(2)
        fields = set()
        for line in body.splitlines():
            line = line.strip()
            if not line or line.startswith("//") or line.startswith("*") or line.startswith("/*"):
                continue
            field = re.match(r"(\w+)\??\s*:", line)
            if field:
                fields.add(field.group(1))
        interfaces[name] = fields
    return interfaces


@pytest.fixture(scope="module")
def openapi_schemas():
    return app.openapi()["components"]["schemas"]


def test_the_parser_found_the_interfaces():
    """A guard on the guard: a rename would otherwise silently check nothing."""
    found = _typescript_interfaces()
    missing = [name for name in MIRRORS if name not in found]
    assert not missing, f"types.ts no longer declares {missing}"


@pytest.mark.parametrize("ts_name,model_name", sorted(MIRRORS.items()))
def test_frontend_type_matches_the_api_model(ts_name, model_name, openapi_schemas):
    assert model_name in openapi_schemas, (
        f"{model_name} is not in the OpenAPI document. It was renamed or is no "
        f"longer referenced by any route; update MIRRORS above."
    )

    server_fields = set(openapi_schemas[model_name].get("properties", {}))
    client_fields = _typescript_interfaces()[ts_name]

    only_server = sorted(server_fields - client_fields)
    only_client = sorted(client_fields - server_fields)

    assert not only_server and not only_client, (
        f"frontend/src/types.ts `{ts_name}` and backend `{model_name}` disagree.\n"
        f"  Returned by the API but missing from types.ts: {only_server or 'none'}\n"
        f"  Declared in types.ts but not returned:         {only_client or 'none'}\n"
        f"A field in the first list reaches the UI untyped; one in the second "
        f"is read at runtime as undefined."
    )
