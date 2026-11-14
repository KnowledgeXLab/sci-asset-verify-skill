#!/usr/bin/env python3
"""检查 skill 类型资产的 validate/ 下是否有测试说明文档，且写清 harness / 模型 / 通过门槛。

为什么必须有：skill 型资产怎么测（用什么 harness、要不要模型、算不算通过）不能靠校验方猜。
提供方在 `validate/test-spec.json` 里写清楚，校验方照着执行；找不到建议的 harness 可换系统里已有的替代，
但要在报告里注明；Sage A1.5 测试沿用同一 harness。

机器可读优先：`validate/test-spec.json`（推荐，见 assets/skill-test-spec.schema.json）；
退而求其次 `validate/test-spec.md`，需含「## Harness」「## 模型」「## 通过门槛」三个小节。

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

SPEC_JSON, SPEC_MD = 'validate/test-spec.json', 'validate/test-spec.md'
HARNESS_KINDS = ('cli', 'mcp', 'api', 'agent', 'notebook')
MD_SECTIONS = ('harness', '模型', '通过门槛')


def check_json(adir: Path, doc: dict) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    for key in ('harness', 'model', 'gate'):
        if not isinstance(doc.get(key), dict):
            errs.append(f'缺少 {key} 段（harness/model/gate 三段必填）')
    h = doc.get('harness') if isinstance(doc.get('harness'), dict) else {}
    if not h.get('name'):
        errs.append('harness.name 为空：写清建议用哪个 harness')
    if h.get('kind') not in HARNESS_KINDS:
        errs.append(f'harness.kind 应为 {"/".join(HARNESS_KINDS)}，实际 {h.get("kind")!r}')
    if h.get('kind') == 'cli':
        ep = str(h.get('entrypoint') or '')
        if not ep:
            errs.append('harness.kind=cli 时 harness.entrypoint 必填')
        else:
            f = (adir / ep).resolve()
            if adir.resolve() not in f.parents and f != adir.resolve():
                errs.append(f'harness.entrypoint 越出资产目录：{ep}')
            elif not f.is_file():
                errs.append(f'harness.entrypoint 不存在：{ep}')
    m = doc.get('model') if isinstance(doc.get('model'), dict) else {}
    if not isinstance(m.get('required'), bool):
        errs.append('model.required 必须是布尔值（是否需要特定大模型）')
    elif m['required'] and not m.get('id'):
        errs.append('model.required=true 时 model.id 必填（如 Sage-A1.5）')
    g = doc.get('gate') if isinstance(doc.get('gate'), dict) else {}
    if g.get('type') not in ('script', 'metric'):
        errs.append(f'gate.type 应为 script 或 metric，实际 {g.get("type")!r}')
    if g.get('type') == 'script':
        sp = str(g.get('script') or '')
        if not sp:
            errs.append('gate.type=script 时 gate.script 必填')
        else:
            f = (adir / sp).resolve()
            if adir.resolve() not in f.parents and f != adir.resolve():
                errs.append(f'gate.script 越出资产目录：{sp}')
            elif not f.is_file():
                errs.append(f'gate.script 不存在：{sp}')
    if g.get('type') == 'metric':
        ms = g.get('metrics')
        if not isinstance(ms, list) or not ms:
            errs.append('gate.type=metric 时 gate.metrics 不能为空')
        else:
            for i, mt in enumerate(ms):
                if not isinstance(mt, dict) or not mt.get('id'):
                    errs.append(f'gate.metrics[{i}] 缺 id')
                elif not isinstance(mt.get('threshold'), (int, float)):
                    errs.append(f'gate.metrics[{i}].threshold 应为数值')
    sage = doc.get('sage') if isinstance(doc.get('sage'), dict) else {}
    if sage and sage.get('same_harness') is False:
        warns.append('sage.same_harness=false：Sage A1.5 测试应沿用同一 harness，若确需更换请在报告里说明')
    return errs, warns


def check_md(adir: Path, text: str) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    heads = [h.strip().lower() for h in re.findall(r'^#{2,4}\s*(.+)$', text, re.M)]
    for key in MD_SECTIONS:
        if not any(key.lower() in h for h in heads):
            errs.append(f'缺少「{key}」小节')
    warns.append('用的是 Markdown 说明文档；建议同时给 validate/test-spec.json，便于机器核对')
    return errs, warns


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='检查 skill 资产的测试说明文档（harness/模型/通过门槛）')
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
        spec_json, spec_md = adir / SPEC_JSON, adir / SPEC_MD
        errs, warns, source = [], [], None
        if spec_json.is_file():
            source = SPEC_JSON
            try:
                errs, warns = check_json(adir, json.loads(spec_json.read_text(encoding='utf-8')))
            except Exception as exc:  # noqa: BLE001
                errs = [f'{SPEC_JSON} 不是合法 JSON：{exc}']
        elif spec_md.is_file():
            source = SPEC_MD
            errs, warns = check_md(adir, spec_md.read_text(encoding='utf-8'))
        else:
            errs = [f'缺少测试说明文档：应在 {rel}/validate/ 下提供 test-spec.json（推荐）或 test-spec.md，'
                    '写明建议的 harness、模型与通过门槛']
        results.append({'asset_dir': rel, 'asset_id': asset.get('asset_id'),
                        'spec': source, 'errors': errs, 'warnings': warns, 'ok': not errs})
        flag = 'OK  ' if not errs else 'FAIL'
        print(f'[{flag}] {rel}（{asset.get("name") or asset.get("asset_id")}）spec={source or "缺失"}')
        for e in errs:
            print(f'      error  {e}', file=sys.stderr)
        for w in warns:
            print(f'      warn   {w}', file=sys.stderr)

    bad = [r for r in results if not r['ok']]
    payload = _stamp({'tool': 'check_skill_spec.py', 'package': str(root),
                      'skills': len(results), 'failed': len(bad),
                      'results': results,
                      'rule': 'skill 资产的 validate/ 必须有 test-spec.json（写清 harness / 模型 / 通过门槛）；'
                              '找不到建议 harness 可替代但须在报告注明；Sage A1.5 沿用同一 harness'})
    print(f'合计 {len(results)} 个 skill 资产：通过 {len(results) - len(bad)}，缺文档或要素不全 {len(bad)}')
    if args.json_out:
        p = Path(args.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return 1 if (args.check and bad) else 0


if __name__ == '__main__':
    raise SystemExit(main())
