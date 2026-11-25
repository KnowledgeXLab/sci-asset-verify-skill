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
# auto 选择与列表展示都按 (priority, order) 排：1=整包跑通、2=最小包跑通、3=可调用但不稳定、4=仅登记/未鉴权
ORDER: list[str] = [a.name for a in sorted(ADAPTERS, key=lambda a: (a.priority, a.order))]


def get(name: str) -> HarnessAdapter | None:
    return REGISTRY.get(name)


def available() -> list[HarnessAdapter]:
    """可被 auto 选中的适配器：装好、支持无头、且不是已知不可用；按优先级排序。"""
    return [REGISTRY[n] for n in ORDER if REGISTRY[n].auto_eligible]


def installed() -> list[HarnessAdapter]:
    """本机装了且支持无头的全部适配器（含 auto 不会选的，如未鉴权的）。"""
    return [REGISTRY[n] for n in ORDER if REGISTRY[n].resolve_bin() and REGISTRY[n].headless]


def priority_table() -> list[dict]:
    """优先级一览：给 README/CLI 展示用。"""
    return [{"name": a.name, "priority": a.priority, "verified": a.verified,
             "auto_eligible": a.auto_eligible, "installed": bool(a.resolve_bin())}
            for a in (REGISTRY[n] for n in ORDER)]


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
            "headless": a.headless, "priority": a.priority, "verified": a.verified,
        }
    return {"schema_version": "2.0.0", "preference": list(ORDER), "harnesses": harnesses}
