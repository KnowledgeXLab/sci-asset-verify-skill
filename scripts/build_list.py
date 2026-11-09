#!/usr/bin/env python3
"""按 skill 内置 schema 生成对外发版资产清单 xlsx，并在生成后**自动调用 check_list.py 自检**。

表头、列序、字段口径全部来自 `assets/external-list-schema.json`，不接受模板参数——这是"生成即合规"
的唯一入口，避免再出现按记忆或按旧模板填错列的问题。

用法
----
  python scripts/build_list.py --rows rows.json --out registration/科学数字资产清单.xlsx \
      [--schema assets/external-list-schema.json] [--cases cases.json] [--no-check]

rows.json 结构（任一）：
  [ {"资产名称": "...", "资产类型": "tool", ...}, ... ]
  { "rows": [ ... ], "cases": [ {"用例编号": "...", "资产名称": "...", ...}, ... ] }

* 缺失字段留空，不补默认值；schema 中 required=true 的字段为空会导致自检报错。
* `--cases` 或 rows.json 里的 cases 会写入「用例明细」sheet（列见 CASE_COLUMNS）。
* 生成后默认调用 `scripts/check_list.py`，并把自检结果落盘为 `check-list-report.json`（可用
  `--check-json-out` 指定路径）；自检不通过时本脚本以非 0 退出，并保留 xlsx 供修表。

退出码：0 生成且自检通过；1 生成成功但自检失败；2 参数/依赖/IO 错误。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_SCHEMA = Path(__file__).resolve().parents[1] / 'assets' / 'external-list-schema.json'
CHECKER = Path(__file__).resolve().parent / 'check_list.py'

CASE_COLUMNS = ['用例编号', '资产名称', '关联资产及检查点', '资产版本', '用例名称', '用例来源', '用例类型',
                '前置条件', '输入或Prompt', '执行入口', '预期结果', '真值类型', '真值来源', '比较规则',
                '验收阈值及依据', '实际输出', '执行状态', '验证结论', '误差或检查结果', '证据与报告', '备注']


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='按内置 schema 生成清单 xlsx 并自动自检')
    ap.add_argument('--rows', required=True, help='行数据 JSON')
    ap.add_argument('--out', required=True, help='输出 xlsx 路径')
    ap.add_argument('--schema', default=str(DEFAULT_SCHEMA))
    ap.add_argument('--cases', help='用例明细 JSON（数组）')
    ap.add_argument('--no-check', action='store_true', help='跳过生成后的自检')
    ap.add_argument('--check-json-out',
                    help='把自检结果写成 JSON（默认 <out 同目录>/check-list-report.json）')
    args = ap.parse_args(argv)

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import _deps
        deps = _deps.ensure_openpyxl()
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
        from openpyxl.worksheet.datavalidation import DataValidation
    except Exception as exc:  # noqa: BLE001
        print(f'无法准备 openpyxl（生成清单表必需）：{exc}', file=sys.stderr)
        return 2

    schema = json.loads(Path(args.schema).read_text(encoding='utf-8'))
    fields = schema['fields']
    headers = [f['header'] for f in fields]
    by_header = {f['header']: f for f in fields}

    raw = json.loads(Path(args.rows).read_text(encoding='utf-8'))
    if isinstance(raw, dict):
        rows = raw.get('rows') or raw.get('data') or []
        cases = raw.get('cases') or []
    else:
        rows, cases = raw, []
    if args.cases:
        cases = json.loads(Path(args.cases).read_text(encoding='utf-8'))
    if not rows:
        print('rows.json 里没有行数据（期望数组或 {"rows": [...]}）', file=sys.stderr)
        return 2

    unknown = sorted({k for r in rows for k in r} - set(headers))
    if unknown:
        print(f'警告：行数据里有 schema 未定义的字段，将被忽略：{unknown}', file=sys.stderr)

    wb = Workbook()
    ws = wb.active
    ws.title = schema.get('sheet_name', '资产明细')
    head_fill = PatternFill('solid', fgColor='DDEBF7')
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = head_fill
        c.alignment = Alignment(wrap_text=True, vertical='center')
    for r in rows:
        ws.append([r.get(h, '') for h in headers])

    # 列宽 + 枚举下拉（数据验证），让下游不容易填错
    width_default = {'资产名称': 26, '资产类型': 10, '领域Profile': 12, '资产摘要': 46, '任务|操作标签': 20,
                     '任务描述': 44, '科学对象': 24, '资产提供单位': 18, '联系人': 16, '项目负责人': 16,
                     '来源|产出方式': 38, '版本': 9, '格式|接口': 24, '规模|数量': 32, '时间范围': 20,
                     '空间|坐标|实验条件': 36, '输入说明': 42, '输出说明': 42, '依赖资源': 32,
                     '运行环境|资源要求': 30, '评价指标|验收条件': 46, '敏感级别': 10, '质量说明': 44,
                     '资产关联': 28}
    for i, h in enumerate(headers, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width_default.get(h, 20)
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical='top')
    ws.freeze_panes = 'B2'

    dv_count = 0
    for i, h in enumerate(headers, start=1):
        opts = by_header[h].get('options')
        if not opts:
            continue
        col = get_column_letter(i)
        formula = '"' + ','.join(o for o in opts if ',' not in o) + '"'
        if len(formula) > 250:          # Excel 数据验证长度上限，超长就不加
            continue
        dv = DataValidation(type='list', formula1=formula, allow_blank=True, showDropDown=False)
        ws.add_data_validation(dv)
        dv.add(f'{col}2:{col}{max(2, ws.max_row)}')
        dv_count += 1

    if cases:
        ws2 = wb.create_sheet('用例明细')
        ws2.append(CASE_COLUMNS)
        for c in ws2[1]:
            c.font = Font(bold=True)
            c.fill = head_fill
            c.alignment = Alignment(wrap_text=True)
        for c in cases:
            ws2.append([c.get(col, '') for col in CASE_COLUMNS])
        for i, col in enumerate(CASE_COLUMNS, start=1):
            ws2.column_dimensions[get_column_letter(i)].width = 40 if col in ('用例名称', '预期结果', '输入或Prompt') else 24
        for row in ws2.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(wrap_text=True, vertical='top')
        ws2.freeze_panes = 'C2'

    ws3 = wb.create_sheet('字段口径说明')
    ws3.append(['项', '说明'])
    for k, v in [
        ('schema 版本', f"{schema.get('schema_version')}（{schema.get('name')}）"),
        ('字段数', f'{len(fields)} 列，必填 {sum(1 for f in fields if f.get("required"))} 列'),
        ('字段顺序', '按 schema fields[].order；表头文字逐字对齐 fields[].header'),
        ('依据', f"{schema.get('authority', {}).get('spec')} {schema.get('authority', {}).get('spec_section')}"),
        ('未纳入的内部管理字段', '、'.join(schema.get('excluded_internal_fields') or [])),
        ('行标识', ' + '.join(schema.get('row_key') or [])),
        ('生成时间', datetime.now().astimezone().isoformat(timespec='seconds')),
        ('生成工具', 'scripts/build_list.py（生成后自动跑 scripts/check_list.py）'),
    ]:
        ws3.append([k, v])
    ws3.column_dimensions['A'].width = 22
    ws3.column_dimensions['B'].width = 110
    for row in ws3.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical='top')

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    print(f'已生成 {out}（{len(rows)} 行 × {len(headers)} 列，下拉校验 {dv_count} 列'
          + (f'，用例明细 {len(cases)} 行' if cases else '')
          + f"，openpyxl {deps['version']}（{deps['source']}））")

    if args.no_check:
        return 0
    check_json = Path(args.check_json_out) if args.check_json_out else out.parent / 'check-list-report.json'
    print('→ 自动自检：', ' '.join([sys.executable, str(CHECKER), str(out), '--schema', args.schema,
                                    '--json-out', str(check_json)]))
    proc = subprocess.run([sys.executable, str(CHECKER), str(out), '--schema', args.schema,
                           '--json-out', str(check_json)])
    print(f'→ 自检结果：{check_json}')
    if proc.returncode != 0:
        print('自检未通过：请按上面的报错修表后重跑 check_list.py；xlsx 已保留。', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
