"""skill 资产专项检查：`validate/readme.md` 是否写清 Agent / 测试任务 / 通过门槛三段。

注：`task` 类资产也常带这份说明，故本模块对所有带 readme 的资产生效（口径与拆分前一致）。
"""
from __future__ import annotations

import re

from .base import Ctx, load

NAME = 'skill'
APPLIES_TO = None            # 全部资产都看 readme

def probe_validate_readme(assets: list, metas: dict) -> dict:
    rows = {}
    for d in assets:
        f = d / 'validate' / 'readme.md'
        if not f.is_file():
            rows[d.name] = {'present': False, 'is_skill': str((metas.get(d.name) or {}).get('asset_type')) == 'skill'}
            continue
        text = f.read_text(encoding='utf-8', errors='replace')
        heads = [h.strip().lower() for h in re.findall(r'^#{2,4}\s*(.+)$', text, re.M)]
        rows[d.name] = {'present': True,
                        'is_skill': str((metas.get(d.name) or {}).get('asset_type')) == 'skill',
                        'has_agent_section': any(k in h for h in heads for k in ('agent', 'harness')),
                        'has_task_section': any(k in h for h in heads for k in ('任务', '怎么测')),
                        'has_gate_section': any(k in h for h in heads for k in ('门槛', '验收')),
                        'mentions_artifact': 'artifact' in text.lower() or '产物' in text}
    return {'assets': dict(sorted(rows.items())),
            'complete': sorted(k for k, v in rows.items()
                               if v.get('present') and v.get('has_agent_section')
                               and v.get('has_task_section') and v.get('has_gate_section'))}



def probe(ctx: Ctx) -> dict:
    return {'validate_readme_profile': probe_validate_readme(ctx.assets, ctx.metas)}
