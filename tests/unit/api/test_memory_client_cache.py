"""Dashboard cache entries must release their independent memory resources."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from octop.api.common.memory_client import _MemoryCache


@pytest.mark.parametrize("backend", ["sqlite", "postgres", "opengauss"])
def test_replacement_eviction_and_invalidation_close_memory(monkeypatch, backend):
    memories = []

    def create(**_kwargs):
        memory = SimpleNamespace(
            _backend=SimpleNamespace(close=Mock()),
            _checkpointer=SimpleNamespace(conn=SimpleNamespace(close=Mock())),
            _checkpointer_pool=SimpleNamespace(close=Mock()),
        )
        memories.append(memory)
        return memory

    monkeypatch.setattr("octop_memory.core.Memory", create)
    monkeypatch.setattr("octop_memory.adapters.bridge.handlers.Bridge", lambda _memory: object())
    cache = _MemoryCache(max_size=2)

    def get(agent_id, fingerprint):
        return cache.get_or_open(
            agent_id, backend=backend, backend_config=None, fingerprint=fingerprint
        )[0]

    first = get("a", "first")
    assert get("a", "first") is first
    first._backend.close.assert_not_called()
    replacement = get("a", "changed")
    first._backend.close.assert_called_once()
    get("b", "second")
    get("c", "third")
    replacement._backend.close.assert_called_once()
    cache.invalidate("b")
    cache.invalidate("b")
    cache.invalidate()
    cache.invalidate()
    for memory in memories:
        memory._backend.close.assert_called_once()
        memory._checkpointer.conn.close.assert_called_once()
        memory._checkpointer_pool.close.assert_called_once()
