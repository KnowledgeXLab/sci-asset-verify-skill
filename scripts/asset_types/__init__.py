"""按资产类型组织的检查模块：一个类型一个目录。

每个 `scripts/asset_types/<type>/` 里：
  static.py     静态检查脚本（判据生成，**不判 P0/P1**）；可独立运行：
                `python3 scripts/asset_types/<type>/static.py --package <包根> --json-out F`
  contract.md   动态检查合约（子智能体读它，占位符由 scripts/dispatch_asset.py 填充）
  tools/        该类型动态检查时用到的专用脚本

主进程在预审阶段：先跑通用检查（probe_package 内置），再**按包内出现的类型并行调用**
各 `static.py`，把判据合并到 checks，最后统一判级（assets/audit-checks.json）。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
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


def _run_one(t: str, use_ctx: Ctx, out_dir: Path, timeout: int) -> tuple[str, dict, str]:
    """跑一个类型的 static.py；返回 (类型, checks, 错误信息)。"""
    script = STATIC_SCRIPTS[t]
    out = out_dir / f'{t}.json'
    try:
        proc = subprocess.run([sys.executable, str(script), '--package', str(use_ctx.root),
                               '--json-out', str(out)],
                              capture_output=True, text=True, timeout=timeout)
        if out.is_file():
            doc = json.loads(out.read_text(encoding='utf-8'))
            return t, doc.get('checks') or {}, ''
        return t, {}, f'rc={proc.returncode} {(proc.stdout or proc.stderr).strip()[-200:]}'
    except Exception as exc:                       # noqa: BLE001
        return t, {}, f'{type(exc).__name__}: {exc}'


def run_static_parallel(ctx: Ctx, types: list[str] | None = None,
                        max_workers: int = 4, timeout: int = 300) -> dict:
    """并行跑各类型的静态检查并合并 checks。

    默认跑**全部**类型：即使包里没有该类型资产，也要返回空结构，
    这样 checks 的键集合稳定（缺键会让下游与回归比对都变得脆弱）。
    8 个子进程并行，总开销仍在 0.3 秒量级。
    """
    wanted = list(types or TYPES)
    if not wanted:
        return {}
    merged: dict = {}
    errors: dict = {}
    with tempfile.TemporaryDirectory(prefix='asset-types-') as td:
        out_dir = Path(td)
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(wanted)))) as pool:
            for t, checks, err in pool.map(lambda t: _run_one(t, ctx, out_dir, timeout), wanted):
                merged.update(checks or {})
                if err:
                    errors[t] = err
    if errors:
        merged['static_check_errors'] = errors
    return merged


def applies_to(asset_type: str) -> list[str]:
    """该类资产由哪些模块负责（含对所有资产生效的 skill readme 检查）。"""
    return [t for t in TYPES if t == asset_type] + (['skill'] if asset_type != 'skill' else [])


# 兼容旧接口
def run_all(ctx: Ctx) -> dict:      # pragma: no cover
    return run_static_parallel(ctx)
