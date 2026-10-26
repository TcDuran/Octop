#!/usr/bin/env python3
"""构建期补丁:可选 prompt-caching 依赖导入失败时不阻断 Agent 创建。

deepagents 会尝试导入 langchain_aws / langchain_fireworks 的可选中间件。
在只支持 x86-64 基线的老 CPU 上,NumPy 2.5 可能因缺少 x86-64-v2 指令集
在导入 langchain_aws 时抛 RuntimeError;随后 Python 的半初始化模块还会让
重试变成 ImportError: cannot load module more than once per process。

这两个中间件都是可选能力。对当前未使用对应 provider 的 Agent,导入失败
应跳过,而不是让整个 Agent 图创建失败。
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path


def patch_source(source: str) -> str:
    """Patch both optional middleware import helpers in *source*."""
    pattern = re.compile(
        r"except ImportError as exc:\n"
        r"        if exc\.name not in \{[^}]+\}:\n"
        r"            raise\n"
        r'        logger\.debug\("([^"]+) unavailable\.", exc_info=exc\)\n'
        r"        return None\n",
    )

    def replace(match: re.Match[str]) -> str:
        label = match.group(1)
        return (
            "except (ImportError, RuntimeError) as exc:\n"
            f'        logger.debug("{label} unavailable.", exc_info=exc)\n'
            "        return None\n"
        )

    patched, count = pattern.subn(replace, source)
    if count != 2:
        sys.exit(
            "deepagents prompt-caching anchors not found or count changed "
            f"(expected 2, got {count}); review the upstream package"
        )
    return patched


def locate_prompt_caching_py() -> Path:
    spec = importlib.util.find_spec("deepagents")
    if spec is None or not spec.submodule_search_locations:
        sys.exit("deepagents is not installed; cannot locate patch target")
    package_dir = Path(list(spec.submodule_search_locations)[0])
    return package_dir / "middleware" / "_prompt_caching.py"


def main() -> None:
    target = locate_prompt_caching_py()
    source = target.read_text(encoding="utf-8")
    if "except (ImportError, RuntimeError) as exc:" in source:
        print(f"already patched, skipping: {target}")
        return
    target.write_text(patch_source(source), encoding="utf-8")
    print(f"patched: {target}")


if __name__ == "__main__":
    main()
