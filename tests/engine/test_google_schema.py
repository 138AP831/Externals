"""Gemini rejects some JSON-Schema keywords in function declarations."""

from openjarvis.engine.cloud import _convert_tools_to_google, _google_schema


def test_strips_unsupported_keys_recursively_but_keeps_property_names():
    schema = {
        "type": "object",
        "additionalProperties": False,
        "$schema": "http://json-schema.org/draft-07/schema#",
        "properties": {
            "title": {"type": "string", "default": "x"},
            "items": {
                "type": "array",
                "items": {"type": "object", "additionalProperties": True},
            },
        },
        "required": ["title"],
    }
    out = _google_schema(schema)
    assert out == {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "items": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["title"],
    }
    # Input is not mutated.
    assert schema["additionalProperties"] is False


def test_convert_tools_applies_sanitizer():
    tools = [
        {
            "type": "function",
            "function": {
                "name": "f",
                "description": "d",
                "parameters": {"type": "object", "additionalProperties": False},
            },
        }
    ]
    assert _convert_tools_to_google(tools)[0]["parameters"] == {"type": "object"}
