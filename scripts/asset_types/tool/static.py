#!/usr/bin/env python3
"""tool 资产：错误契约一致性 —— 静态检查（判据生成）。

用法：python3 scripts/asset_types/tool/static.py --package <包根> [--json-out F]

输出：{"type": "tool", "checks": {<键>: <结构>}, "assets": [<资产目录>...]}
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

TYPE = "tool"


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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="tool 资产：错误契约一致性 静态检查")
    ap.add_argument("--package", required=True)
    ap.add_argument("--json-out")
    args = ap.parse_args(argv)
    ctx = collect_ctx(Path(args.package).expanduser().resolve())
    checks = probe(ctx)
    names = sorted(d.name for d in ctx.of_type(TYPE))
    payload = {"tool": "asset_types/tool/static.py", "type": TYPE,
               "assets": names, "checks": checks}
    if args.json_out:
        p = Path(args.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"type": TYPE, "keys": sorted(checks), "assets": len(names)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
