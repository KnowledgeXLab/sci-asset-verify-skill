#!/usr/bin/env python3
"""检查验证报告的问题清单是否按 P0/P1/P2 三级罗列。

规则（SKILL.md §5 / references/validation.md）：
  · 报告必须有「问题清单」一节；
  · 本节按 **P0 阻断 / P1 重要 / P2 建议** 三级罗列，每条带编号（P0-1、P1-2…）、问题与证据、影响或整改要求；
  · 某一级为空写「无」；三级全空时写 **未发现问题**。

同时核对报告收尾必备的两个机打小节是否在位（缺失只告警，不判失败，因为 prune / stamp 可能单独执行过）。

用法：
  python scripts/check_report.py --report <run>/validation-report.md [--json-out FILE] [--strict]

退出码：0 通过；1 问题清单不合规（--strict 下告警也算失败）；2 IO 错误。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

try:
    from _version import stamp as _stamp
except Exception:  # noqa: BLE001
    def _stamp(payload):
        return payload

SECTIONS = ('P0', 'P1', 'P2')
LEVEL_NAMES = {'P0': '阻断', 'P1': '重要', 'P2': '建议'}
EMPTY_WORDS = ('无', '没有', '未发现', 'none')
NO_ISSUE = '未发现问题'


def section_of(text: str) -> str:
    """取「问题清单 / 问题」小节正文：到下一个**同级或更高级**标题为止（### P0 之类子标题属于本节）。"""
    m = re.search(r'^(#{2,4})\s*[^\n]*问题[^\n]*\n', text, re.M)
    if not m:
        return ''
    level = len(m.group(1))
    rest = text[m.end():]
    nxt = re.search(rf'^#{{2,{level}}}\s', rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='检查报告问题清单的 P0/P1/P2 分级')
    ap.add_argument('--report', required=True)
    ap.add_argument('--strict', action='store_true', help='告警也判失败')
    ap.add_argument('--json-out')
    args = ap.parse_args(argv)

    report = Path(args.report).expanduser().resolve()
    if not report.is_file():
        print(f'报告不存在：{report}', file=sys.stderr)
        return 2
    text = report.read_text(encoding='utf-8')
    errors, warnings = [], []

    if not re.search(r'^#{2,4}\s*[^\n]*问题[^\n]*\n', text, re.M):
        errors.append('缺少「问题清单」小节')
    body = section_of(text)

    def block(p: str) -> str:
        m = re.search(rf'^#{{2,4}}[^\n]*\b{p}\b[^\n]*\n(.*?)(?=^#{{2,4}}\s|\Z)', body, re.S | re.M)
        return m.group(1) if m else ''

    present = {p_: bool(re.search(rf'\b{p_}\b', body)) for p_ in SECTIONS}
    if not body.strip():
        if NO_ISSUE not in text:
            errors.append(f'报告未给问题结论：既无「问题清单」分级清单，也未写「{NO_ISSUE}」')
    else:
        missing = [p_ for p_ in SECTIONS if not present[p_]]
        if missing and NO_ISSUE not in body:
            errors.append('问题清单缺少分级标注：' + '、'.join(f'{p_} {LEVEL_NAMES[p_]}' for p_ in missing))
        for p_ in SECTIONS:
            blk = block(p_)
            # 只认条目开头的编号（表格行 / 列表项），避免把正文里的交叉引用当成该级条目
            ids = [m.groups() for m in re.finditer(r'^\s*[|\-*]?\s*(P[0-2])-(\d+)', blk, re.M)]
            wrong = [f'{a}-{b}' for a, b in ids if a != p_]
            if wrong:
                warnings.append(f'{p_} 段下出现不属于该级别的编号：{wrong[:3]}')
            if present[p_] and not blk.strip():
                warnings.append(f'{p_} 段为空，建议显式写「无」')
        levels_defined = [p_ for p_ in SECTIONS if present[p_]]
        if levels_defined and all(not block(p_).strip() for p_ in levels_defined) and NO_ISSUE not in body:
            errors.append(f'三级都为空时必须写「{NO_ISSUE}」')

    # markdown 表格：未转义的竖线会把单元格切错列，逐块核对每行竖线数与表头一致
    def pipe_count(line: str) -> int:
        # 先去掉 \| 转义，再数
        return line.replace('\\|', '').count('|')

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith('|'):
            block = []
            while i < len(lines) and lines[i].lstrip().startswith('|'):
                block.append((i + 1, lines[i]))
                i += 1
            if len(block) >= 2:
                head_n = pipe_count(block[0][1])
                for lineno, row in block[1:]:
                    n = pipe_count(row)
                    if n != head_n:
                        errors.append(f'第 {lineno} 行表格竖线数与表头不一致（表头 {head_n} 个，本行 {n} 个）：'
                                      '单元格内的 | 必须写成 \\| 转义，否则会被切错列')
        else:
            i += 1

    # 收尾机打小节（告警）
    if '<!-- validation-tool:begin -->' not in text:
        warnings.append('缺少「校验工具版本」小节（跑 scripts/stamp_report.py 生成）')
    if '<!-- evidence-prune:begin -->' not in text:
        warnings.append('缺少「证据保留口径」小节（跑 scripts/prune_evidence.py 生成）')

    payload = _stamp({'tool': 'check_report.py', 'report': str(report),
                      'levels_present': present, 'errors': errors, 'warnings': warnings,
                      'ok': not errors and not (args.strict and warnings)})
    for e in errors:
        print(f'  error  {e}', file=sys.stderr)
    for w in warnings:
        print(f'  warn   {w}', file=sys.stderr)
    print(f'报告结构检查：{"通过" if payload["ok"] else "未通过"}（error {len(errors)}，warning {len(warnings)}）')
    if args.json_out:
        p = Path(args.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if payload['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
