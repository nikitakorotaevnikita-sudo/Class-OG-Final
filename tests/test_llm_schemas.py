"""Tests for src/llm_schemas.py — validate schema correctness."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import jsonschema
import pytest

from llm_schemas import (
    ROUTER_L1_SCHEMA,
    ROUTER_L2_SCHEMA,
    FINAL_PICK_SCHEMA,
    PTO_CLEANUP_SCHEMA,
)


def test_router_l1_schema_is_valid_json_schema():
    jsonschema.Draft202012Validator.check_schema(ROUTER_L1_SCHEMA)


def test_router_l1_accepts_valid_response():
    valid = {
        "l1_ranked": [
            {"code": "0003", "conf": 0.7},
            {"code": "0005", "conf": 0.2},
        ],
        "reasoning": "обращение про благоустройство",
    }
    jsonschema.validate(valid, ROUTER_L1_SCHEMA)


def test_router_l1_rejects_missing_required_field():
    invalid = {"reasoning": "test"}  # missing l1_ranked
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(invalid, ROUTER_L1_SCHEMA)


def test_router_l2_accepts_valid_response():
    valid = {
        "l2_codes": ["0096", "0097"],
        "reasoning": "новостройка и благоустройство",
    }
    jsonschema.validate(valid, ROUTER_L2_SCHEMA)


def test_final_pick_accepts_valid_response():
    valid = {
        "code": "0003.0009.0097.0689",
        "confidence": 0.85,
        "reasoning": "точно благоустройство дворов",
    }
    jsonschema.validate(valid, FINAL_PICK_SCHEMA)


def test_final_pick_rejects_invalid_code_format():
    invalid = {
        "code": "not-a-code",
        "confidence": 0.85,
        "reasoning": "test",
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(invalid, FINAL_PICK_SCHEMA)


def test_pto_cleanup_accepts_valid_response():
    valid = {
        "primary_code": "0001.0002.0027.0151",
        "primary_confidence": 0.85,
        "secondary_codes": [
            {"code": "0001.0001.0011.0038", "confidence": 0.6},
        ],
        "rejected_assigned": True,
        "reasoning": "обращение про работу госорганов",
    }
    jsonschema.validate(valid, PTO_CLEANUP_SCHEMA)


def test_pto_cleanup_allows_empty_secondary():
    valid = {
        "primary_code": "0001.0002.0027.0151",
        "primary_confidence": 0.85,
        "secondary_codes": [],
        "rejected_assigned": False,
        "reasoning": "точная категория",
    }
    jsonschema.validate(valid, PTO_CLEANUP_SCHEMA)
