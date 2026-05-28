"""JSON schemas for constrained decoding via vLLM response_format.

Use these schemas with Ario/vLLM API:
    response_format = {"type": "json_schema", "json_schema": {"name": "...", "schema": SCHEMA, "strict": True}}

Schemas guarantee LLM output structure — no parsing of malformed JSON needed.
"""

from __future__ import annotations

# Code pattern: 4 dot-separated 4-digit groups (e.g., "0003.0009.0097.0689")
# Optional 5th group for L5 codes
CODE_PATTERN = r"^[0-9]{4}(\.[0-9]{4}){3,4}$"

# L1 code: 4-digit single group (e.g., "0003")
L1_CODE_PATTERN = r"^[0-9]{4}$"

# L2 code: 4-digit group OR full 0001.0001 form
L2_CODE_PATTERN = r"^[0-9]{4}(\.[0-9]{4})?$"


ROUTER_L1_SCHEMA = {
    "type": "object",
    "properties": {
        "l1_ranked": {
            "type": "array",
            "minItems": 1,
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "pattern": L1_CODE_PATTERN},
                    "conf": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                },
                "required": ["code", "conf"],
                "additionalProperties": False,
            },
        },
        "reasoning": {"type": "string", "maxLength": 500},
    },
    "required": ["l1_ranked"],
    "additionalProperties": False,
}


ROUTER_L2_SCHEMA = {
    "type": "object",
    "properties": {
        "l2_codes": {
            "type": "array",
            "minItems": 1,
            "maxItems": 10,
            "items": {"type": "string", "pattern": L2_CODE_PATTERN},
        },
        "reasoning": {"type": "string", "maxLength": 500},
    },
    "required": ["l2_codes"],
    "additionalProperties": False,
}


FINAL_PICK_SCHEMA = {
    "type": "object",
    "properties": {
        "code": {"type": "string", "pattern": CODE_PATTERN},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "reasoning": {"type": "string", "maxLength": 1000},
    },
    "required": ["code", "confidence"],
    "additionalProperties": False,
}


PTO_CLEANUP_SCHEMA = {
    "type": "object",
    "properties": {
        "primary_code": {"type": "string", "pattern": CODE_PATTERN},
        "primary_confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "secondary_codes": {
            "type": "array",
            "maxItems": 10,
            "items": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "pattern": CODE_PATTERN},
                    "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                },
                "required": ["code", "confidence"],
                "additionalProperties": False,
            },
        },
        "rejected_assigned": {"type": "boolean"},
        "reasoning": {"type": "string", "maxLength": 1000},
    },
    "required": ["primary_code", "primary_confidence", "secondary_codes", "rejected_assigned"],
    "additionalProperties": False,
}
