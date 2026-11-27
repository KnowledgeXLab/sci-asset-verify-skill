#!/usr/bin/env python3
"""environment 资产：运行环境声明 —— 静态检查（判据生成）。

用法：python3 scripts/asset_types/environment/static.py --package <包根> [--json-out F]

输出：{"type": "environment", "checks": {<键>: <结构>}, "assets": [<资产目录>...]}
判据由主进程汇总后统一判级（assets/audit-checks.json）；本脚本**不判 P0/P1**。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from asset_types.base import Ctx, collect_ctx          # noqa: E402

TYPE = "environment"


def probe(ctx: Ctx) -> dict:
    """返回 {checks 键: 结构}；无专项检查的类型返回空字典。"""
    return {}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="environment 资产：运行环境声明 静态检查")
    ap.add_argument("--package", required=True)
    ap.add_argument("--json-out")
    args = ap.parse_args(argv)
    ctx = collect_ctx(Path(args.package).expanduser().resolve())
    checks = probe(ctx)
    names = sorted(d.name for d in ctx.of_type(TYPE))
    payload = {"tool": "asset_types/environment/static.py", "type": TYPE,
               "assets": names, "checks": checks}
    if args.json_out:
        p = Path(args.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"type": TYPE, "keys": sorted(checks), "assets": len(names)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
