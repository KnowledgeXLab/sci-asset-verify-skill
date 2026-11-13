#!/usr/bin/env python3
"""按资产类型核对用例覆盖度，低于下限就标“覆盖不足”。

读 `cases.json`（以及可选的 `inventory.json` 拿包内实际存在的资产类型），按
`assets/case-minimums.json` 的下限判定每个类型覆盖是否充分，输出：

* `coverage.json`：逐类型 planned/executed/passed/min_cases/status/gaps
* 可选 `--report validation-report.md`：把覆盖度小节注入报告（用标记块原地替换，
  标记为 `<!-- coverage:begin -->` … `<!-- coverage:end -->`；不存在则在文末追加）

退出码：0 覆盖充足；1 存在覆盖不足（可用 --no-gate 只出报告不拦）；2 读取失败。

用法：
  python scripts/check_coverage.py --cases <run>/cases.json [--inventory <run>/inventory.json] \
      [--run-dir <run>] [--report <run>/validation-report.md] [--min-per-type tool=4,data=2] \
      [--no-gate] [--json-out <run>/coverage.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

try:                                     # 版本戳：报告与证据 JSON 都要能追溯校验 skill 版本
    from _version import stamp as _stamp
except Exception:                        # noqa: BLE001
    def _stamp(payload):
        return payload

DEFAULT_MIN = Path(__file__).resolve().parents[1] / 'assets' / 'case-minimums.json'
BEGIN, END = '<!-- coverage:begin -->', '<!-- coverage:end -->'
TYPE_CN = {'data': 'data 数据', 'tool': 'tool 工具', 'model': 'model 模型',
           'environment': 'environment 环境', 'skill': 'skill 技能', 'task': 'task 任务',
           'trajectory': 'trajectory 轨迹', 'benchmark': 'benchmark 评测'}


def load(p):
    return json.loads(Path(p).read_text(encoding='utf-8'))


TYPES = ('data', 'tool', 'model', 'environment', 'skill', 'task', 'trajectory', 'benchmark')


def asset_type_of(case):
    """从 asset_id 取类型：兼容 v1.1 的 sciasset://{project}/{ns}/{type}/{slug} 与旧的 {ns}/{type}/{slug}。"""
    aid = str(case.get('asset_id') or '')
    m = re.match(r'^sciasset://[^/]+/([^/]+)/([^/]+)/[^/]+$', aid)      # 四段
    if m and m.group(2) in TYPES:
        return m.group(2)
    m = re.match(r'^sciasset://[^/]+/([^/]+)/[^/]+$', aid)             # 三段（旧）
    if m and m.group(1) in TYPES:
        return m.group(1)
    return str(case.get('asset_type') or 'unknown')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='按类型核对用例覆盖度')
    ap.add_argument('--cases', required=True)
    ap.add_argument('--inventory', help='inventory.json（用于知道包内实际有哪些类型）')
    ap.add_argument('--run-dir', help='输出目录，默认取 cases.json 所在目录')
    ap.add_argument('--report', help='validation-report.md，注入覆盖度小节')
    ap.add_argument('--min-per-type', help='覆盖下限，如 tool=4,data=2')
    ap.add_argument('--no-gate', action='store_true', help='不足也不返回非 0')
    ap.add_argument('--json-out')
    args = ap.parse_args(argv)

    try:
        cases_doc = load(args.cases)
    except Exception as e:  # noqa: BLE001
        print(f'读取 cases.json 失败：{e}', file=sys.stderr)
        return 2
    cases = cases_doc.get('cases') if isinstance(cases_doc, dict) else cases_doc
    if not isinstance(cases, list):
        print('cases.json 结构不认识（期望 {"cases":[...]} 或数组）', file=sys.stderr)
        return 2

    minimums = load(DEFAULT_MIN)
    by_type = dict(minimums.get('by_type') or {})
    require = minimums.get('require_kinds') or {}
    default_min = minimums.get('default') or {}
    if args.min_per_type:
        for part in args.min_per_type.split(','):
            k, _, v = part.partition('=')
            if k.strip() and v.strip().isdigit():
                by_type.setdefault(k.strip(), {})['min_cases'] = int(v)

    # 包内实际存在的类型
    present = set()
    if args.inventory and Path(args.inventory).exists():
        inv = load(args.inventory)
        for a in inv.get('assets') or []:
            if a.get('asset_type'):
                present.add(a['asset_type'])
    present |= {asset_type_of(c) for c in cases}

    stats = defaultdict(lambda: {'planned': 0, 'executed': 0, 'passed': 0, 'failed': 0,
                                 'skipped': 0, 'not_evaluated': 0, 'negative_expected': 0})
    for c in cases:
        t = asset_type_of(c)
        s = stats[t]
        s['planned'] += 1
        st = str(c.get('execution_status') or '')
        vs = str(c.get('validation_status') or '')
        if st == 'completed':
            s['executed'] += 1
        if st == 'skipped':
            s['skipped'] += 1
        if vs == 'pass':
            s['passed'] += 1
        elif vs == 'fail':
            s['failed'] += 1
        elif vs in ('not_evaluated', '', None):
            s['not_evaluated'] += 1
        exp = str(c.get('expected_kind') or 'success')
        kind = str(c.get('kind') or '')
        if exp != 'success' or kind in ('rejection', 'invalid', 'error'):
            s['negative_expected'] += 1

    rows, gaps, gate_fail = [], [], False
    for t in sorted(present, key=lambda x: ('data', 'tool', 'model', 'environment', 'skill',
                                            'task', 'trajectory', 'benchmark').index(x)
                    if x in ('data', 'tool', 'model', 'environment', 'skill', 'task',
                             'trajectory', 'benchmark') else 99):
        s = stats.get(t) or {'planned': 0, 'executed': 0, 'passed': 0, 'failed': 0,
                             'skipped': 0, 'not_evaluated': 0, 'negative_expected': 0}
        conf = by_type.get(t, {})
        min_cases = int(conf.get('min_cases', default_min.get('min_cases', 2)))
        min_exec = int(conf.get('min_executed', default_min.get('min_executed', 1)))
        need_neg = int((require.get(t) or {}).get('negative_expected', 0))

        reasons = []
        if s['planned'] == 0:
            reasons.append('包内有该类型资产但无任何给定用例（no_provided_case）')
        else:
            if s['planned'] < min_cases:
                reasons.append(f'用例数 {s["planned"]} < 下限 {min_cases}')
            if s['executed'] < min_exec:
                reasons.append(f'已执行 {s["executed"]} < 下限 {min_exec}')
            if s['executed'] == 0:
                reasons.append('全部用例未执行（skipped）')
            if need_neg and s['negative_expected'] < need_neg:
                reasons.append(f'缺少预期错误/边界用例（需 ≥{need_neg}，实际 {s["negative_expected"]}）')
        status = 'ok' if not reasons else ('no_case' if s['planned'] == 0 else 'insufficient')
        if status != 'ok':
            gate_fail = True
            gaps.append({'asset_type': t, 'status': status, 'reasons': reasons})
        rows.append({'asset_type': t, 'planned': s['planned'], 'executed': s['executed'],
                     'passed': s['passed'], 'failed': s['failed'], 'skipped': s['skipped'],
                     'not_evaluated': s['not_evaluated'],
                     'negative_expected': s['negative_expected'],
                     'min_cases': min_cases, 'min_executed': min_exec, 'status': status,
                     'reasons': reasons})

    total = {'planned': sum(r['planned'] for r in rows), 'executed': sum(r['executed'] for r in rows),
             'passed': sum(r['passed'] for r in rows), 'failed': sum(r['failed'] for r in rows),
             'skipped': sum(r['skipped'] for r in rows)}

    out_dir = Path(args.run_dir) if args.run_dir else Path(args.cases).resolve().parent
    payload = {'tool': 'check_coverage.py',
               'checked_at': datetime.now().astimezone().isoformat(timespec='seconds'),
               'cases_file': str(args.cases), 'minimums': str(DEFAULT_MIN),
               'verdict': 'sufficient' if not gate_fail else 'insufficient',
               'totals': total, 'by_type': rows, 'gaps': gaps}

    json_out = Path(args.json_out) if args.json_out else out_dir / 'coverage.json'
    json_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(_stamp(payload), ensure_ascii=False, indent=2), encoding='utf-8')

    # ---- markdown 小节
    lines = [BEGIN, '### 用例覆盖度（按资产类型）', '']
    if gate_fail:
        lines.append(f'**覆盖不足**：{len(gaps)} 个类型未达下限，以下结论不得视为完整验收。')
    else:
        lines.append('各类型用例数均达到下限。')
    lines += ['', '| 类型 | 计划 | 执行 | 通过 | 跳过 | 下限 | 判定 |', '|---|---|---|---|---|---|---|']
    for r in rows:
        mark = '✅ 达标' if r['status'] == 'ok' else ('❌ 无用例' if r['status'] == 'no_case' else '⚠️ 不足')
        lines.append(f"| {TYPE_CN.get(r['asset_type'], r['asset_type'])} | {r['planned']} | "
                     f"{r['executed']} | {r['passed']} | {r['skipped']} | ≥{r['min_cases']} | {mark} |")
    if gaps:
        lines += ['', '不足原因：']
        for g in gaps:
            lines.append(f"- {TYPE_CN.get(g['asset_type'], g['asset_type'])}：" + '；'.join(g['reasons']))
    lines.append(END)
    block = '\n'.join(lines)

    if args.report:
        rp = Path(args.report)
        if rp.exists():
            text = rp.read_text(encoding='utf-8')
            if BEGIN in text and END in text:
                text = re.sub(re.escape(BEGIN) + r'.*?' + re.escape(END), block, text, flags=re.S)
            else:
                # 优先插在「最终判定」之前（报告结构：结论→对比对象→结果表→问题→最终判定）
                anchor = re.search(r'^##\s*5[\.、\s]', text, re.M) or re.search(r'^##\s*最终判定', text, re.M)
                if anchor:
                    idx = anchor.start()
                    text = text[:idx] + block + '\n\n' + text[idx:]
                else:
                    text = text.rstrip() + '\n\n' + block + '\n'
            rp.write_text(text, encoding='utf-8')
        else:
            rp.parent.mkdir(parents=True, exist_ok=True)
            rp.write_text(block + '\n', encoding='utf-8')
        print(f'已注入覆盖度小节 → {rp}')

    print(block)
    if gate_fail and not args.no_gate:
        print('\n覆盖不足：请补用例或显式说明缺口（--no-gate 可只出报告不拦截）。', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
