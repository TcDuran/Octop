"""Container probes use the same file/env configuration as the server."""

import io
import json
import os
import re
from pathlib import Path
from urllib import request

import pytest


@pytest.mark.parametrize("dockerfile", ["docker/Dockerfile", "fnos/docker/Dockerfile"])
@pytest.mark.parametrize(
    ("env_prefix", "persisted_prefix", "expected"),
    [
        (None, None, "/apps/octop"),
        (None, "/persisted", "/persisted"),
        ("/override/", None, "/override"),
        ("/override/", "/persisted", "/persisted"),
        ("", None, ""),
        ("", "/persisted", "/persisted"),
    ],
)
def test_healthcheck_uses_runtime_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dockerfile: str,
    env_prefix: str | None,
    persisted_prefix: str | None,
    expected: str,
) -> None:
    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path))
    monkeypatch.setenv("OCTOP_PORT", "18088")
    monkeypatch.delenv("OCTOP_BASE_PATH", raising=False)
    if env_prefix is not None:
        monkeypatch.setenv("OCTOP_BASE_PATH", env_prefix)
    if persisted_prefix is not None:
        (tmp_path / "env").write_text(f"OCTOP_BASE_PATH={persisted_prefix}\n", encoding="utf-8")
    (tmp_path / "config.json").write_text(
        json.dumps({"base_path": "/apps/octop", "port": 8088}), encoding="utf-8"
    )
    source = (Path(__file__).resolve().parents[2] / dockerfile).read_text(encoding="utf-8")
    match = re.search(r"HEALTHCHECK[^\n]*\\\n\s+CMD (\[[^\n]+\])", source)
    assert match is not None
    command = json.loads(match.group(1))
    assert command[:2] == ["python", "-c"]
    calls: list[tuple[str, int]] = []

    def urlopen(url: str, *, timeout: int) -> io.BytesIO:
        calls.append((url, timeout))
        return io.BytesIO(b"{}")

    monkeypatch.setattr(request, "urlopen", urlopen)
    # Inline Python has no source file; do not report the Dockerfile to coverage.
    exec(compile(command[2], f"<{dockerfile}:healthcheck>", "exec"), {})
    assert calls == [(f"http://127.0.0.1:18088{expected}/api/health", 5)]
