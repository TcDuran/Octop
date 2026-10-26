#!/usr/bin/env python3
"""Patch octop-harness MCP schemas to preserve nested object fields.

octop-harness 1.0.5 already preserves enums and arrays, but represents an
object as ``dict[str, Any]``. LangChain then drops the object's nested
properties while rebuilding ``tool_call_schema``. This patch keeps the
upstream enum/array/null handling and changes only objects with properties to
recursive Pydantic models, so nested REST request bodies remain visible to the
LLM and are sent as JSON objects.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

MARKER = "_octop_recursive_mcp_schema_type"

NEW_SCHEMA_HELPER = '''def _octop_recursive_mcp_schema_type(
    schema: dict[str, Any], model_name: str, depth: int = 0
) -> Any:
    """Keep upstream enum/array handling and model nested objects recursively."""
    enum = schema.get("enum")
    if isinstance(enum, list) and enum and all(isinstance(v, str | int | bool) for v in enum):
        literal: Any = Literal
        return literal[tuple(enum)]
    kind = schema.get("type")
    if not isinstance(kind, str):
        return Any
    if kind == "object":
        properties = schema.get("properties")
        if depth >= 8 or not isinstance(properties, dict) or not properties:
            return dict[str, Any]
        return mcp_args_model(model_name, schema, _depth=depth)
    if kind == "array":
        items = schema.get("items")
        item_type = (
            _octop_recursive_mcp_schema_type(items, f"{model_name}_item", depth + 1)
            if isinstance(items, dict)
            else Any
        )
        return list[item_type]
    return {"string": str, "integer": int, "number": float, "boolean": bool}.get(kind, Any)


'''

NEW_ARGS_FUNCTION = '''def mcp_args_model(
    tool_name: str, input_schema: dict[str, Any], *, _depth: int = 0
) -> type[Any]:
    """Build an MCP args model while retaining nested object properties."""
    props = input_schema.get("properties") or {}
    if not isinstance(props, dict):
        props = {}
    required = set(input_schema.get("required") or [])
    model_name = re.sub(r"[^A-Za-z0-9_]", "_", tool_name)
    if not model_name or model_name[0].isdigit():
        model_name = f"Mcp_{model_name}"

    fields: dict[str, Any] = {}
    used_names: set[str] = set()
    for key, spec in props.items():
        spec_dict: dict[str, Any] = spec if isinstance(spec, dict) else {}
        py_type = _octop_recursive_mcp_schema_type(
            spec_dict, f"{model_name}_{key}", _depth + 1
        )
        desc = spec_dict.get("description")
        field_name = _pydantic_field_name(str(key), used_names)
        if field_name != key:
            field = Field(
                description=desc,
                validation_alias=key,
                serialization_alias=key,
                json_schema_extra=spec_dict,
            )
        else:
            field = Field(description=desc, json_schema_extra=spec_dict)
        if key in required:
            fields[field_name] = (py_type, field)
        else:
            optional_field = (
                Field(
                    default=None,
                    description=desc,
                    validation_alias=key,
                    serialization_alias=key,
                    json_schema_extra=spec_dict,
                )
                if field_name != key
                else Field(default=None, description=desc, json_schema_extra=spec_dict)
            )
            fields[field_name] = (py_type | None, optional_field)

    return create_model(model_name, __base__=_McpArgsBase, **fields)
'''


def locate_mcp_py() -> Path:
    spec = importlib.util.find_spec("octop_harness")
    if spec is None or not spec.submodule_search_locations:
        sys.exit("octop_harness 未安装，无法定位补丁目标")
    package_dir = Path(list(spec.submodule_search_locations)[0])
    return package_dir / "mcp.py"


def main() -> None:
    target = locate_mcp_py()
    source = target.read_text(encoding="utf-8")
    if MARKER in source:
        print(f"已是递归 MCP schema 修复版，跳过: {target}")
        return

    helper_pattern = re.compile(
        r"def _schema_value_type\(schema: dict\[str, Any\]\) -> Any:.*?\n\ndef mcp_args_model",
        re.S,
    )
    helper_match = helper_pattern.search(source)
    if not helper_match:
        sys.exit("_schema_value_type 锚点未找到，上游源码可能已变更，请人工复核")

    function_pattern = re.compile(
        r"def mcp_args_model\(tool_name: str, input_schema: dict\[str, Any\]\) -> type\[Any\]:.*?"
        r"return create_model\(model_name, __base__=_McpArgsBase, \*\*fields\)\n",
        re.S,
    )
    function_match = function_pattern.search(source, helper_match.start())
    if not function_match:
        sys.exit("mcp_args_model 锚点未找到，上游源码可能已变更，请人工复核")

    source = (
        source[: helper_match.start()]
        + NEW_SCHEMA_HELPER
        + source[helper_match.end() : function_match.start()]
        + NEW_ARGS_FUNCTION
        + source[function_match.end() :]
    )
    target.write_text(source, encoding="utf-8")
    print(f"已修补递归 MCP schema（保留官方 enum/array 逻辑）: {target}")


if __name__ == "__main__":
    main()
