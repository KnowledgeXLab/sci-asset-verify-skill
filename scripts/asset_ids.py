#!/usr/bin/env python3
"""从资产包络生成清单「资产ID」列。

规范 v1.1 §4.1 规定包络里的 asset_id 稳定、不含版本号：
    sciasset://{project-id}/{namespace}/{asset-type}/{slug}
清单的「资产ID」列与之对应，去掉 sciasset:// 前缀、**不拼版本**（版本由清单的「版本」列表达）：
    {project-id}/{namespace}/{asset-type}/{slug}

本脚本是唯一入口：扫描资产包内的 catalog.json 与各 asset.json，产出两者的对应表，
直接写入 rows.json 的「资产ID」列，避免手工拼接出错。

用法
----
  # 只看映射
  python scripts/asset_ids.py --package <资产根目录>

  # 写回 rows.json（按「资产名称 + 版本」匹配）
  python scripts/asset_ids.py --package <资产根目录> --rows rows.json --inject

  # 校验：格式合法、无重复、存在 asset_version
  python scripts/asset_ids.py --package <资产根目录> --check

退出码：0 正常；1 --check 发现不合法/重复/缺失；2 参数或读取失败。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:                                     # 版本戳：报告与证据 JSON 都要能追溯校验 skill 版本
    from _version import stamp as _stamp
except Exception:                        # noqa: BLE001
    def _stamp(payload):
        return payload

ASSET_ID = re.compile(r'^sciasset://([A-Za-z0-9][A-Za-z0-9._-]*)/([a-z0-9][a-z0-9-]*)'
                      r'/(data|tool|model|environment|skill|task|trajectory|benchmark)/([a-z0-9][a-z0-9._-]*)$')
TYPES = 'data|tool|model|environment|skill|task|trajectory|benchmark'
LIST_ID = re.compile(r'^([A-Za-z0-9][A-Za-z0-9._-]*)/([a-z0-9][a-z0-9-]*)/(' + TYPES + r')/'
                     r'([a-z0-9][a-z0-9._-]*)$')
SKIP_PARTS = {'asset-runs', '.git', 'vendor', '__pycache__', 'node_modules'}


def load(p: Path):
    return json.loads(p.read_text(encoding='utf-8'))


def iter_assets(root: Path):
    """catalog.json 优先，其次各 asset.json；按 asset_id 去重。"""
    seen, out = set(), []
    cands = []
    cat = root / 'catalog.json'
    if cat.exists():
        cands.append(cat)
    cands += [p for p in sorted(root.rglob('asset.json'))
              if not (SKIP_PARTS & set(p.relative_to(root).parts))]
    for f in cands:
        try:
            doc = load(f)
        except Exception:  # noqa: BLE001
            continue
        items = doc if isinstance(doc, list) else (
            doc.get('assets') if isinstance(doc.get('assets'), list)
            else ([doc] if 'asset_id' in doc else []))
        for a in items:
            if not isinstance(a, dict) or 'asset_id' not in a:
                continue
            aid = a['asset_id']
            if aid in seen:
                continue
            seen.add(aid)
            out.append({'asset': a, 'file': str(f.relative_to(root))})
    return out


def list_id_of(asset: dict):
    """asset_id + asset_version → 清单资产ID；不合法时抛 ValueError 并说明原因。"""
    aid = str(asset.get('asset_id') or '')
    ver = str(asset.get('asset_version') or '')
    m = ASSET_ID.match(aid)
    if not m:
        raise ValueError(f'asset_id 不符合 §4.1：{aid!r}')
    if not ver:
        raise ValueError(f'{aid} 缺少 asset_version')
    project, ns, typ, slug = m.groups()
    if slug.endswith('-' + ver):
        raise ValueError(f'slug 以版本号结尾（{slug}），asset_id 不应包含版本号（§4.1）')
    return f'{project}/{ns}/{typ}/{slug}'


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='从资产包络生成清单「资产ID」列')
    ap.add_argument('--package', required=True, help='资产根目录')
    ap.add_argument('--rows', help='rows.json（可配合 --inject 写回）')
    ap.add_argument('--inject', action='store_true', help='把生成的资产ID 写入 rows.json')
    ap.add_argument('--out-rows', help='写回到新文件（默认原地覆盖 --rows）')
    ap.add_argument('--check', action='store_true', help='校验格式、唯一性与版本完整性')
    ap.add_argument('--json-out')
    args = ap.parse_args(argv)

    root = Path(args.package).expanduser().resolve()
    if not root.is_dir():
        print(f'资产根目录不存在：{root}', file=sys.stderr)
        return 2

    rows, problems = [], []
    for item in iter_assets(root):
        a = item['asset']
        try:
            lid = list_id_of(a)
        except ValueError as exc:
            problems.append({'asset_id': a.get('asset_id'), 'file': item['file'], 'error': str(exc)})
            lid = None
        rows.append({'asset_id': a.get('asset_id'), 'asset_name': a.get('name'),
                     'asset_type': a.get('asset_type'), 'asset_version': a.get('asset_version'),
                     'list_asset_id': lid, 'file': item['file']})

    seen = {}
    for r in rows:
        if r['list_asset_id']:
            if r['list_asset_id'] in seen:
                problems.append({'asset_id': r['asset_id'], 'error': f'清单资产ID 重复：{r["list_asset_id"]}'})
            seen[r['list_asset_id']] = True

    payload = {'tool': 'asset_ids.py', 'package': str(root), 'assets': len(rows),
               'with_list_id': sum(1 for r in rows if r['list_asset_id']),
               'problems': problems, 'rows': rows}
    if args.json_out:
        p = Path(args.json_out); p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(_stamp(payload), ensure_ascii=False, indent=2), encoding='utf-8')

    for r in rows:
        print(f"  {str(r['asset_name'])[:28]:28s} {str(r['asset_type']):12s} "
              f"{str(r['list_asset_id']) or '—（' + str(problems and problems[0]['error']) + '）'}")
    print(f"共 {len(rows)} 个资产，{payload['with_list_id']} 个可生成清单资产ID，问题 {len(problems)} 条")
    for p in problems:
        print(f"  ! {p['asset_id']}: {p['error']}", file=sys.stderr)

    if args.rows:
        rp = Path(args.rows).expanduser().resolve()
        doc = load(rp)
        rws = doc.get('rows') if isinstance(doc, dict) else doc
        if not isinstance(rws, list):
            print(f'rows.json 结构不认识：{rp}', file=sys.stderr)
            return 2
        by_key = {}
        for r in rows:
            if r['list_asset_id']:
                by_key[(str(r['asset_name']), str(r['asset_version']))] = r['list_asset_id']
        hit = miss = 0
        for w in rws:
            key = (str(w.get('资产名称') or ''), str(w.get('版本') or ''))
            if key in by_key:
                w['资产ID'] = by_key[key]
                hit += 1
            else:
                miss += 1
                print(f"  ! rows 中的资产在包内找不到对应包络：{key}", file=sys.stderr)
        print(f'rows 匹配 {hit} 行，未匹配 {miss} 行')
        if args.inject and not args.check:
            target = Path(args.out_rows).expanduser().resolve() if args.out_rows else rp
            target.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding='utf-8')
            print(f'已写入：{target}')
        elif args.inject and args.check:
            print('--check 模式下不写回', file=sys.stderr)

    incomplete = bool(problems) or bool(args.rows and miss)
    if args.check:
        print('校验通过' if not incomplete else f'校验未通过：{len(problems)} 条问题')
        return 0 if not incomplete else 1
    if args.inject and incomplete:
        print(f'警告：{len(problems)} 个资产的 asset_id 不合规，清单「资产ID」列未能全部写入', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
