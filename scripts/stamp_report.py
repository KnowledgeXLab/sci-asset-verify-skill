#!/usr/bin/env python3
"""把「校验工具版本」写进报告末尾，并给 run 内所有证据 JSON 补 skill 版本戳。

为什么需要：一次核验要能回答「用的是哪个版本的校验 skill」。生产者脚本各自会写自己的 JSON，
但 `cases.json`/`comparison.json`/`state.json` 这类由执行方整理的台账不会自带版本，报告也需要显式声明。
本脚本是收尾时的统一入口，幂等——重复跑只更新内容，不追加重复小节。

用法：
  python scripts/stamp_report.py --run <asset-runs/run_id> [--report <md>] [--json-out <file>]

退出码：0 正常；2 参数/IO 问题。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from _version import skill_info, now
except Exception:  # noqa: BLE001
    def skill_info():
        return {'name': 'sci-asset-verify-skill', 'version': 'unknown', 'commit': None,
                'version_source': 'unknown'}

    def now():
        return ''

BEGIN, END = '<!-- validation-tool:begin -->', '<!-- validation-tool:end -->'
SKIP_PARTS = {'work', 'node_modules', '.git', '__pycache__'}
MAX_JSON = 2 * 1024 * 1024      # 超过 2MB 的 JSON 不碰（多为数据实体）


def render(info: dict) -> str:
    commit = info.get('commit') or '（无 git 信息，按 VERSION 文件）'
    return (f"{BEGIN}\n## 校验工具版本\n\n"
            f"| 项 | 值 |\n|---|---|\n"
            f"| 校验 skill | `{info['name']}` |\n"
            f"| 版本 | `{info['version']}` |\n"
            f"| 提交 | `{commit}` |\n"
            f"| 版本来源 | {info.get('version_source')} |\n"
            f"| 写入时间 | {now()} |\n\n"
            f"本次核验的全部结论与证据均出自上述版本；证据 JSON 内另有 `skill` 字段可逐份核对。\n"
            f"{END}")


def write_report(report: Path, info: dict) -> str:
    block = render(info)
    text = report.read_text(encoding='utf-8') if report.exists() else ''
    if BEGIN in text:
        head, _, rest = text.partition(BEGIN)
        _, _, tail = rest.partition(END)
        text = (head + block + tail).rstrip() + '\n'
        action = 'updated'
    else:
        text = (text.rstrip() + '\n\n' + block + '\n') if text.strip() else block + '\n'
        action = 'appended'
    report.write_text(text, encoding='utf-8')
    return action


def stamp_jsons(run: Path, info: dict) -> dict:
    touched, skipped = [], []
    for f in sorted(run.rglob('*.json')):
        rel = f.relative_to(run)
        if SKIP_PARTS & set(rel.parts):
            skipped.append(str(rel))
            continue
        try:
            if f.stat().st_size > MAX_JSON:
                skipped.append(str(rel))
                continue
            doc = json.loads(f.read_text(encoding='utf-8'))
        except Exception:  # noqa: BLE001
            skipped.append(str(rel))
            continue
        if not isinstance(doc, dict):
            skipped.append(str(rel))     # 数组型（cases 列表等）不改结构
            continue
        if doc.get('skill') == info:
            continue
        doc['skill'] = info
        f.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        touched.append(str(rel))
    return {'stamped': touched, 'skipped': skipped}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='写入校验工具版本（报告末尾 + 证据 JSON）')
    ap.add_argument('--run', required=True, help='asset-runs/<run_id>')
    ap.add_argument('--report', help='报告路径，默认 <run>/validation-report.md')
    ap.add_argument('--no-json', action='store_true', help='只写报告，不动 JSON')
    ap.add_argument('--version', help='补录历史批次时显式指定版本（正常流程不要用）')
    ap.add_argument('--commit', help='补录历史批次时显式指定提交号')
    ap.add_argument('--json-out')
    args = ap.parse_args(argv)

    run = Path(args.run).expanduser().resolve()
    if not run.is_dir():
        print(f'run 目录不存在：{run}', file=sys.stderr)
        return 2
    info = skill_info()
    if args.version or args.commit:
        info = dict(info)
        if args.version:
            info['version'] = args.version
            info['version_source'] = 'manual:--version'
        if args.commit:
            info['commit'] = args.commit
    report = Path(args.report).expanduser().resolve() if args.report else run / 'validation-report.md'
    action = write_report(report, info)
    res = {'tool': 'stamp_report.py', 'run': run.name, 'report': str(report),
           'report_action': action, 'skill': info,
           'json': {'stamped': [], 'skipped': []} if args.no_json else stamp_jsons(run, info)}
    print(f"报告：{action} {report.name}")
    print(f"skill：{info['name']} {info['version']}"
          f"（commit {info.get('commit') or '—'}，来源 {info.get('version_source')}）")
    if not args.no_json:
        print(f"证据 JSON：打戳 {len(res['json']['stamped'])} 份，跳过 {len(res['json']['skipped'])} 份")
    if args.json_out:
        p = Path(args.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
