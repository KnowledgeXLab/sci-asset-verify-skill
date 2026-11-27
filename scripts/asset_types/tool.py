"""tool 资产专项检查：声明的错误码/错误名 vs 实现里出现的（事实对比，不判对错）。"""
from __future__ import annotations

import re

from .base import Ctx, load

NAME = 'tool'
APPLIES_TO = ('tool',)

def probe_tool_contract(assets: list, metas: dict) -> dict:
    """工具类资产：声明的错误码/错误名 vs 实现里出现的，做成事实对比（不判对错）。"""
    rows = {}
    for d in assets:
        if str(metas[d.name].get('asset_type')) != 'tool':
            continue
        impl_text = ''
        for f in sorted(d.rglob('*.py')):
            impl_text += f.read_text(encoding='utf-8', errors='replace')
        decl_text = ''
        for f in sorted(list(d.rglob('*.json')) + list(d.rglob('*.md'))):
            if f.name == 'asset.json' or '.mounted-external' in f.name:
                continue
            decl_text += f.read_text(encoding='utf-8', errors='replace')
        impl_rc = sorted(set(int(m) for m in re.findall(r'(?:sys\.exit|SystemExit)\((\d)\)', impl_text)))
        impl_errs = sorted(set(re.findall(r"['\"]error['\"]\s*:\s*['\"]([a-z0-9_]+)['\"]", impl_text)))
        decl_errs = sorted(set(re.findall(r'\b([a-z]+(?:_[a-z]+)+)\b', decl_text)))
        rows[d.name] = {'impl_exit_codes': impl_rc, 'impl_error_names': impl_errs,
                        'impl_error_names_not_declared': sorted(set(impl_errs) - set(decl_errs))}
    return {'assets': dict(sorted(rows.items()))}



def probe(ctx: Ctx) -> dict:
    return {'tool_contract_profile': probe_tool_contract(ctx.assets, ctx.metas)}
