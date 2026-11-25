"""Harness 适配器注册表：新增 harness = 新增一个文件 + 在 ADAPTERS 里追加一行。"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .base import HarnessAdapter
from . import claude, codex, cursor, dsh, kimi, opencode, qwen, zcode

ADAPTERS: tuple[HarnessAdapter, ...] = (
    dsh.ADAPTER, codex.ADAPTER, claude.ADAPTER, opencode.ADAPTER, cursor.ADAPTER,
    kimi.ADAPTER, qwen.ADAPTER, zcode.ADAPTER,
)

REGISTRY: dict[str, HarnessAdapter] = {a.name: a for a in ADAPTERS}
ORDER: list[str] = [a.name for a in sorted(ADAPTERS, key=lambda a: a.order)]


def get(name: str) -> HarnessAdapter | None:
    return REGISTRY.get(name)


def available() -> list[HarnessAdapter]:
    """已安装且支持无头执行的适配器，按 order 排序。"""
    return [REGISTRY[n] for n in ORDER if REGISTRY[n].resolve_bin() and REGISTRY[n].headless]


def describe_all() -> list[dict]:
    return [REGISTRY[n].describe() for n in ORDER]


def legacy_registry() -> dict:
    """兼容旧接口的 dict 形态（run_skill_agent.resolve_harness 等仍按它工作）。

    ``ASSET_VERIFY_HARNESS_REGISTRY`` 指向一个 JSON 时优先用它——仅供自测/私有分发替换注册表。
    """
    override = os.environ.get("ASSET_VERIFY_HARNESS_REGISTRY")
    if override:
        try:
            return json.loads(Path(override).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    harnesses = {}
    for name in ORDER:
        a = REGISTRY[name]
        harnesses[name] = {
            "display": a.display, "bins": list(a.bins), "argv": list(a.argv),
            "model_flag": list(a.model_flag) or None, "notes": a.note, "sage": a.sage,
            "headless": a.headless,
        }
    return {"schema_version": "2.0.0", "preference": list(ORDER), "harnesses": harnesses}
