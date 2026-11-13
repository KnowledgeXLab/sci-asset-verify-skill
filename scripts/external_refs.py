#!/usr/bin/env python3
"""校验外部资产引用文件 external_assets.json，并生成清单「资产关联」列内容。

外部资产引用是**可选**文件，放在资产包根目录：当包内某些数据因体积或授权原因不随包，
就在这里声明平台应按资产编号解析的资产。格式见《课题5 地学科学问题收集包核验报告》附录：

    {
      "schema_version": "1.0.0",
      "assets": [
        {"asset_id": "sciasset://<项目编号>/<命名空间>/<类型>/<slug>",
         "asset_version": "1.0.0",
         "target_path": "relative/path/in/package",
         "media_type": "application/x-netcdf",
         "required": true}
      ]
    }

用法
----
  python scripts/external_refs.py --package <资产根目录>                 # 只校验并打印
  python scripts/external_refs.py --package <资产根目录> --rows rows.json --inject
  python scripts/external_refs.py --package <资产根目录> --check         # 有错则退出码 1

退出码：0 正常（含"文件不存在"这一合规情形）；1 校验未通过；2 参数/IO 错误。
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

FILE = 'external_assets.json'
ASSET_ID = re.compile(r'^sciasset://([A-Za-z0-9][A-Za-z0-9._-]*)/([a-z0-9][a-z0-9-]*)/'
                      r'(data|tool|model|environment|skill|task|trajectory|benchmark)/'
                      r'([a-z0-9][a-z0-9._-]*)$')
SEMVER = re.compile(r'^[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.-]+)?$')
SKIP_PARTS = {'asset-runs', '.git', '__pycache__', 'vendor', 'node_modules'}


def err(problems, where, message):
    problems.append({'where': where, 'message': message})


def validate(doc, root: Path):
    problems, warnings = [], []
    if not isinstance(doc, dict):
        err(problems, '$', '顶层应为对象')
        return problems, warnings
    sv = doc.get('schema_version')
    if not sv:
        err(problems, 'schema_version', '缺少 schema_version')
    elif not SEMVER.match(str(sv)):
        err(problems, 'schema_version', f'应为语义化版本：{sv!r}')
    if 'task_id' in doc:
        warnings.append({'where': 'task_id', 'message': '该字段已从格式中移除，可直接删除'})
    assets = doc.get('assets')
    if not isinstance(assets, list) or not assets:
        err(problems, 'assets', 'assets 必须是非空数组')
        return problems, warnings
    seen = {}
    for i, a in enumerate(assets):
        w = f'assets[{i}]'
        if not isinstance(a, dict):
            err(problems, w, '应为对象')
            continue
        aid = a.get('asset_id')
        if not aid:
            err(problems, f'{w}.asset_id', '缺少 asset_id')
        elif not ASSET_ID.match(str(aid)):
            err(problems, f'{w}.asset_id',
                f'应为 sciasset://<项目编号>/<命名空间>/<类型>/<slug>（不含版本号）：{aid!r}')
        ver = a.get('asset_version')
        if not ver:
            err(problems, f'{w}.asset_version', '缺少 asset_version')
        elif not SEMVER.match(str(ver)):
            err(problems, f'{w}.asset_version', f'应为语义化版本：{ver!r}')
        tp = a.get('target_path')
        if not tp:
            err(problems, f'{w}.target_path', '缺少 target_path')
        else:
            tp = str(tp)
            if tp.startswith('/') or re.match(r'^[A-Za-z]:[\\/]', tp):
                err(problems, f'{w}.target_path', f'应为相对任务包根的 POSIX 路径：{tp!r}')
            elif '..' in Path(tp).parts:
                err(problems, f'{w}.target_path', f'不得包含 ..（越出包根）：{tp!r}')
            elif '\\' in tp:
                err(problems, f'{w}.target_path', f'应使用 POSIX 正斜杠：{tp!r}')
            elif not (root / tp).exists():
                warnings.append({'where': f'{w}.target_path',
                                 'message': f'包内未见该路径（若由平台解析可忽略）：{tp}'})
        if a.get('media_type') is not None and not isinstance(a.get('media_type'), str):
            err(problems, f'{w}.media_type', '应为字符串')
        if a.get('required') is not None and not isinstance(a.get('required'), bool):
            err(problems, f'{w}.required', '应为布尔值')
        key = (str(aid), str(ver))
        if key in seen:
            err(problems, w, f'重复引用同一资产同一版本：{key}')
        seen[key] = True
    return problems, warnings


def render(assets) -> str:
    """清单「资产关联」列的内容：只放 asset_id 去掉 sciasset:// 前缀，英文逗号分隔。

    清单该列只认包根 external_assets.json 声明的编号，不写依赖描述、不写资产名、没有就留空。
    """
    ids = []
    for a in assets:
        aid = str(a.get('asset_id') or '')
        if aid.startswith('sciasset://'):
            aid = aid[len('sciasset://'):]
        if aid and aid not in ids:
            ids.append(aid)
    return ','.join(ids)


def detail_text(assets) -> str:
    """给人看的明细（写进 JSON 证据，不进清单）。"""
    parts = []
    for a in assets:
        flag = 'required' if a.get('required', True) else 'optional'
        mt = f"，{a['media_type']}" if a.get('media_type') else ''
        parts.append(f"{a.get('asset_id')}（v{a.get('asset_version')}）→ {a.get('target_path')}（{flag}{mt}）")
    return '外部资产引用：' + '；'.join(parts) if parts else ''


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='校验 external_assets.json 并生成「资产关联」列')
    ap.add_argument('--package', required=True, help='资产根目录')
    ap.add_argument('--file', help=f'引用文件路径（默认 <package>/{FILE}）')
    ap.add_argument('--rows', help='rows.json（配合 --inject 写「资产关联」）')
    ap.add_argument('--inject', action='store_true')
    ap.add_argument('--out-rows')
    ap.add_argument('--check', action='store_true', help='有错时退出码 1')
    ap.add_argument('--json-out')
    args = ap.parse_args(argv)

    root = Path(args.package).expanduser().resolve()
    if not root.is_dir():
        print(f'资产根目录不存在：{root}', file=sys.stderr)
        return 2
    cands = [Path(args.file).expanduser().resolve()] if args.file else [
        p for p in [root / FILE] + sorted(root.glob(f'*/{FILE}'))
        if not (SKIP_PARTS & set(p.relative_to(root).parts))]
    target = next((p for p in cands if p.exists()), None)
    payload = {'tool': 'external_refs.py', 'package': str(root), 'file': None,
               'present': False, 'problems': [], 'warnings': [], 'assets': [], 'cell': None}
    if target is None:
        print(f'未发现 {FILE}（可选文件，合规）')
        if args.rows and not args.check:
            rp = Path(args.rows).expanduser().resolve()
            doc_rows = json.loads(rp.read_text(encoding='utf-8'))
            rws = doc_rows.get('rows') if isinstance(doc_rows, dict) else doc_rows
            if isinstance(rws, list):
                for w in rws:
                    w['资产关联'] = ''          # 没有声明就留空，不写「不适用」也不写描述
                if args.inject:
                    out = Path(args.out_rows).expanduser().resolve() if args.out_rows else rp
                    out.write_text(json.dumps(doc_rows, ensure_ascii=False, indent=1), encoding='utf-8')
                    print(f'无 external_assets.json：{len(rws)} 行的「资产关联」列已清空并写回 {out.name}')
        if args.json_out:
            p = Path(args.json_out); p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(_stamp(payload), ensure_ascii=False, indent=2), encoding='utf-8')
        return 0

    payload['file'] = str(target)
    payload['present'] = True
    try:
        doc = json.loads(target.read_text(encoding='utf-8'))
    except Exception as exc:  # noqa: BLE001
        print(f'{target} 不是合法 JSON：{exc}', file=sys.stderr)
        payload['problems'] = [{'where': '$', 'message': f'JSON 解析失败：{exc}'}]
        if args.json_out:
            Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.json_out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        return 1 if args.check else 0

    problems, warnings = validate(doc, root)
    payload.update({'problems': problems, 'warnings': warnings,
                    'assets': doc.get('assets') or [],
                    'cell': render(doc.get('assets') or []) if not problems else None,
                    'detail': detail_text(doc.get('assets') or []) if not problems else None})
    print(f'{target.relative_to(root)}：{len(doc.get("assets") or [])} 条引用，'
          f'问题 {len(problems)} 条，告警 {len(warnings)} 条')
    for p_ in problems:
        print(f'  ! {p_["where"]}: {p_["message"]}', file=sys.stderr)
    for w_ in warnings:
        print(f'  · {w_["where"]}: {w_["message"]}', file=sys.stderr)
    if payload['cell'] is not None:
        print(f'  资产关联列：{payload["cell"] or "（空）"}')
    if payload.get('detail'):
        print(f'  明细：{payload["detail"][:160]}{"…" if len(payload["detail"]) > 160 else ""}')

    if args.rows:
        rp = Path(args.rows).expanduser().resolve()
        doc_rows = json.loads(rp.read_text(encoding='utf-8'))
        rws = doc_rows.get('rows') if isinstance(doc_rows, dict) else doc_rows
        if not isinstance(rws, list):
            print(f'rows.json 结构不认识：{rp}', file=sys.stderr)
            return 2
        if problems:
            print('存在校验问题，未写入「资产关联」列', file=sys.stderr)
        else:
            cell = payload['cell'] or ''
            for w in rws:
                w['资产关联'] = cell            # 覆盖写：有声明写编号，无声明留空
            if cell:
                print(f'已写入 {len(rws)} 行的「资产关联」列：{cell}')
            else:
                print(f'包内无 external_assets.json，{len(rws)} 行的「资产关联」列已清空（按规则留空）')
        if args.inject and not args.check and not problems:
            out = Path(args.out_rows).expanduser().resolve() if args.out_rows else rp
            out.write_text(json.dumps(doc_rows, ensure_ascii=False, indent=1), encoding='utf-8')
            print(f'已写入：{out}')

    if args.json_out:
        p = Path(args.json_out); p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(_stamp(payload), ensure_ascii=False, indent=2), encoding='utf-8')

    if args.check and problems:
        print(f'校验未通过：{len(problems)} 条问题')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
