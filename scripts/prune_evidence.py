#!/usr/bin/env python3
"""证据保留：跑完用例后清理中间产出，只留报告类证据。

为什么需要：`tool/work/`（逐 case 的 request/out/log）与 `_prepared/`（现制真值的派生数据）
可以到 GB 级，而验收真正要交给对方的是**报告类证据**——逐 case 的检查数值在
`correctness_report.json`/`data_cases_report.json`，台账在 `cases.json`，比较在 `comparison.json`。
把它们打进交付包会把一个 5 MB 的工具包撑成上百 MB，且都可重跑复现。

保留（永不删除）：
  * run 目录下除 `*/work/` 之外的全部文件（inventory/envelope-report/usage/state/cases/comparison/
    coverage/validation-report/envelope/work-copy/manifest-before 等）
  * `registration/**`、`scripts/**`、`refs/**`（冻结期望值）、`deployment/deployment_status.json`、`deployment/cli_probe_summary.json`、
    `deployment/probes/*/probe.json`（probe.json 已内嵌 result.json/error.json 的内容与退出码）
  * 任意 `.md` / `.json` / `.txt` / `.log` / `.xlsx`（报告类）

清理：
  * 任意名为 `work` / `_prepared` / `refwork` / `data-offline` / `data-all` 的目录
    （逐 case 工作目录、现制真值、以及 prepare_data.py 可重建的数据目录）
  * `deployment/probes/*/out/`（探针输出；其内容已进 probe.json）
  * `registration/` 之外的大体积实体文件：.tif .tiff .csv .npz .npy .shp .dbf .shx .prj .cpg .gpkg
    .zip .png .jpg .jpeg .pdf .h5 .pt .pkl .joblib .xls .xlsx（xlsx 指非 registration 的同名产物）

清理后会把 `cases.json` / `comparison.json` 里指向被删路径的 `actual_output` 标记 `actual_pruned: true`
（保留原路径便于追溯），写 `evidence-prune.json` 记录清理清单与剩余体积，并在
`validation-report.md` 里插入「证据保留口径」小节。

用法：
  python scripts/prune_evidence.py --run <asset-runs>/<run_id> [--report validation-report.md]
      [--keep-work] [--dry-run]

退出码：0 成功（含无可清理项）；2 参数/IO 错误。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

KEEP_SUFFIX = {'.md', '.json', '.txt', '.log', '.xlsx'}
# 目录名命中即整目录清理：逐 case 工作目录，以及由包内脚本可重建的数据/参考工作目录
REGENERABLE_DIRS = {'work', '_prepared', 'refwork', 'data-offline', 'data-all', 'prepared-data', 'mmwf-data'}
BULK_SUFFIX = {'.tif', '.tiff', '.csv', '.npz', '.npy', '.shp', '.dbf', '.shx', '.prj', '.cpg', '.gpkg',
               '.zip', '.png', '.jpg', '.jpeg', '.pdf', '.h5', '.pt', '.pkl', '.joblib', '.xls',
               '.tif.gz', '.tar', '.gz'}
NEVER_PRUNE_DIRS = ('registration', 'scripts', 'refs')   # refs/ 是冻结的期望值目录，整体保留（含 .npy）


def fmt(n: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return f'{n} {unit}' if unit == 'B' else f'{n:.2f} {unit}'
        n /= 1024


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


def size_of(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    total = 0
    for f in p.rglob('*'):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total


def collect(run: Path, keep_work: bool, dry_run: bool) -> tuple[list[dict], int]:
    pruned, pruned_bytes = [], 0
    work_dirs = []
    for d in run.rglob('*'):
        if d.is_dir() and d.name in REGENERABLE_DIRS \
                and not any(part in NEVER_PRUNE_DIRS for part in d.relative_to(run).parts):
            work_dirs.append(d)
    # 由浅到深处理，父目录先删掉就不用再管子目录
    work_dirs.sort(key=lambda p: len(p.parts))
    covered: list[Path] = []
    if not keep_work:
        for d in work_dirs:
            if any(d.is_relative_to(c) for c in covered):
                continue
            n, b = size_of(d), size_of(d)
            pruned.append({'path': str(d.relative_to(run)), 'kind': 'work_dir', 'bytes': b})
            pruned_bytes += b
            covered.append(d)
    for probe_out in run.glob('deployment/probes/*/out'):
        if any(probe_out.is_relative_to(c) for c in covered):
            continue
        b = size_of(probe_out)
        pruned.append({'path': str(probe_out.relative_to(run)), 'kind': 'probe_out', 'bytes': b})
        pruned_bytes += b
        covered.append(probe_out)
    for f in run.rglob('*'):
        if not f.is_file():
            continue
        rel = f.relative_to(run)
        if any(f.is_relative_to(c) for c in covered):
            continue
        if any(part in NEVER_PRUNE_DIRS for part in rel.parts):
            continue
        if f.suffix.lower() in BULK_SUFFIX and f.suffix.lower() not in KEEP_SUFFIX:
            b = f.stat().st_size
            pruned.append({'path': str(rel), 'kind': 'bulk_file', 'bytes': b})
            pruned_bytes += b
            covered.append(f)
    if not dry_run:
        for item in pruned:
            p = run / item['path']
            if p.is_dir():
                shutil.rmtree(p)
            elif p.exists():
                p.unlink()
    return pruned, pruned_bytes


def mark_ledgers(run: Path, pruned_paths: list[str]) -> dict:
    touched = {}
    for name in ('cases.json', 'comparison.json'):
        p = run / name
        if not p.exists():
            continue
        doc = json.loads(p.read_text(encoding='utf-8'))
        hits = 0
        for c in doc.get('cases') or []:
            raw = c.get('actual_output') or c.get('actual_path') or []
            aps = raw if isinstance(raw, list) else [raw]      # 规范允许列表
            aps = [str(x) for x in aps if x]
            if not aps:
                continue
            if any(any(a.startswith(pref) or pref in a for pref in pruned_paths) for a in aps):
                c['actual_pruned'] = True
                hits += 1
            # 证据列表只保留仍存在的路径（清理后不再指向已删的原始输出）
            ev = c.get('evidence') or []
            kept = [e for e in ev if (run / str(e)).exists()]
            if len(kept) != len(ev):
                c['evidence'] = kept
                hits += 1 if not c.get('actual_pruned') else 0
        p.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding='utf-8')
        touched[name] = hits
    return touched


NOTE_BEGIN, NOTE_END = '<!-- evidence-prune:begin -->', '<!-- evidence-prune:end -->'


def inject_note(report: Path, pruned_bytes: int, kept_bytes: int, items: int, actual_report: str) -> bool:
    if not report.exists():
        return False
    text = report.read_text(encoding='utf-8')
    if items:
        head = ("本次跑完用例后按 skill 约定清理了中间产出（逐 case 工作目录 `*/work/`、探针 `out/`、以及"
                "`registration/` 之外的大体积实体文件）："
                f"清理 {items} 项、{fmt(pruned_bytes)}，保留报告类证据 {fmt(kept_bytes)}。")
    else:
        head = ("本次未产生需要清理的中间产出（没有 `*/work/` 或大体积实体文件），"
                f"报告类证据共 {fmt(kept_bytes)}。")
    block = (f"{NOTE_BEGIN}\n### 证据保留口径\n\n{head}\n\n"
             "**逐 case 的检查数值与判定仍在** `cases.json`、`comparison.json` 与 "
             f"`{actual_report}` 里；被清理的只是可用原命令重跑复现的原始输出文件，清单见 "
             "`evidence-prune.json`。台账里被清理的 `actual_output` 已标记 `actual_pruned: true`。\n"
             f"{NOTE_END}")
    if NOTE_BEGIN in text:
        head, _, rest = text.partition(NOTE_BEGIN)
        _, _, tail = rest.partition(NOTE_END)
        text = head + block + tail
    else:
        idx = text.find('\n## 5.')
        text = (text[:idx] + '\n' + block + '\n' + text[idx:]) if idx != -1 else (text.rstrip() + '\n\n' + block + '\n')
    report.write_text(text, encoding='utf-8')
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='清理中间产出，只保留报告类证据')
    ap.add_argument('--run', required=True, help='asset-runs/<run_id> 目录')
    ap.add_argument('--report', help='validation-report.md（默认用 run 目录下的）')
    ap.add_argument('--keep-work', action='store_true', help='保留 */work/（只清大体积实体文件）')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)
    run = Path(args.run).expanduser().resolve()
    if not run.is_dir():
        print(f'run 目录不存在：{run}', file=sys.stderr)
        return 2
    before = size_of(run)
    pruned, pruned_bytes = collect(run, args.keep_work, args.dry_run)
    after = size_of(run) if not args.dry_run else max(before - pruned_bytes, 0)
    paths = [p['path'] for p in pruned]
    touched, mark_error = {}, None
    if not args.dry_run:
        try:
            touched = mark_ledgers(run, paths)
        except Exception as exc:  # noqa: BLE001
            mark_error = f'{type(exc).__name__}: {exc}'   # 已经删了文件，记录必须照写
            print(f'[warn] 台账标记失败（清理记录仍会落盘）：{mark_error}', file=sys.stderr)
    out_path = run / 'evidence-prune.json'
    prev = {}
    if out_path.exists():
        try:
            prev = json.loads(out_path.read_text(encoding='utf-8'))
        except Exception:  # noqa: BLE001
            prev = {}
    passes = list(prev.get('passes') or [])
    if prev and not passes and prev.get('pruned_items'):
        passes.append({'at': prev.get('pruned_at'), 'items': prev.get('pruned_items'),
                       'bytes': prev.get('pruned_bytes'), 'pruned': prev.get('pruned') or []})
    if not args.dry_run:
        passes.append({'at': now(), 'items': len(pruned), 'bytes': pruned_bytes, 'pruned': pruned})
    seen, merged = set(), []
    for ps in passes:
        for item in ps.get('pruned') or []:
            if item['path'] in seen:
                continue
            seen.add(item['path'])
            merged.append(item)
    total_items = sum(int(ps.get('items') or 0) for ps in passes)
    total_bytes = sum(int(ps.get('bytes') or 0) for ps in passes)
    scan_items, scan_bytes = len(pruned), pruned_bytes
    if args.dry_run:      # 预览：报的是“这一遍会删什么”，不是历史累计
        total_items, total_bytes = scan_items, scan_bytes
    note = False
    if not args.dry_run:
        candidates = ['validation-report.md', 'comparison.json', 'tool/correctness_report.json',
                      'data/data_cases_report.json']
        actual_report = next((c for c in candidates if (run / c).exists()), None)
        if actual_report is None:
            mds = sorted(p.name for p in run.glob('*.md'))
            actual_report = mds[0] if mds else '本 run 的报告文件'
        note = inject_note(Path(args.report) if args.report else run / 'validation-report.md',
                           total_bytes, after, total_items, actual_report)
    payload = {
        'tool': 'prune_evidence.py',
        'run': run.name,
        'pruned_at': now(),
        'dry_run': args.dry_run,
        'ledger_mark_error': mark_error,
        'keep_work': args.keep_work,
        'bytes_before': before,
        'bytes_after': after,
        'pruned_items': total_items,
        'pruned_bytes': total_bytes,
        'scan_items': scan_items,
        'scan_bytes': scan_bytes,
        'passes': passes,
        'kept_rule': ('保留 *.md/*.json/*.txt/*.log/*.xlsx、registration/、scripts/、deployment/probes/*/probe.json；'
                      '清理 */work/、deployment/probes/*/out/ 与 registration/ 之外的大体积实体文件'),
        'pruned': merged,
        'ledger_marks': touched,
        'report_note_injected': note,
    }
    if not args.dry_run:      # --dry-run 只预览，不落盘、不改台账
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: payload[k] for k in ('run', 'pruned_items', 'pruned_bytes', 'bytes_before',
                                              'bytes_after', 'ledger_marks', 'report_note_injected')},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
