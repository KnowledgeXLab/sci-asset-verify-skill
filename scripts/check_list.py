#!/usr/bin/env python3
"""按 skill 内置的对外发版清单 schema 检查填写好的 xlsx。

标准来源：assets/external-list-schema.json（24 列）。**不比对用户自定义模板**——表头必须与内置
schema 一致（顺序与文字），顺序不同报 error；用户显式指定其他 schema 时用 `--schema` 指向该文件。

依赖 openpyxl（仅本脚本需要；校验包络的 validate_envelope.py 是纯标准库）。

用法：
  python scripts/check_list.py <xlsx> [--schema FILE] [--sheet 资产明细] [--json-out FILE]
退出码：0 通过；1 有问题；2 无法读取/缺依赖
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

DEFAULT_SCHEMA = Path(__file__).resolve().parents[1] / 'assets' / 'external-list-schema.json'
SEMVER = re.compile(r'^\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?$')


def load_schema(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='按内置 schema 检查对外发版资产清单 xlsx')
    ap.add_argument('xlsx')
    ap.add_argument('--schema', default=str(DEFAULT_SCHEMA))
    ap.add_argument('--sheet', default=None, help='资产明细所在工作表名（默认取 schema.sheet_name）')
    ap.add_argument('--external-refs', action='append', default=[],
                    help='external_assets.json 路径（可重复；每个资产目录一份）')
    ap.add_argument('--package', help='资产包根目录：自动收集各资产目录下的 external_assets.json 用于核对')
    ap.add_argument('--json-out')
    args = ap.parse_args(argv)

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import _deps
        _deps.ensure_openpyxl(verbose=False)
        from openpyxl import load_workbook
    except Exception as exc:  # noqa: BLE001
        print(f'无法准备 openpyxl（校验清单表必需）：{exc}', file=sys.stderr)
        return 2

    schema = load_schema(Path(args.schema))
    sheet_name = args.sheet or schema.get('sheet_name', '资产明细')
    fields = schema['fields']
    headers = [f['header'] for f in fields]
    by_header = {f['header']: f for f in fields}
    row_key = schema.get('row_key', ['资产名称', '版本'])
    rel_rules = schema.get('relation_rules') or {}
    rel_col = rel_rules.get('column') or '资产关联'
    declared_refs = None
    ref_files = list(args.external_refs or [])
    if args.package:
        root = Path(args.package).expanduser().resolve()
        ref_files += [str(p) for p in root.rglob('external_assets.json')
                      if 'asset-runs' not in p.parts and '.git' not in p.parts]
    if ref_files:
        declared_refs = set()
        for rf in ref_files:
            try:
                ext = json.loads(Path(rf).read_text(encoding='utf-8'))
            except Exception as exc:  # noqa: BLE001
                print(f'无法读取 external_assets.json（{rf}）：{exc}', file=sys.stderr)
                return 2
            for a in (ext.get('assets') or []):
                aid = str(a.get('asset_id') or '')
                declared_refs.add(aid[len('sciasset://'):] if aid.startswith('sciasset://') else aid)
    id_rules = schema.get('id_rules') or {}
    id_col = id_rules.get('list_column') or '资产ID'
    id_re = re.compile(id_rules['pattern']) if id_rules.get('pattern') else None

    try:
        wb = load_workbook(args.xlsx, data_only=False)
    except Exception as e:  # noqa: BLE001
        print(f'无法打开工作簿：{e}', file=sys.stderr)
        return 2

    errors, warnings = [], []
    if sheet_name not in wb.sheetnames:
        errors.append({'where': 'workbook', 'message': f'缺少工作表「{sheet_name}」，实际：{wb.sheetnames}'})
        ws = None
    else:
        ws = wb[sheet_name]

    rows_checked = 0
    keys = {}
    asset_names = set()
    if ws is not None:
        actual = [(c.value or '').strip() if isinstance(c.value, str) else c.value for c in ws[1]]
        # 去掉尾部空列
        while actual and actual[-1] in (None, ''):
            actual.pop()
        if [str(x) for x in actual] != headers:
            missing = [h for h in headers if h not in actual]
            extra = [a for a in actual if a not in headers]
            order_ok = set(actual) == set(headers)
            errors.append({'where': f'{sheet_name}!1',
                           'message': ('表头与内置 schema 不一致'
                                       + ('（列序不同）' if order_ok else '')
                                       + (f'；缺列：{missing}' if missing else '')
                                       + (f'；多列：{extra}' if extra else '')),
                           'expected': headers, 'actual': [str(x) for x in actual]})

        idx = {h: i for i, h in enumerate(actual)}
        for r in range(2, ws.max_row + 1):
            values = [ws.cell(row=r, column=c + 1).value for c in range(len(actual))]
            if all(v in (None, '') for v in values):
                continue
            rows_checked += 1
            row = {h: values[i] for h, i in idx.items() if h in by_header}
            for h, spec in by_header.items():
                val = row.get(h)
                if isinstance(val, str):
                    val_s = val.strip()
                    if val != val_s:
                        warnings.append({'where': f'{sheet_name}!{h}{r}', 'message': '首尾有空白，建议 strip'})
                    val = val_s
                if spec.get('required') and val in (None, ''):
                    errors.append({'where': f'{sheet_name}!{h}{r}', 'message': f'必填字段为空（{spec.get("required_note") or "必填"}）'})
                if val in (None, ''):
                    continue
                opts = spec.get('options')
                if opts:
                    parts = [p.strip() for p in re.split(r'[;；]', str(val)) if p.strip()]
                    for p in parts:
                        if p not in opts:
                            errors.append({'where': f'{sheet_name}!{h}{r}',
                                           'message': f'取值 {p!r} 不在 schema options 内'})
                if h == '版本' and not SEMVER.match(str(val)):
                    errors.append({'where': f'{sheet_name}!{h}{r}', 'message': f'版本应为语义化版本：{val!r}'})
                if h == id_col and id_re is not None:
                    sid = str(val)
                    m = id_re.match(sid)
                    if not m:
                        errors.append({'where': f'{sheet_name}!{h}{r}',
                                       'message': f'资产ID 不符合 {id_rules.get("format")}：{sid!r}'})
                    else:
                        seg_type = m.group(3)
                        vcol = str(row.get('版本') or '').strip()
                        tcol = str(row.get('资产类型') or '').strip()
                        if vcol and str(val).endswith('-' + vcol):
                            errors.append({'where': f'{sheet_name}!{h}{r}',
                                           'message': f'资产ID 不应包含版本号（版本由「版本」列表达）：{val!r}'})
                        if tcol and seg_type != tcol:
                            errors.append({'where': f'{sheet_name}!{h}{r}',
                                           'message': f'资产ID 类型段 {seg_type!r} 与「资产类型」列 {tcol!r} 不一致'})
                if h == rel_col:
                    raw = str(val).strip()
                    if '，' in raw:
                        errors.append({'where': f'{sheet_name}!{h}{r}',
                                       'message': '资产关联 多个编号请用英文逗号 , 分隔，不要用中文逗号'})
                    if 'sciasset://' in raw:
                        errors.append({'where': f'{sheet_name}!{h}{r}',
                                       'message': f'资产关联 不写 sciasset:// 前缀，只填 asset_id 的路径部分：{raw!r}'})
                    elif raw and not re.match(r'^[^\s,]+(,[^\s,]+)*$', raw):
                        errors.append({'where': f'{sheet_name}!{h}{r}',
                                       'message': '资产关联 只填 asset_id（去前缀、英文逗号分隔），不写依赖描述或资产名称：'
                                                  f'{raw!r}'})
                    else:
                        toks = [t for t in raw.split(',') if t]
                        if len(toks) != len(set(toks)):
                            errors.append({'where': f'{sheet_name}!{h}{r}', 'message': f'资产关联 有重复编号：{raw!r}'})
                        for t in toks:
                            if not re.match(r'^[A-Za-z0-9][A-Za-z0-9._-]*/[a-z0-9][a-z0-9-]*/'
                                            r'(data|tool|model|environment|skill|task|trajectory|benchmark)/'
                                            r'[a-z0-9][a-z0-9._-]*$', t):
                                errors.append({'where': f'{sheet_name}!{h}{r}',
                                               'message': f'资产关联 里的编号不成四段格式：{t!r}'})
                            elif declared_refs is not None and t not in declared_refs:
                                errors.append({'where': f'{sheet_name}!{h}{r}',
                                               'message': f'资产关联 填了 external_assets.json 里没有声明的资产：{t!r}'})
                if h == '任务|操作标签':
                    for p in [x.strip() for x in re.split(r'[;；]', str(val)) if x.strip()]:
                        if not re.match(r'^(problem|operation|stage|task):[a-z0-9-]+$', p):
                            errors.append({'where': f'{sheet_name}!{h}{r}', 'message': f'标签前缀不合法：{p!r}'})
            if row.get('资产名称'):
                asset_names.add(str(row['资产名称']).strip())
            key = tuple(str(row.get(k, '')).strip() for k in row_key)
            if all(key):
                if key in keys:
                    errors.append({'where': f'{sheet_name}!{r}', 'message': f'行标识重复：{key}（另见第 {keys[key]} 行）'})
                else:
                    keys[key] = r

        # 用例明细：资产名称必须能在资产明细解析
        case_sheet = '用例明细'
        if case_sheet not in wb.sheetnames:
            warnings.append({'where': 'workbook', 'message': f'缺少工作表「{case_sheet}」'})
        else:
            ws2 = wb[case_sheet]
            hdr2 = [c.value for c in ws2[1]]
            if '资产名称' not in hdr2:
                errors.append({'where': f'{case_sheet}!1', 'message': '用例明细缺少「资产名称」列（本清单无资产编号列）'})
            else:
                ci = hdr2.index('资产名称')
                names = asset_names
                dangling = set()
                for r in range(2, ws2.max_row + 1):
                    v = ws2.cell(row=r, column=ci + 1).value
                    if v and str(v).strip() and str(v).strip() not in names:
                        dangling.add(str(v).strip())
                if dangling:
                    errors.append({'where': case_sheet, 'message': f'用例明细引用了资产明细中不存在的资产名称：{sorted(dangling)[:5]}'})

    payload = {'tool': 'check_list.py', 'schema': str(Path(args.schema).resolve()), 'xlsx': str(Path(args.xlsx).resolve()),
               'sheet': sheet_name, 'rows_checked': rows_checked,
               'summary': {'errors': len(errors), 'warnings': len(warnings)},
               'errors': errors[:80], 'warnings': warnings[:40]}
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(_stamp(payload), ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"rows={rows_checked} errors={len(errors)} warnings={len(warnings)}")
    for e in payload['errors'][:20]:
        print(f"  ERROR {e['where']}: {e['message']}")
    for w in payload['warnings'][:10]:
        print(f"  WARN  {w['where']}: {w['message']}")
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
