#!/usr/bin/env python3
"""检查 skill 资产的 `validate/readme.md`：写明 agent（harness + model）、怎么测、通过门槛。

为什么要求 Markdown + 上 agent：skill 是**给 agent 用的**，验收必须在 agent 环境里让 agent 使用该 skill
完成一个任务、再看产物是否达标；只跑 validate/ 里的脚本不能算 skill 的验收。文档要写清：
三节：① Agent（agent harness + agent model）② 怎么测（给 agent 什么任务、输入什么、输出什么）
③ 通过门槛（指标阈值 或 对 agent 产物的验证脚本）。

用法：
  python scripts/check_skill_spec.py --package <包根> [--json-out FILE] [--check]

退出码：0 通过（或包内无 skill 资产）；1 有 skill 资产缺文档/要素不全（仅 --check 时）；2 参数问题。
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

try:
    from _layout import package_asset_dirs
except Exception:  # noqa: BLE001
    def package_asset_dirs(root):
        out = []
        for f in sorted(Path(root).rglob('asset.json')):
            if (f.parent / '.mounted-external.json').is_file():
                continue
            if {'asset-runs', '.git', '__pycache__'} & set(f.relative_to(root).parts):
                continue
            out.append(f.parent)
        return out

SPEC_MD = 'validate/readme.md'
OLD_NAMES = ('validate/test-spec.md', 'validate/test-spec.json')  # 旧文件名，提示改到 readme.md
SECTIONS = [
    ('agent', ('agent', 'harness', '模型'), ('Agent',)),
    ('task', ('任务', '怎么测', '测试任务'), ('任务（怎么测）',)),
    ('gate', ('门槛', '通过标准', '验收标准'), ('通过门槛',)),
]
PLACEHOLDER = re.compile(r'<[^>\n]{1,80}>')


def split_sections(text: str) -> dict:
    """按二级/三级标题切分，返回 {小写标题: 正文}。"""
    out, cur, buf = {}, None, []
    for line in text.splitlines():
        m = re.match(r'^#{2,4}\s*(.+?)\s*$', line)
        if m:
            if cur is not None:
                out[cur] = '\n'.join(buf).strip()
            cur, buf = m.group(1).lower(), []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        out[cur] = '\n'.join(buf).strip()
    return out


def find(sections: dict, keywords) -> tuple[str, str]:
    for head, body in sections.items():
        if any(k in head for k in keywords):
            return head, body
    return '', ''


def check_md(text: str) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    sections = split_sections(text)
    bodies = {}
    for key, head_kw, _ in SECTIONS:
        head, body = find(sections, head_kw)
        bodies[key] = body
        if not head:
            errs.append(f'缺少「{head_kw[0]}」小节')
        elif not body.strip():
            errs.append(f'「{head}」小节是空的')
        elif PLACEHOLDER.search(body) and len(PLACEHOLDER.sub('', body).strip()) < 10:
            errs.append(f'「{head}」小节只有模板占位符，没有实填内容')

    a = bodies.get('agent', '')
    if a and not re.search(r'agent|智能体|dsh|claude|assistant|平台', a, re.I):
        errs.append('「Agent」小节要写 **agent harness**（skill 给 agent 用，必须上 agent 测试；'
                    '写清如 DSH / Claude Code / 平台 Agent）')
    if a and not re.search(r'model|模型', a, re.I):
        errs.append('「Agent」小节还要写 **agent model**（建议模型 + 是否必须特定模型）')

    t = bodies.get('task', '')
    if t:
        if not re.search(r'输入|input', t, re.I):
            errs.append('「测试任务」里要写清给 agent 的**输入**（文件/参数/提示词）')
        if not re.search(r'输出|产物|output', t, re.I):
            errs.append('「测试任务」里要写清**期望输出**（产物形态）')

    g = bodies.get('gate', '')
    if g and not re.search(r'validate/|脚本|指标|阈值|threshold', g, re.I):
        errs.append('「通过门槛」要给出指标阈值或对产物的验证脚本（如 validate/validate.py）')

    return errs, warns


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='检查 skill 资产的测试说明文档（agent harness / model / 怎么测 / 门槛）')
    ap.add_argument('--package', required=True)
    ap.add_argument('--json-out')
    ap.add_argument('--check', action='store_true', help='有缺失时退出码 1')
    args = ap.parse_args(argv)

    root = Path(args.package).expanduser().resolve()
    if not root.is_dir():
        print(f'包不存在：{root}', file=sys.stderr)
        return 2

    results = []
    for adir in package_asset_dirs(root):
        try:
            asset = json.loads((adir / 'asset.json').read_text(encoding='utf-8'))
        except Exception:  # noqa: BLE001
            continue
        if str(asset.get('asset_type')) != 'skill':
            continue
        rel = str(adir.relative_to(root)) or '.'
        spec_md = adir / SPEC_MD
        errs, warns = [], []
        if spec_md.is_file():
            errs, warns = check_md(spec_md.read_text(encoding='utf-8'))
        elif any((adir / n).is_file() for n in OLD_NAMES):
            errs = [f'验证说明的固定文件名是 {rel}/validate/readme.md（test-spec.* 已不再接受），请改名']
        else:
            errs = [f'缺少验证说明：{rel}/validate/readme.md，需写清 Agent（harness+model）、'
                    '怎么测（任务/输入/输出）与通过门槛（模板见 assets/skill-test-spec.template.md）']
        results.append({'asset_dir': rel, 'asset_id': asset.get('asset_id'),
                        'spec': SPEC_MD if spec_md.is_file() else None,
                        'errors': errs, 'warnings': warns, 'ok': not errs})
        flag = 'OK  ' if not errs else 'FAIL'
        print(f'[{flag}] {rel}（{asset.get("name") or asset.get("asset_id")}）')
        for e in errs:
            print(f'      error  {e}', file=sys.stderr)
        for w in warns:
            print(f'      warn   {w}', file=sys.stderr)

    bad = [r for r in results if not r['ok']]
    payload = _stamp({'tool': 'check_skill_spec.py', 'package': str(root),
                      'spec_format': 'markdown', 'skills': len(results), 'failed': len(bad),
                      'results': results,
                      'rule': 'skill 给 agent 用，验收必须上 agent：validate/readme.md 三节——'
                              'Agent（agent harness + agent model）、测试任务（任务/输入/输出）、'
                              '通过门槛（指标或对 agent 产物的验证脚本）；'
                              '缺建议 harness 可替代但须在报告注明'})
    print(f'合计 {len(results)} 个 skill 资产：通过 {len(results) - len(bad)}，缺文档或要素不全 {len(bad)}')
    if args.json_out:
        p = Path(args.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return 1 if (args.check and bad) else 0


if __name__ == '__main__':
    raise SystemExit(main())
