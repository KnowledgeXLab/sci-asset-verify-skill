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


PACKAGE_SCOPE_WORDS = ('包级', '整包', '顶层', '包结构', 'package')


def check_attribution(report: Path, text: str) -> list[str]:
    """P0/P1 每一行都要写明归属：属于哪个（哪些）资产，或明说「包级」。"""
    run_dir = report.parent
    if not (run_dir / 'probe-report.json').is_file():
        return []
    names = set()
    inv = run_dir / 'inventory.json'
    if inv.is_file():
        try:
            doc = json.loads(inv.read_text(encoding='utf-8'))
            for a in (doc.get('assets') or []):
                for key in ('dir', 'name', 'asset_id'):
                    v = str(a.get(key) or '')
                    if v:
                        names.add(v)
                        names.add(v.split('/')[-1])
        except Exception:  # noqa: BLE001
            pass
    block = section_of(text) or ''
    warns = []
    for p_ in ('P0', 'P1'):
        for line in block.splitlines():
            line = line.strip()
            if not line.startswith('|') or '---' in line or f'| {p_}-' not in line:
                continue
            if any(w in line for w in PACKAGE_SCOPE_WORDS):
                continue
            if any(n and n in line for n in names):
                continue
            warns.append(f'{p_} 行未写明归属（属于哪个资产，或「包级」）：{line[:50]}…'
                         '——请补「归属」列，便于定位源头')
    return warns


def check_sage_run_dir(report: Path) -> list[str]:
    """有 sage-need.json 却没带 --run-dir 跑探测时，P0-SAGE 会被漏判。"""
    run_dir = report.parent
    need_f = run_dir / 'sage-need.json'
    probe_f = run_dir / 'probe-report.json'
    if not need_f.is_file():
        return []
    try:
        needed = bool(json.loads(need_f.read_text(encoding='utf-8')).get('needed'))
    except Exception:  # noqa: BLE001
        return []
    if not needed:
        return []
    if not probe_f.is_file():
        return ['本批需要 Sage 附加测试，但缺少 probe-report.json —— 请带 --run-dir 重跑 probe_package.py']
    try:
        prof = (json.loads(probe_f.read_text(encoding='utf-8')).get('checks') or {}).get('sage_profile')
    except Exception:  # noqa: BLE001
        prof = None
    if not prof:
        return ['本批需要 Sage 附加测试，但 probe-report.json 里没有 sage_profile'
                ' —— 说明探测没带 --run-dir，P0-SAGE 会漏判，请重跑']
    if not prof.get('ran_ok') and 'P0-SAGE' not in probe_f.read_text(encoding='utf-8'):
        return ['Sage 未跑通，但结论里没有 P0-SAGE —— 请按 SKILL.md 第 6 步索取配置后重判，'
                '仍失败须判 P0 阻断']
    return []


def check_root_file_judgement(report: Path, text: str) -> list[str]:
    """包根一级文件不参与判定：P0/P1 若拿包根文件的内容说事，要告警。"""
    run_dir = report.parent
    inv = run_dir / 'inventory.json'
    if not inv.is_file():
        return []
    try:
        doc = json.loads(inv.read_text(encoding='utf-8'))
        root = Path(str(doc.get('package') or '')).expanduser()
    except Exception:  # noqa: BLE001
        return []
    if not root.is_dir():
        return []
    root_files = [p.name for p in sorted(root.iterdir()) if p.is_file()]
    if not root_files:
        return []
    block = section_of(text) or ''
    warns = []
    for p_ in ('P0', 'P1', 'P2'):
        for line in block.splitlines():
            line = line.strip()
            if not line.startswith('|') or '---' in line or f'| {p_}-' not in line:
                continue
            cells = [c.strip() for c in line.strip('|').split('|')]
            # 只看「归属」性质的单元格（含包级/整包/顶层，或是短的资产名），避免把问题正文里的引用算进来
            owner_text = ' '.join(
                c for c in cells[1:3]
                if ('包级' in c or '整包' in c or '顶层' in c or 0 < len(c) <= 40))
            hit = [f for f in root_files if f in owner_text]
            if hit:
                warns.append(f'{p_} 行的归属是包根一级文件（{", ".join(hit[:2])}）：{line[:50]}…'
                             '——包根一级文件不参与判定，请直接移除该行（不要降为 P2）')
    return warns


LIFECYCLE_WORDS = ('lifecycle', 'draft')


def check_lifecycle_mention(report: Path, text: str) -> list[str]:
    """lifecycle=draft 是正常交付状态：问题清单里出现 lifecycle/draft 一律要求移除。"""
    if not (report.parent / 'probe-report.json').is_file():
        return []
    block = section_of(text) or ''
    warns = []
    for p_ in ('P0', 'P1', 'P2'):
        for line in block.splitlines():
            line = line.strip()
            if not line.startswith('|') or '---' in line or f'| {p_}-' not in line:
                continue
            cells = [c.strip() for c in line.strip('|').split('|')]
            owner_text = ' '.join(cells[1:3])
            low = owner_text.lower()
            if any(w in low for w in LIFECYCLE_WORDS):
                warns.append(f'{p_} 行提到 lifecycle/draft：{line[:50]}…'
                             '——draft 是正常交付状态，不要据它判级；请删除该表述或移除整行（不要降为 P2）')
    return warns


FOREIGN_CONTEXT = ('上次判', '上一批', '与上一批相比', '历史结论', '我记得', '此前认为', '之前判定',
                   '以前核验过', '之前那批', '上次核验', '上次跑')


def check_foreign_context(report: Path, text: str) -> list[str]:
    """报告不得以跨会话/跨批次的记忆作为判定依据（历史污染）。"""
    block = section_of(text) or text
    warns = []
    for line in block.splitlines():
        line = line.strip()
        if not line:
            continue
        hit = [w for w in FOREIGN_CONTEXT if w in line]
        if hit:
            warns.append(f'报告引用了跨会话/跨批次记忆（{hit[0]}）：{line[:50]}…'
                         '——判定只能以本批次落盘的机检事实为准')
    return warns


def check_checklist_citation(report: Path, text: str) -> list[str]:
    """新式批次（run 里有 probe-report.json）里，只有 P0/P1 必须引用 check_id；P2 是建议，允许自由条目。"""
    if not (report.parent / 'probe-report.json').is_file():
        return []
    block = section_of(text) or ''
    warns = []
    ids = set()
    try:
        checklist = Path(__file__).resolve().parents[1] / 'assets' / 'audit-checks.json'
        spec = json.loads(checklist.read_text(encoding='utf-8'))
        ids = {c['id'] for c in spec.get('checks') or [] if c.get('id')}
    except Exception:  # noqa: BLE001
        ids = set()
    for p_ in ('P0', 'P1'):          # P2 就是"建议"，不限制来源
        for line in block.splitlines():
            line = line.strip()
            if not line.startswith('|') or '---' in line or f'| {p_}-' not in line:
                continue
            if not any(i in line for i in ids) if ids else 'CHK-' not in line:
                warns.append(f'{p_} 行未引用固定检查清单的检查项 id：{line[:50]}…'
                             f'（{p_} 是判定性结论，必须来自 audit-checks.json；纯建议请降到 P2）')
    return warns


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

    # 收尾机打小节（告警）
    head = text.split('\n---\n', 1)[0] if text.startswith('---\n') else ''
    if 'skill_version' not in head:
        warnings.append('报告开头缺 front matter 的 skill_version / generated_at（跑 scripts/stamp_report.py 生成）')
    if '<!-- evidence-prune:begin -->' not in text:
        warnings.append('缺少「证据保留口径」小节（跑 scripts/prune_evidence.py 生成）')
    warnings += check_checklist_citation(report, text)
    warnings += check_foreign_context(report, text)
    warnings += check_attribution(report, text)
    warnings += check_root_file_judgement(report, text)
    warnings += check_sage_run_dir(report)
    warnings += check_lifecycle_mention(report, text)

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
