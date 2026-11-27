#!/usr/bin/env python3
"""task 资产：harbor 任务包结构 —— 静态检查（判据生成）。

用法：python3 scripts/asset_types/task/static.py --package <包根> [--json-out F]

输出：{"type": "task", "checks": {<键>: <结构>}, "assets": [<资产目录>...]}
判据由主进程汇总后统一判级（assets/audit-checks.json）；本脚本**不判 P0/P1**。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from asset_types.base import Ctx, collect_ctx          # noqa: E402

try:                      # harbor 任务包探测（与对象签同源）
    from _harbor import probe_harbor
except Exception:         # noqa: BLE001
    probe_harbor = None

TYPE = "task"



def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="task 资产：harbor 任务包结构 静态检查")
    ap.add_argument("--package", required=True)
    ap.add_argument("--json-out")
    args = ap.parse_args(argv)
    ctx = collect_ctx(Path(args.package).expanduser().resolve())
    checks = probe(ctx)
    names = sorted(d.name for d in ctx.of_type(TYPE))
    payload = {"tool": "asset_types/task/static.py", "type": TYPE,
               "assets": names, "checks": checks}
    if args.json_out:
        p = Path(args.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"type": TYPE, "keys": sorted(checks), "assets": len(names)}, ensure_ascii=False))
    return 0




def probe_assets(root: Path, assets: list, metas: dict) -> dict:
    """按资产分片调用：只处理传入的这批资产（通常一个），返回 checks 片段。"""
    prof = (probe_harbor(root, assets) if probe_harbor
            else {'assets': {}, 'unregistered_task_dirs': []})
    for name, e in (prof.get('assets') or {}).items():
        e['declared_asset_type'] = (metas.get(name) or {}).get('asset_type')
    return {'harbor_profile': prof}


def probe(ctx: Ctx) -> dict:
    return probe_assets(ctx.root, ctx.assets, ctx.metas)


if __name__ == "__main__":
    raise SystemExit(main())
