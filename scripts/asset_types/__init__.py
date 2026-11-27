"""按资产类型组织的检查模块：一个类型一个目录。

每个 `scripts/asset_types/<type>/` 里：
  static.py     静态检查脚本（判据生成，**不判 P0/P1**）；可独立运行：
                `python3 scripts/asset_types/<type>/static.py --package <包根> --json-out F`
  contract.md   动态检查合约（子智能体读它，占位符由 scripts/dispatch_asset.py 填充）
  tools/        该类型动态检查时用到的专用脚本

并发轴是**资产**：`probe_package.py` 逐资产调用通用检查 + 本类型的 `probe_assets()`，
用进程池并行（`--jobs`），合并后统一判级（assets/audit-checks.json）。
所以"一个包里全是同一种类型"也能并行——按类型并行才是错的轴。
"""
from __future__ import annotations

import json
from pathlib import Path

from .base import Ctx

HERE = Path(__file__).resolve().parent
CONTRACTS: dict[str, Path] = {p.name: p / 'contract.md' for p in sorted(HERE.iterdir())
                              if p.is_dir() and (p / 'contract.md').is_file()}
STATIC_SCRIPTS: dict[str, Path] = {p.name: p / 'static.py' for p in sorted(HERE.iterdir())
                                   if p.is_dir() and (p / 'static.py').is_file()}
TYPES = tuple(STATIC_SCRIPTS)


def contract_for(asset_type: str) -> Path | None:
    """该类型资产用哪份动态检查合约；未知类型回落到通用合约。"""
    if asset_type in CONTRACTS:
        return CONTRACTS[asset_type]
    generic = HERE / 'generic' / 'contract.md'
    return generic if generic.is_file() else None



def module_for(asset_type: str):
    """该类型对应的模块（`asset_types/<type>/static.py`）；未知类型返回 None。"""
    import importlib
    if asset_type not in TYPES:
        return None
    return importlib.import_module(f'{__name__}.{asset_type}.static')


def probe_asset(asset_type: str, root, assets: list, metas: dict) -> dict:
    """按资产分片调用该类型的静态检查（主进程或进程池里都能用）。"""
    mod = module_for(asset_type)
    if mod is None:
        return {}
    try:
        return mod.probe_assets(root, assets, metas) or {}
    except Exception as exc:                        # noqa: BLE001
        return {f'{asset_type}_type_check_error': f'{type(exc).__name__}: {exc}'}


def applies_to(asset_type: str) -> list[str]:
    """该类资产由哪些模块负责（含对所有资产生效的 skill readme 检查）。"""
    return [t for t in TYPES if t == asset_type] + (['skill'] if asset_type != 'skill' else [])


