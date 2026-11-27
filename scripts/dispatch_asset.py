#!/usr/bin/env python3
"""为某个资产生成「动态检查合约」（子智能体读的那份），放在 <run>/agents/<资产>/contract.md。

主智能体在动态检查阶段按此派活：一个资产一个子智能体，每个子智能体只看自己那份合约。

用法：
  python3 scripts/dispatch_asset.py --run <run> --package <执行根> --asset <资产目录名>
  python3 scripts/dispatch_asset.py --run <run> --package <执行根> --all
  python3 scripts/dispatch_asset.py --run <run> --package <执行根> --asset a1 --dry-run

产物：
  <run>/agents/<资产>/contract.md    子智能体的唯一输入
  <run>/agents/<资产>/task.json      派活记录（类型、合约来源、路径），供汇总与追溯

退出码：0 成功；2 参数/IO 错误。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR))              # asset_types 包在仓库根
sys.path.insert(0, str(SKILL_DIR / 'scripts'))
import asset_types                                    # noqa: E402


def asset_type_of(asset_dir: Path) -> str:
    try:
        doc = json.loads((asset_dir / 'asset.json').read_text(encoding='utf-8'))
        return str(doc.get('asset_type') or '')
    except Exception:                                 # noqa: BLE001
        return ''


def render(contract: Path, *, asset: str, asset_dir: Path, package: Path, run: Path) -> str:
    text = contract.read_text(encoding='utf-8')
    values = {
        '{asset}': asset,
        '{asset_dir}': str(asset_dir),
        '{package}': str(package),
        '{run}': str(run),
        '{artifacts}': str(run / 'artifacts' / asset),
        '{skill_dir}': str(SKILL_DIR),
    }
    for k, v in values.items():
        text = text.replace(k, v)
    return text


def dispatch(run: Path, package: Path, asset: str) -> tuple[int, dict]:
    asset_dir = package / asset
    if not asset_dir.is_dir():
        return 2, {'asset': asset, 'error': f'资产目录不存在：{asset_dir}'}
    atype = asset_type_of(asset_dir)
    contract = asset_types.contract_for(atype)
    if contract is None:
        return 2, {'asset': asset, 'error': f'找不到类型 {atype!r} 的动态检查合约（asset_types/*/contract.md）'}
    out_dir = run / 'agents' / asset
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'artifacts').mkdir(exist_ok=True)
    text = render(contract, asset=asset, asset_dir=asset_dir, package=package, run=run)
    (out_dir / 'contract.md').write_text(text, encoding='utf-8')
    task = {'tool': 'dispatch_asset.py', 'asset': asset, 'asset_type': atype,
            'package': str(package), 'run': str(run),
            'contract': str(out_dir / 'contract.md'),
            'contract_source': str(contract.relative_to(SKILL_DIR)),
            'expects': str(out_dir / 'findings.json')}
    (out_dir / 'task.json').write_text(json.dumps(task, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return 0, task


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='生成单资产的动态检查合约（供子智能体执行）')
    ap.add_argument('--run', required=True)
    ap.add_argument('--package', required=True, help='执行根（副本根）')
    ap.add_argument('--asset', action='append', help='资产目录名，可重复')
    ap.add_argument('--all', action='store_true', help='为包内所有资产生成')
    ap.add_argument('--dry-run', action='store_true', help='只打印将生成的合约路径')
    ap.add_argument('--json-out')
    args = ap.parse_args(argv)

    run = Path(args.run).expanduser().resolve()
    package = Path(args.package).expanduser().resolve()
    if not package.is_dir():
        print(f'执行根不存在：{package}', file=sys.stderr)
        return 2
    assets = sorted(args.asset or [])
    if args.all:
        assets = sorted(d.name for d in package.iterdir()
                        if d.is_dir() and (d / 'asset.json').is_file() and not d.name.startswith('.'))
    if not assets:
        print('需要 --asset <名字> 或 --all', file=sys.stderr)
        return 2

    results, rc_all = [], 0
    for a in assets:
        if args.dry_run:
            contract = asset_types.contract_for(asset_type_of(package / a))
            results.append({'asset': a, 'contract': str(contract) if contract else None})
            print(f"  {a:24s} → {contract.relative_to(SKILL_DIR) if contract else '（无合约）'}")
            continue
        rc, info = dispatch(run, package, a)
        rc_all = rc_all or rc
        results.append(info)
        print(f"  {a:24s} → {info.get('contract') or info.get('error')}")
    if args.json_out:
        p = Path(args.json_out); p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({'tool': 'dispatch_asset.py', 'assets': results},
                                ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return rc_all


if __name__ == '__main__':
    raise SystemExit(main())
