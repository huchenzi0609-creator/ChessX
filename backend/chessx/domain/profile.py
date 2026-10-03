"""Immutable, canonical rule baseline independent of storage or HTTP."""

import hashlib
import json
from importlib.resources import files

SUPPORTED_RULE_PROFILES = {"demo-v2.1": "rule_profile_v2_1.json", "demo-v2.2": "rule_profile.json"}
LATEST_RULE_PROFILE = "demo-v2.2"
_SOURCE = json.loads(
    files("chessx.domain").joinpath("rule_profile.json").read_text(encoding="utf-8")
)
CANONICAL_RULE_MANIFEST = json.dumps(
    _SOURCE, ensure_ascii=False, sort_keys=True, separators=(",", ":")
)
RULE_MANIFEST_HASH = hashlib.sha256(CANONICAL_RULE_MANIFEST.encode("utf-8")).hexdigest()


def frozen_rule_manifest(profile_id: str = LATEST_RULE_PROFILE) -> dict:
    # Deserialize a fresh copy; callers cannot mutate the frozen package baseline.
    if profile_id == LATEST_RULE_PROFILE:
        return json.loads(CANONICAL_RULE_MANIFEST)
    path = SUPPORTED_RULE_PROFILES[profile_id]
    return json.loads(files("chessx.domain").joinpath(path).read_text(encoding="utf-8"))


def profile_manifest_hash(profile_id: str) -> str:
    return manifest_hash(frozen_rule_manifest(profile_id))


def is_legacy_rules(state: dict) -> bool:
    return state.get("rule_profile", "demo-v2.1") == "demo-v2.1"


def rule_parameter(state: dict, name: str, default):
    return state.get("rule_manifest", {}).get("engineering_parameters", {}).get(name, default)


def conversation_message_budget(state: dict, conversation: dict) -> int:
    if is_legacy_rules(state):
        return state["limits"]["conversation_max_messages"]
    peak = max(conversation.get("peak_members", 0), len(conversation.get("members", [])))
    return peak * 50


def manifest_hash(manifest: dict) -> str:
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
