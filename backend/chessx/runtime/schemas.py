"""Small formal contracts. Provider reasoning is never a field in these schemas."""

from pydantic import TypeAdapter

from chessx.domain.models import Intent

ACTION = TypeAdapter(Intent).json_schema()


def obj(properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required or list(properties),
        "additionalProperties": False,
    }


def enum(*values: str) -> dict:
    return {"type": "string", "enum": list(values)}


TEXT = {"type": "string"}
IDS = {"type": "array", "items": TEXT, "uniqueItems": True}
BOOLEAN = {"type": "boolean"}
INVITE = obj({"stay": BOOLEAN})
VOTE = obj({"approve": BOOLEAN})
TRANSFER = obj({"accept": BOOLEAN})
SURRENDER = obj({"surrender": BOOLEAN})
NARRATIVE = obj({"narrative": TEXT, "public_narrative": TEXT})
OUTCOME = obj({"winners": IDS, "losers": IDS})
TERMINAL = obj({"kind": enum("approve_release", "execute_loser")})
SETTLEMENT = obj(
    {
        "kind": enum(
            "say",
            "propose_release",
            "approve_release",
            "execute_loser",
            "pass",
            "leave",
            "accept_condition",
        ),
        "speech": TEXT,
        "loser_id": TEXT,
        "intent": ACTION,
    },
    ["kind"],
)
SETTLEMENT["$defs"] = ACTION["$defs"]
MEMORY = obj(
    {
        "memory": TEXT,
        "goals_markdown": TEXT,
        "goal_decision": enum("keep", "replace", "refine"),
        "goal_status": enum("active", "achieved", "impossible"),
        "reason_summary": TEXT,
        "source_receipt_ids": IDS,
    }
)
COMPACTION = obj(
    {
        "summary": {"type": "string", "maxLength": 1400},
        "additions": {
            "type": "array",
            "maxItems": 8,
            "items": obj(
                {
                    "kind": enum(
                        "heard_claim",
                        "observed_fact",
                        "commitment",
                        "location_report",
                        "hypothesis",
                    ),
                    "text": {"type": "string", "minLength": 1, "maxLength": 300},
                    "salience": enum("important", "normal"),
                    "source_receipt_ids": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 12,
                        "uniqueItems": True,
                        "items": {"type": "string", "minLength": 1},
                    },
                }
            ),
        },
        "goals_markdown": TEXT,
        "goal_decision": enum("keep", "replace", "refine"),
        "goal_status": enum("active", "achieved", "impossible"),
        "reason_summary": TEXT,
    }
)
BARD = obj(
    {
        "title": TEXT,
        "paragraphs": {
            "type": "array",
            "minItems": 1,
            "items": obj(
                {
                    "text": TEXT,
                    "event_refs": {"type": "array", "minItems": 1, "items": TEXT},
                    "claim_kind": enum("event_based"),
                }
            ),
        },
    }
)
