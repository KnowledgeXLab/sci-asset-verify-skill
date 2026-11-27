"""按资产类型组织的专项检查（"子 skill"式的模块边界）。

`probe_package.py` 只负责**通用**检查（结构/包络/实体/凭据/越界/文档引用/版本/用例/入口），
各类型的专项检查放在这里：一个类型一个文件，接口统一为 `probe(ctx) -> {checks 键: 结构}`。

新增类型检查 = 加一个文件 + 在 MODULES 追加一行；`probe_package.py` 不用改。
键名保持与原实现一致（`data_declaration_profile` 等），因此对下游脚本/报告完全透明。
"""
from __future__ import annotations

from . import benchmark, data, environment, model, skill, task, tool, trajectory
from .base import Ctx

MODULES = (data, tool, skill, task, model, environment, trajectory, benchmark)


def run_all(ctx: Ctx) -> dict:
    """跑所有类型的专项检查并合并结果；某个模块出错不影响其它模块。"""
    merged: dict = {}
    for mod in MODULES:
        try:
            merged.update(mod.probe(ctx) or {})
        except Exception as exc:                     # noqa: BLE001
            merged[f'{mod.NAME}_type_check_error'] = f'{type(exc).__name__}: {exc}'
    return merged


def applies_to(asset_type: str) -> list[str]:
    """该类资产由哪些模块负责（用于文档/SKILL 按需加载）。"""
    return [m.NAME for m in MODULES if not m.APPLIES_TO or asset_type in m.APPLIES_TO]
