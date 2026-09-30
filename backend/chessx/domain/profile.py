"""Immutable, canonical rule baseline independent of storage or HTTP."""

import hashlib
import json
from importlib.resources import files

_SOURCE = json.loads(
    files("chessx.domain").joinpath("rule_profile.json").read_text(encoding="utf-8")
)
CANONICAL_RULE_MANIFEST = json.dumps(
    _SOURCE, ensure_ascii=False, sort_keys=True, separators=(",", ":")
)
RULE_MANIFEST_HASH = hashlib.sha256(CANONICAL_RULE_MANIFEST.encode("utf-8")).hexdigest()


def frozen_rule_manifest() -> dict:
    # Deserialize a fresh copy; callers cannot mutate the frozen package baseline.
    return json.loads(CANONICAL_RULE_MANIFEST)


def manifest_hash(manifest: dict) -> str:
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
