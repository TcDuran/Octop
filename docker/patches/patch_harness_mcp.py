#!/usr/bin/env python3
"""构建期补丁:修复 octop-harness 的 MCP 工具参数类型降级问题。

背景:octop-harness 的 mcp_args_model() 把 MCP 工具 inputSchema 转成 Pydantic
参数模型时,类型映射只处理 integer/number/boolean 三种 JSON 类型,
`object`(嵌套对象)与 `array`(数组)一律落到 else 分支被声明为 str。
模型按降级后的参数表填参会把嵌套对象整包序列化成字符串发送,服务端
JSON Schema 校验报 "/req: 已找到 string,必须是 object" 且无法自愈。

修复:补上 object→dict、array→list 两个分支。字段级的完整校验由
MCP server 端的 JSON Schema 校验兜底,这里只需保证类型声明不降级。

用法(Dockerfile 构建阶段,venv python 在 PATH 上):
    python docker/patches/patch_harness_mcp.py

幂等:已打过补丁或上游已修复时安全退出;上游源码结构变化导致锚点
缺失时以非零码退出,提示人工复核。
"""

import importlib.util
import sys
from pathlib import Path

OLD = """        elif json_type == "boolean":
            py_type = bool
        else:
            py_type = str"""

NEW = """        elif json_type == "boolean":
            py_type = bool
        elif json_type == "object":
            py_type = dict
        elif json_type == "array":
            py_type = list
        else:
            py_type = str"""


def locate_mcp_py() -> Path:
    spec = importlib.util.find_spec("octop_harness")
    if spec is None or not spec.submodule_search_locations:
        sys.exit("octop_harness 未安装,无法定位补丁目标")
    package_dir = Path(list(spec.submodule_search_locations)[0])
    return package_dir / "mcp.py"


def main() -> None:
    target = locate_mcp_py()
    source = target.read_text(encoding="utf-8")
    if NEW in source:
        print(f"已是修复版,跳过: {target}")
        return
    if OLD not in source:
        sys.exit(f"锚点未找到(上游源码可能已变更,需人工复核): {target}")
    target.write_text(source.replace(OLD, NEW), encoding="utf-8")
    print(f"已修补: {target}")


if __name__ == "__main__":
    main()
