#!/usr/bin/env python3
"""确定性探测：把「资产包里到底有什么事实」抽成固定 JSON，供固定检查清单判定。

为什么要有它：核验结论的可复现性取决于**跑的是不是同一批检查**。自由发挥的"补充检查"每次都不一样，
三次核验就会得到三套不同的 P1。本脚本只产出**事实**（不判严重度），同一份包跑多少次输出都一致；
`assets/audit-checks.json` 再把事实映射到固定严重度。

用法：
  python scripts/probe_package.py --package <包根> [--json-out <run>/probe-report.json]

输出（节选）：
  package_summary / governance_profile / lifecycle_profile / artifact_integrity /
  cases_profile / validate_readme_profile / external_refs_profile / tool_contract_profile

确定性：`checks` 里不含时间戳，字段顺序固定；同一份包两次运行结果逐字节相同（`--print-hash` 给出指纹）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

try:
    from _layout import package_asset_dirs
except Exception:  # noqa: BLE001
    def package_asset_dirs(root):
        out = []
        for f in sorted(Path(root).rglob('asset.json')):
            rel = f.relative_to(root)
            if {'asset-runs', '.git', '__pycache__'} & set(rel.parts):
                continue
            if (f.parent / '.mounted-external.json').is_file():
                continue
            out.append(f.parent)
        return out

try:
    from _version import stamp as _stamp
except Exception:  # noqa: BLE001
    def _stamp(payload):
        return payload


def load(p: Path):
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:  # noqa: BLE001
        return None


def probe_package(root: Path, assets: list) -> dict:
    kinds = Counter()
    metas = {}
    for d in assets:
        doc = load(d / 'asset.json') or {}
        kinds[str(doc.get('asset_type') or 'unknown')] += 1
        metas[d.name] = doc
    top = sorted(p.name for p in root.iterdir() if p.is_file())
    return {
        'asset_count': len(assets),
        'asset_types': dict(sorted(kinds.items())),
        'asset_dirs': sorted(metas),
        'root_files': top,
        'has_catalog_json': (root / 'catalog.json').is_file(),
        'root_markdown': [f for f in top if f.lower().endswith(('.md', '.txt'))],
        'has_asset_runs': (root / 'asset-runs').is_dir(),
    }


def probe_governance(metas: dict) -> dict:
    lic, acc, risk = Counter(), Counter(), Counter()
    for name, doc in metas.items():
        gov = doc.get('governance') or {}
        lic[str(gov.get('license') or '')[:80]] += 1
        acc[str(gov.get('access') or '')] += 1
        risk[str(gov.get('risk_level') or '')] += 1
    return {'license': dict(sorted(lic.items())), 'access': dict(sorted(acc.items())),
            'risk_level': dict(sorted(risk.items()))}


def probe_lifecycle(metas: dict) -> dict:
    lc = Counter(str((d or {}).get('lifecycle') or '') for d in metas.values())
    return {'distribution': dict(sorted(lc.items()))}


def probe_artifacts(root: Path, assets: list, metas: dict) -> dict:
    missing, size_bad, sha_bad, checked = [], [], [], 0
    for d in assets:
        for i, a in enumerate((metas[d.name].get('spec') or {}).get('artifacts') or []):
            uri = str((a or {}).get('uri') or '')
            if not uri or re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', uri):
                continue
            f = (d / uri)
            checked += 1
            if not f.is_file():
                missing.append(f'{d.name}:{uri}')
                continue
            if isinstance(a.get('size_bytes'), int) and f.stat().st_size != a['size_bytes']:
                size_bad.append(f'{d.name}:{uri}')
            if isinstance(a.get('sha256'), str) and len(a['sha256']) == 64:
                if hashlib.sha256(f.read_bytes()).hexdigest() != a['sha256'].lower():
                    sha_bad.append(f'{d.name}:{uri}')
    return {'artifacts_checked': checked, 'missing': sorted(missing),
            'size_mismatch': sorted(size_bad), 'sha_mismatch': sorted(sha_bad)}


def probe_cases(assets: list) -> dict:
    rows = {}
    for d in assets:
        vdir = d / 'validate'
        cases = vdir / 'cases.json'
        acc = d / 'acceptance.json'
        entry = {'has_validate_dir': vdir.is_dir(), 'has_cases_json': cases.is_file(),
                 'has_acceptance_json': acc.is_file(), 'case_count': 0,
                 'acceptance_missing_paths': []}
        if cases.is_file():
            doc = load(cases) or {}
            entry['case_count'] = len(doc.get('cases') or [])
        if acc.is_file():
            doc = load(acc) or {}
            refs = re.findall(r'[\w./-]+\.(?:csv|json|nc|tif|tiff|h5|npy|npz|txt|py)', json.dumps(doc, ensure_ascii=False))
            entry['acceptance_missing_paths'] = sorted({r for r in refs if not (d / r).exists()})
        rows[d.name] = entry
    return {'assets': dict(sorted(rows.items())),
            'assets_with_cases': sum(1 for v in rows.values() if v['case_count'] > 0),
            'assets_without_cases': sorted(k for k, v in rows.items() if v['case_count'] == 0)}


def probe_validate_readme(assets: list, metas: dict) -> dict:
    rows = {}
    for d in assets:
        f = d / 'validate' / 'readme.md'
        if not f.is_file():
            rows[d.name] = {'present': False, 'is_skill': str((metas.get(d.name) or {}).get('asset_type')) == 'skill'}
            continue
        text = f.read_text(encoding='utf-8', errors='replace')
        heads = [h.strip().lower() for h in re.findall(r'^#{2,4}\s*(.+)$', text, re.M)]
        rows[d.name] = {'present': True,
                        'is_skill': str((metas.get(d.name) or {}).get('asset_type')) == 'skill',
                        'has_agent_section': any(k in h for h in heads for k in ('agent', 'harness')),
                        'has_task_section': any(k in h for h in heads for k in ('任务', '怎么测')),
                        'has_gate_section': any(k in h for h in heads for k in ('门槛', '验收')),
                        'mentions_artifact': 'artifact' in text.lower() or '产物' in text}
    return {'assets': dict(sorted(rows.items())),
            'complete': sorted(k for k, v in rows.items()
                               if v.get('present') and v.get('has_agent_section')
                               and v.get('has_task_section') and v.get('has_gate_section'))}


def probe_external_refs(assets: list) -> dict:
    rows = {}
    for d in assets:
        f = d / 'external_assets.json'
        if not f.is_file():
            continue
        doc = load(f) or {}
        items = doc.get('assets') or []
        bad = []
        for a in items:
            tp = str((a or {}).get('target_path') or '')
            if not tp or tp.startswith('/') or '..' in Path(tp).parts or '\\' in tp:
                bad.append(tp or '(空)')
        rows[d.name] = {'declared': len(items), 'invalid_target_path': sorted(bad)}
    return {'assets': dict(sorted(rows.items()))}


def probe_tool_contract(assets: list, metas: dict) -> dict:
    """工具类资产：声明的错误码/错误名 vs 实现里出现的，做成事实对比（不判对错）。"""
    rows = {}
    for d in assets:
        if str(metas[d.name].get('asset_type')) != 'tool':
            continue
        impl_text = ''
        for f in sorted(d.rglob('*.py')):
            impl_text += f.read_text(encoding='utf-8', errors='replace')
        decl_text = ''
        for f in sorted(list(d.rglob('*.json')) + list(d.rglob('*.md'))):
            if f.name == 'asset.json' or '.mounted-external' in f.name:
                continue
            decl_text += f.read_text(encoding='utf-8', errors='replace')
        impl_rc = sorted(set(int(m) for m in re.findall(r'(?:sys\.exit|SystemExit)\((\d)\)', impl_text)))
        impl_errs = sorted(set(re.findall(r"['\"]error['\"]\s*:\s*['\"]([a-z0-9_]+)['\"]", impl_text)))
        decl_errs = sorted(set(re.findall(r'\b([a-z]+(?:_[a-z]+)+)\b', decl_text)))
        rows[d.name] = {'impl_exit_codes': impl_rc, 'impl_error_names': impl_errs,
                        'impl_error_names_not_declared': sorted(set(impl_errs) - set(decl_errs))}
    return {'assets': dict(sorted(rows.items()))}


def checks_of(root: Path) -> dict:
    assets = package_asset_dirs(root)
    metas = {}
    for d in assets:
        metas[d.name] = load(d / 'asset.json') or {}
    return {
        'package_summary': probe_package(root, assets),
        'governance_profile': probe_governance(metas),
        'lifecycle_profile': probe_lifecycle(metas),
        'artifact_integrity': probe_artifacts(root, assets, metas),
        'cases_profile': probe_cases(assets),
        'validate_readme_profile': probe_validate_readme(assets, metas),
        'external_refs_profile': probe_external_refs(assets),
        'tool_contract_profile': probe_tool_contract(assets, metas),
    }


RISKY_LICENSE = ('仅限内部', '仅限项目内部', '禁止再分发', '禁止修改', '不得再分发', 'internal only', 'no redistribution')
CHECKLIST = Path(__file__).resolve().parents[1] / 'assets' / 'audit-checks.json'


def evaluate(checks: dict, intent: str = 'delivery') -> dict:
    """按固定检查清单把事实映射成**固定严重度**的结论（同一份包结果恒定）。"""
    try:
        spec = json.loads(CHECKLIST.read_text(encoding='utf-8'))
    except Exception:  # noqa: BLE001
        return {'error': '读不到 assets/audit-checks.json', 'findings': []}
    checks = dict(checks)
    # skill 类资产的说明文档完备性（只对 skill 资产判）
    rmp = checks.get('validate_readme_profile', {})
    cases = checks.get('cases_profile', {}).get('assets', {})
    skill_incomplete = sorted(
        name for name, info in (rmp.get('assets') or {}).items()
        if info.get('is_skill') and not (info.get('present') and info.get('has_agent_section')
                                        and info.get('has_task_section') and info.get('has_gate_section')))
    findings = []

    def add(cid, fact, detail):
        meta = next((c for c in spec['checks'] if c['id'] == cid), {})
        findings.append({'check_id': cid, 'level': meta.get('level'), 'name': meta.get('name'),
                         'status': 'fail', 'detail': detail, 'criterion': meta.get('criterion')})

    art = checks.get('artifact_integrity') or {}
    bad_art = {'missing': art.get('missing') or [], 'size_mismatch': art.get('size_mismatch') or [],
               'sha_mismatch': art.get('sha_mismatch') or []}
    if any(bad_art.values()):
        add('CHK-ART-01', 'artifact_integrity', bad_art)

    no_cases = (checks.get('cases_profile') or {}).get('assets_without_cases') or []
    if no_cases:
        add('CHK-CASE-01', 'cases_profile', {'assets_without_cases': no_cases})

    missing_acc = {k: v['acceptance_missing_paths'] for k, v in cases.items() if v.get('acceptance_missing_paths')}
    if missing_acc:
        add('CHK-CASE-02', 'cases_profile', missing_acc)

    lic = (checks.get('governance_profile') or {}).get('license') or {}
    if intent == 'delivery' and lic and all(any(k in text for k in RISKY_LICENSE) for text in lic):
        add('CHK-LIC-01', 'governance_profile', {'license': lic, 'intent': intent})

    ext = (checks.get('external_refs_profile') or {}).get('assets') or {}
    bad_ext = {k: v['invalid_target_path'] for k, v in ext.items() if v.get('invalid_target_path')}
    if bad_ext:
        add('CHK-EXT-01', 'external_refs_profile', bad_ext)

    tool = (checks.get('tool_contract_profile') or {}).get('assets') or {}
    bad_tool = {k: v['impl_error_names_not_declared'] for k, v in tool.items()
                if v.get('impl_error_names_not_declared')}
    if bad_tool:
        add('CHK-TOOL-01', 'tool_contract_profile', bad_tool)

    if skill_incomplete:
        add('CHK-RM-01', 'validate_readme_profile', {'skill_assets_incomplete': skill_incomplete})

    pkg = checks.get('package_summary') or {}
    if not pkg.get('has_catalog_json') and not pkg.get('root_markdown'):
        add('CHK-SPEC-01', 'package_summary', {'root_files': pkg.get('root_files') or []})

    return {'intent': intent, 'findings': findings,
            'summary': {'P0': sum(1 for f in findings if f['level'] == 'P0'),
                        'P1': sum(1 for f in findings if f['level'] == 'P1'),
                        'P2': sum(1 for f in findings if f['level'] == 'P2')},
            'note': '结论只来自本清单；清单外的发现只能记 OBS（观察），不计 P0/P1/P2，也不进通过率'}


def fingerprint(checks: dict) -> str:
    return hashlib.sha256(json.dumps(checks, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='确定性探测：抽取资产包事实，供固定检查清单判定')
    ap.add_argument('--package', required=True)
    ap.add_argument('--json-out')
    ap.add_argument('--print-hash', action='store_true', help='只打印事实指纹（用于验证可复现性）')
    ap.add_argument('--evaluate', action='store_true', help='按 assets/audit-checks.json 输出固定严重度的结论')
    ap.add_argument('--intent', choices=['delivery', 'review'], default='delivery',
                    help='交付用途：delivery=对外交付（默认）/ review=内部评审')
    args = ap.parse_args(argv)

    root = Path(args.package).expanduser().resolve()
    if not root.is_dir():
        print(f'包不存在：{root}', file=sys.stderr)
        return 2
    checks = checks_of(root)
    fp = fingerprint(checks)
    if args.print_hash:
        print(fp)
        return 0
    payload = _stamp({'tool': 'probe_package.py', 'package': str(root),
                      'probe_fingerprint': fp, 'checks': checks})
    if args.evaluate:
        payload['verdict'] = evaluate(checks, args.intent)
    print(f'探测完成：{checks["package_summary"]["asset_count"]} 个资产，指纹 {fp[:16]}')
    if args.evaluate:
        v = payload['verdict']
        print(f"按固定清单判定（intent={v['intent']}）：P0 {v['summary']['P0']} / P1 {v['summary']['P1']} / P2 {v['summary']['P2']}")
        for f in v['findings']:
            print(f"  [{f['level']}] {f['check_id']} {f['name']}")
    if args.json_out:
        p = Path(args.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
