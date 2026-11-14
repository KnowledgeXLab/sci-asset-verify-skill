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


META_KEYS = ('skill', 'skill_version', 'skill_commit', 'skill_version_source', 'generated_at')


def _parse_front(text: str):
    """拆出 YAML front matter（仅支持 flat key: value），返回 (meta_dict, body)。"""
    if text.startswith('---\n'):
        end = text.find('\n---\n', 4)
        if end != -1:
            meta = {}
            for line in text[4:end].splitlines():
                if ':' in line:
                    k, _, v = line.partition(':')
                    meta[k.strip()] = v.strip()
            return meta, text[end + 5:]
    return {}, text


def write_meta(report: Path, info: dict) -> str:
    """把版本与生成时间写进文件开头的 front matter，正文不再出现。"""
    raw = report.read_text(encoding='utf-8') if report.exists() else ''
    action = 'created'
    if BEGIN in raw:                       # 旧的文末小节：迁移时删除
        head, _, rest = raw.partition(BEGIN)
        _, _, tail = rest.partition(END)
        raw = (head.rstrip() + '\n' + tail.lstrip('\n')).rstrip() + '\n'
        action = 'migrated'
    meta, body = _parse_front(raw)
    if meta:
        action = 'updated' if action == 'created' else action
    meta.update({
        'skill': info['name'],
        'skill_version': info['version'],
        'skill_commit': info.get('commit') or 'unknown',
        'skill_version_source': info.get('version_source') or 'unknown',
        'generated_at': now(),
    })
    lines = ['---'] + [f'{k}: {meta[k]}' for k in META_KEYS if k in meta]
    for k in meta:
        if k not in META_KEYS:
            lines.append(f'{k}: {meta[k]}')
    lines.append('---')
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text('\n'.join(lines) + '\n\n' + body.lstrip('\n'), encoding='utf-8')
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
    action = write_meta(report, info)
    res = {'tool': 'stamp_report.py', 'run': run.name, 'report': str(report),
           'report_action': action, 'skill': info,
           'json': {'stamped': [], 'skipped': []} if args.no_json else stamp_jsons(run, info)}
    print(f"报告 front matter：{action} {report.name}")
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
