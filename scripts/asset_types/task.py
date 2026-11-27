"""task 资产专项检查：harbor 任务包（terminal-bench-science 格式）的结构与 oracle/nop 结果。"""
from __future__ import annotations

from pathlib import Path

from .base import Ctx

NAME = 'task'
APPLIES_TO = ('task',)

try:                      # harbor 任务包探测模块（与对象签同源）
    from _harbor import probe_harbor, static_issues
except Exception:         # noqa: BLE001
    probe_harbor = None
    static_issues = None


def probe(ctx: Ctx) -> dict:
    """返回 harbor_profile（结构事实）与 harbor_run_profile（运行结果，仅非 static_only）。"""
    out: dict = {}
    prof = (probe_harbor(ctx.root, ctx.assets) if probe_harbor
            else {'assets': {}, 'unregistered_task_dirs': []})
    for name, e in (prof.get('assets') or {}).items():
        e['declared_asset_type'] = (ctx.metas.get(name) or {}).get('asset_type')
    out['harbor_profile'] = prof
    return out
