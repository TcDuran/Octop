#!/usr/bin/env python3
"""构建期补丁:修复 octop-harness 的 MCP 工具参数类型降级问题(v3 递归版)。

背景:octop-harness 的 mcp_args_model() 把 MCP 工具 inputSchema 转成 Pydantic
参数模型时,类型映射只处理 integer/number/boolean 三种 JSON 类型,
`object`(嵌套对象)与 `array`(数组)一律落到 else 分支被声明为 str。
模型按降级后的参数表填参会把嵌套对象整包序列化成字符串发送,服务端
JSON Schema 校验报 "/req: 已找到 string,必须是 object" 且无法自愈。

v2 尝试(object→dict + json_schema_extra 透传内层结构)不够:LangChain 的
StructuredTool.tool_call_schema 重建字段时会丢弃 json_schema_extra,嵌套
字段结构仍然丢失。v3 改为递归生成嵌套 Pydantic 模型,内层字段进入类型
系统本身,重建不丢:

- object(有 properties)→ 递归 create_model 嵌套模型(深度上限 8 防循环)
- object(无 properties)→ dict
- array → list[items 递归类型]
- 其余 → int / float / bool / str

用法(Dockerfile 构建阶段,venv python 在 PATH 上):
    python docker/patches/patch_harness_mcp.py

幂等:已打过补丁或上游已修复时安全退出;上游源码结构变化导致锚点缺失时
以非零码退出,提示人工复核。
"""

import importlib.util
import re
import sys
from pathlib import Path

NEW_HELPER = '''def _mcp_json_type_to_py(spec: dict, model_name: str, depth: int = 0) -> Any:
    """JSON Schema 类型 → Python 类型;object/array 递归保真(嵌套字段进入
    类型系统,不依赖 json_schema_extra —— LangChain tool_call_schema 重建
    字段时会丢弃 extra 附加信息)。"""
    json_type = spec.get("type")
    if json_type == "integer":
        return int
    if json_type == "number":
        return float
    if json_type == "boolean":
        return bool
    if json_type == "array":
        items = spec.get("items")
        item_type = (
            _mcp_json_type_to_py(items, f"{model_name}Item", depth + 1)
            if isinstance(items, dict) else Any
        )
        return list[item_type]
    if json_type == "object":
        if depth >= 8 or not (spec.get("properties") or {}):
            return dict
        return mcp_args_model(model_name, spec)
    return str


'''

NEW_FUNCTION = '''def mcp_args_model(tool_name: str, input_schema: dict[str, Any]) -> type[Any]:
    """Build a Pydantic args model from an MCP tool ``inputSchema``."""
    props = input_schema.get("properties") or {}
    if not isinstance(props, dict):
        props = {}
    required = set(input_schema.get("required") or [])

    fields: dict[str, Any] = {}
    used_names: set[str] = set()
    for key, spec in props.items():
        spec_dict: dict[str, Any] = spec if isinstance(spec, dict) else {}
        py_type: Any = _mcp_json_type_to_py(spec_dict, str(key))
        desc = spec_dict.get("description")
        field_name = _pydantic_field_name(str(key), used_names)
        if field_name != key:
            field = Field(description=desc, validation_alias=key, serialization_alias=key)
        else:
            field = Field(description=desc)
        if key in required:
            fields[field_name] = (py_type, field)
        else:
            optional_field = (
                Field(default=None, description=desc, validation_alias=key, serialization_alias=key)
                if field_name != key
                else Field(default=None, description=desc)
            )
            fields[field_name] = (py_type | None, optional_field)

    model_name = re.sub(r"[^A-Za-z0-9_]", "_", tool_name)
    if not model_name or model_name[0].isdigit():
        model_name = f"Mcp_{model_name}"
    return create_model(model_name, __base__=_McpArgsBase, **fields)
'''


def locate_mcp_py() -> Path:
    spec = importlib.util.find_spec("octop_harness")
    if spec is None or not spec.submodule_search_locations:
        sys.exit("octop_harness 未安装,无法定位补丁目标")
    package_dir = Path(list(spec.submodule_search_locations)[0])
    return package_dir / "mcp.py"


def main() -> None:
    target = locate_mcp_py()
    source = target.read_text(encoding="utf-8")

    if "_mcp_json_type_to_py" in source:
        print(f"已是 v3 修复版,跳过: {target}")
        return

    pattern = re.compile(
        r"def mcp_args_model\(tool_name: str, input_schema: dict\[str, Any\]\) -> type\[Any\]:.*?"
        r"return create_model\(model_name, __base__=_McpArgsBase, \*\*fields\)\n",
        re.S,
    )
    match = pattern.search(source)
    if not match:
        sys.exit(f"mcp_args_model 锚点未找到(上游源码可能已变更,需人工复核): {target}")

    patched = source[: match.start()] + NEW_HELPER + NEW_FUNCTION + source[match.end() :]
    target.write_text(patched, encoding="utf-8")
    print(f"已修补(v3 递归版): {target}")


if __name__ == "__main__":
    main()
