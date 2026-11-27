#!/usr/bin/env python3
"""汇总各资产的动态检查结果（子智能体写的 findings.json），并把"自查误判"结果一并落盘。

规则
----
* 只把 `confirmed` 计入交付结论；`dismissed` 单独保留（含排除理由），进报告附录可追溯；
* 结构不合规的 findings.json 直接报错（不静默丢弃）：`level` 必须是 P0/P1、
  必须有 `summary` 与 `evidence`；`dismissed` 必须有 `why_false_positive`；
* `status=blocked` 的资产不计入通过，进 `blocked` 列表（附原因）。

用法：
  python3 scripts/collect_asset_reports.py --run <run> [--json-out F]

产物：<run>/asset-findings.json
退出码：0 全部有效；1 存在结构不合规的报告；2 参数/IO 错误。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

LEVELS = ('P0', 'P1')


def load(p: Path):
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:                                  # noqa: BLE001
        return None


def validate(doc: dict, asset: str) -> list[str]:
    errs = []
    if doc.get('asset') != asset:
        errs.append(f'asset 字段应为 {asset!r}，实际 {doc.get("asset")!r}')
    if doc.get('status') not in ('completed', 'blocked'):
        errs.append(f'status 必须是 completed/blocked，实际 {doc.get("status")!r}')
    if doc.get('status') == 'blocked' and not str(doc.get('blocked_reason') or '').strip():
        errs.append('status=blocked 必须写 blocked_reason')
    for i, f in enumerate(doc.get('confirmed') or []):
        if str(f.get('level')) not in LEVELS:
            errs.append(f'confirmed[{i}].level 必须是 P0/P1')
        for k in ('summary', 'evidence'):
            if not str(f.get(k) or '').strip():
                errs.append(f'confirmed[{i}] 缺 {k}')
    for i, f in enumerate(doc.get('dismissed') or []):
        if not str(f.get('why_false_positive') or '').strip():
            errs.append(f'dismissed[{i}] 缺 why_false_positive（自查误报必须写排除理由）')
    return errs


def collect(run: Path) -> dict:
    confirmed, dismissed, blocked, per_asset, errors = [], [], [], [], []
    usage_parts: list[str] = []
    for f in sorted((run / 'agents').glob('*/findings.json')):
        asset = f.parent.name
        doc = load(f)
        if not isinstance(doc, dict):
            errors.append({'asset': asset, 'errors': ['findings.json 不可解析']})
            continue
        errs = validate(doc, asset)
        if errs:
            errors.append({'asset': asset, 'errors': errs})
        cases = doc.get('cases') or {}
        adir = f.parent
        usage_f, deploy_f = adir / 'usage.md', adir / 'deployment.json'
        if usage_f.is_file():
            usage_parts.append(usage_f.read_text(encoding='utf-8').strip())
        elif doc.get('status') == 'completed':
            errors.append({'asset': asset, 'errors': ['status=completed 但缺 usage.md（使用说明是交付物之一）']})
        deploy = {}
        if deploy_f.is_file():
            deploy = load(deploy_f) or {}
        per_asset.append({'asset': asset, 'asset_type': doc.get('asset_type'),
                          'usage': str(usage_f.name) if usage_f.is_file() else '',
                          'deployment_status': (deploy or {}).get('status', ''),
                          'status': doc.get('status'), 'cases': cases,
                          'confirmed': len(doc.get('confirmed') or []),
                          'dismissed': len(doc.get('dismissed') or [])})
        for x in doc.get('confirmed') or []:
            confirmed.append({'asset': asset, **x})
        for x in doc.get('dismissed') or []:
            dismissed.append({'asset': asset, **x})
        if doc.get('status') == 'blocked':
            blocked.append({'asset': asset, 'reason': doc.get('blocked_reason')})
    if usage_parts:
        (run / 'usage.md').write_text('\n\n'.join(usage_parts) + '\n', encoding='utf-8')
    totals = {lv: sum(1 for c in confirmed if c.get('level') == lv) for lv in LEVELS}
    return {'tool': 'collect_asset_reports.py', 'run': str(run),
            'assets': per_asset, 'confirmed': confirmed, 'dismissed': dismissed,
            'blocked': blocked, 'errors': errors, 'totals': totals,
            'usage': str(run / 'usage.md') if usage_parts else '',
            'case_totals': {k: sum(int((a['cases'] or {}).get(k) or 0) for a in per_asset)
                            for k in ('planned', 'executed', 'passed', 'failed', 'blocked')}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='汇总各资产动态检查结果（含自查误报筛除）')
    ap.add_argument('--run', required=True)
    ap.add_argument('--json-out')
    args = ap.parse_args(argv)
    run = Path(args.run).expanduser().resolve()
    if not (run / 'agents').is_dir():
        print(f'没有 agents 目录：{run / "agents"}（先跑 dispatch_asset.py 派活）', file=sys.stderr)
        return 2
    out = collect(run)
    dest = Path(args.json_out) if args.json_out else (run / 'asset-findings.json')
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    t = out['totals']
    print(f"汇总：资产 {len(out['assets'])} 个（blocked {len(out['blocked'])}）；"
          f"确认问题 P0 {t['P0']} / P1 {t['P1']}；自查排除误报 {len(out['dismissed'])} 条 → {dest}")
    for e in out['errors']:
        print(f"  [不合规] {e['asset']}：{'；'.join(e['errors'])}", file=sys.stderr)
    return 0 if not out['errors'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
