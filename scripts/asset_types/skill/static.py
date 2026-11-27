#!/usr/bin/env python3
"""skill 资产：验收说明完整性 —— 静态检查（判据生成）。

用法：python3 scripts/asset_types/skill/static.py --package <包根> [--json-out F]

输出：{"type": "skill", "checks": {<键>: <结构>}, "assets": [<资产目录>...]}
判据由主进程汇总后统一判级（assets/audit-checks.json）；本脚本**不判 P0/P1**。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from asset_types.base import Ctx, collect_ctx, load   # noqa: E402

TYPE = "skill"


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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="skill 资产：验收说明完整性 静态检查")
    ap.add_argument("--package", required=True)
    ap.add_argument("--json-out")
    args = ap.parse_args(argv)
    ctx = collect_ctx(Path(args.package).expanduser().resolve())
    checks = probe(ctx)
    names = sorted(d.name for d in ctx.of_type(TYPE))
    payload = {"tool": "asset_types/skill/static.py", "type": TYPE,
               "assets": names, "checks": checks}
    if args.json_out:
        p = Path(args.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"type": TYPE, "keys": sorted(checks), "assets": len(names)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
