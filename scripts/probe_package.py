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
            from urllib.parse import unquote
            f = (d / uri)
            if not f.is_file():
                f = (d / unquote(uri))
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


def probe_data_declarations(assets: list, metas: dict) -> dict:
    """data 资产：metadata.json 的声明 vs 实体（记录数/列名/bbox/网格/公式复算）。"""
    import csv as _csv
    import math as _math

    rows = {}
    for d in assets:
        if str((metas.get(d.name) or {}).get('asset_type')) != 'data':
            continue
        meta = load(d / 'metadata.json')
        if not isinstance(meta, dict):
            rows[d.name] = {'has_metadata': False}
            continue
        payload = None
        for cand in sorted(d.rglob('*.csv')):
            payload = cand
            break
        info = {'has_metadata': True, 'payload': str(payload.relative_to(d)) if payload else None,
                'records_mismatch': None, 'columns_mismatch': None,
                'bbox_mismatch': None, 'grid_incomplete': None, 'formula_max_dev': None,
                'formula_tolerance': (meta.get('formula') or {}).get('tolerance')}
        if payload is None:
            rows[d.name] = info
            continue
        with payload.open(encoding='utf-8', errors='replace') as f:
            data = list(_csv.DictReader(f))
        if isinstance(meta.get('records'), int):
            info['records_mismatch'] = None if len(data) == meta['records'] else {'declared': meta['records'], 'actual': len(data)}
        if isinstance(meta.get('columns'), list):
            cols = list(data[0].keys()) if data else []
            info['columns_mismatch'] = None if cols == meta['columns'] else {'declared': meta['columns'], 'actual': cols}
        if isinstance(meta.get('bbox'), dict) and data and 'x' in data[0] and 'y' in data[0]:
            xs = [float(r['x']) for r in data]
            ys = [float(r['y']) for r in data]
            bx, by = meta['bbox'].get('x'), meta['bbox'].get('y')
            if bx and by and ([min(xs), max(xs)] != bx or [min(ys), max(ys)] != by):
                info['bbox_mismatch'] = {'declared': meta['bbox'], 'actual': {'x': [min(xs), max(xs)], 'y': [min(ys), max(ys)]}}
        grid = meta.get('grid') or {}
        if grid.get('nx') and grid.get('ny') and data and 'x' in data[0] and 'y' in data[0]:
            cells = {(int(float(r['x'])), int(float(r['y']))) for r in data}
            want = {(x, y) for x in range(int(grid['nx'])) for y in range(int(grid['ny']))}
            if cells != want:
                info['grid_incomplete'] = {'missing': len(want - cells), 'extra': len(cells - want)}
        formula = meta.get('formula') or {}
        if formula.get('expr') and data and {'x', 'y'} <= set(data[0]) and 'value' in data[0]:
            ns0 = {'sin': _math.sin, 'cos': _math.cos, 'sqrt': _math.sqrt, 'pi': _math.pi, 'exp': _math.exp}
            base = int(formula.get('i_base', 1))
            worst = 0.0
            try:
                for k, r in enumerate(data, start=base):
                    env = dict(ns0, x=float(r['x']), y=float(r['y']), i=k)
                    worst = max(worst, abs(eval(formula['expr'], {'__builtins__': {}}, env) - float(r['value'])))
                info['formula_max_dev'] = worst
            except Exception as exc:  # noqa: BLE001
                info['formula_error'] = str(exc)[:120]
        rows[d.name] = info
    return {'assets': dict(sorted(rows.items()))}


def probe_structure(root: Path, assets: list) -> dict:
    """包根子目录只能是资产目录（含 asset.json）或 asset-runs/；包根不得有 asset.json（资产只能以子目录存在）。"""
    bad = []
    for d in sorted(x for x in root.iterdir() if x.is_dir()):
        if d.name in ('asset-runs', '.git', '__pycache__') or d.name.startswith('.'):
            continue
        if not (d / 'asset.json').is_file():
            bad.append(d.name)
    return {'asset_dir_count': len(assets), 'non_asset_dirs': bad,
            'root_asset_json': (root / 'asset.json').is_file()}


def probe_envelope(root: Path) -> dict:
    """调用仓库自带 validate_envelope.py 做包络校验，拿 error 明细（口径与机检完全一致）。"""
    import subprocess
    script = Path(__file__).resolve().parent / 'validate_envelope.py'
    out = root.parent / '.probe-envelope.json'
    try:
        proc = subprocess.run([sys.executable, str(script), str(root), '--json-out', str(out)],
                              capture_output=True, text=True)
        data = json.loads(out.read_text(encoding='utf-8')) if out.is_file() else {}
        out.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        return {'error': f'包络校验调用失败：{exc}', 'errors': []}
    errs = []
    for a in (data.get('assets') or []):
        if str(a.get('asset_dir') or '') in ('.', '', root.name):     # 包根文件不校验（另由 P0-STRUCT 判）
            continue
        for e in (a.get('errors') or []):
            errs.append({'asset': a.get('asset_dir'), 'where': e.get('where'), 'message': e.get('message')})
    return {'errors': errs, 'summary': data.get('summary') or {}, 'rc': proc.returncode}


def probe_outside_refs(root: Path, assets: list, metas: dict) -> dict:
    """声明引用包外路径（../、绝对、file://）与指向包外的软链。"""
    outside, links = [], []
    for d in assets:
        base = d.resolve()
        for i, a in enumerate((metas[d.name].get('spec') or {}).get('artifacts') or []):
            uri = str((a or {}).get('uri') or '')
            if not uri or re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*://', uri) and not uri.startswith('file://'):
                continue
            if uri.startswith('file://'):
                outside.append(f'{d.name}:spec.artifacts[{i}].uri={uri}')
                continue
            target = (d / uri).resolve()
            if base not in target.parents and target != base:
                outside.append(f'{d.name}:spec.artifacts[{i}].uri={uri}')
    for d in assets:
        for p in d.rglob('*'):
            if not p.is_symlink():
                continue
            try:
                tgt = p.resolve()
            except Exception:  # noqa: BLE001
                continue
            if d.resolve() not in tgt.parents and tgt != d.resolve():
                links.append(f'{d.name}:{p.relative_to(d)}')
    return {'outside_refs': sorted(outside), 'escaping_symlinks': sorted(links)}


def probe_doc_refs(root: Path, assets: list) -> dict:
    """包内文档里出现的包内路径是否存在（只看形如 a/b.ext 的相对路径 token）。"""
    # 只认 ASCII 路径 token，避免把中文说明和路径粘连（如「119条预测与expected/x.tsv」）
    token = re.compile(r'(?<![A-Za-z0-9_./-])((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.'
                       r'(?:py|sh|json|jsonl|md|ya?ml))')   # 只看脚本/文档/契约，数据样例文件不判
    missing = {}
    for d in assets:
        bad = set()
        for f in list(d.rglob('*.md')):
            text = f.read_text(encoding='utf-8', errors='replace')
            for tok in token.findall(text):
                if tok.startswith(('http', 'validate/cases')) or '<' in tok:
                    continue
                if tok.split('/')[0] in ('out', 'output', 'outputs', 'results', 'dist', 'build', 'tmp', 'logs'):
                    continue                       # 常见产物目录，不算"引用了不存在的包内文件"
                cands = [(f.parent / tok), (d / tok), (root / tok)]
                if any(c.exists() for c in cands):
                    continue
                bad.add(tok)
        if bad:
            missing[d.name] = sorted(bad)[:10]
    return {'missing': dict(sorted(missing.items()))}


def probe_version_consistency(assets: list, metas: dict) -> dict:
    bad = {}
    for d in assets:
        ver = str((metas[d.name] or {}).get('asset_version') or '')
        if not ver:
            continue
        seen = set()
        for f in list(d.rglob('cases.json')) + list(d.rglob('acceptance.json')):
            doc = load(f) or {}
            v = str(doc.get('asset_version') or '')
            if v and v != ver:
                seen.add(f'{f.relative_to(d)}={v}')
        if seen:
            bad[d.name] = {'declared': ver, 'others': sorted(seen)}
    return {'mismatch': dict(sorted(bad.items()))}


def probe_verify_docs(assets: list) -> dict:
    """验证说明里显式声明的用例数量/编号 vs validate/cases.json。"""
    bad = {}
    for d in assets:
        cases = load(d / 'validate' / 'cases.json') or {}
        ids = [str(c.get('id') or '') for c in (cases.get('cases') or [])]
        if not ids:
            continue
        readme = d / 'validate' / 'readme.md'
        text = readme.read_text(encoding='utf-8', errors='replace') if readme.is_file() else ''
        if not text:
            continue
        issues = []
        m = re.search(r'(\d+)\s*组', text)
        if m and int(m.group(1)) != len(ids):
            issues.append(f'说明写 {m.group(1)} 组，实际 {len(ids)} 组')
        mentioned = set(re.findall(r'\b[A-Za-z0-9_-]*(?:C\d{2}|case[-_]\d+|TC[-_]?\d+)[A-Za-z0-9_-]*', text))
        ghost = sorted(x for x in mentioned if x not in ids and not any(x in i for i in ids))
        if ghost:
            issues.append('说明提到的用例在 cases.json 中不存在：' + ', '.join(ghost[:5]))
        if issues:
            bad[d.name] = issues
    return {'mismatch': dict(sorted(bad.items()))}


def probe_verify_entry(assets: list) -> dict:
    """可执行验证入口：用例清单 / 验收声明 / 可执行脚本（.sh/.py）/ tests 目录。"""
    rows = {}
    for d in assets:
        cases = [str(p.relative_to(d)) for p in d.rglob('cases.json')]
        scripts = [str(p.relative_to(d)) for p in d.rglob('*.sh') if (d / p).is_file()]
        runners = [str(p.relative_to(d)) for p in d.rglob('run*.py')]
        tests_py = [str(p.relative_to(d)) for p in d.rglob('test_*.py')]
        tests_dir = any(x.is_dir() and x.name in ('tests', 'test') for x in d.rglob('*'))
        rows[d.name] = {'cases_json': sorted(cases)[:3],
                        'acceptance_json': (d / 'acceptance.json').is_file(),
                        'shell_scripts': sorted(scripts)[:3],
                        'runner_py': sorted(runners)[:3],
                        'unit_tests': sorted(tests_py)[:3], 'tests_dir': tests_dir,
                        'validate_readme': (d / 'validate' / 'readme.md').is_file()}
    def ok(v):
        return bool(v['cases_json'] or v['acceptance_json'] or v['shell_scripts']
                    or v['runner_py'] or v['unit_tests'] or v['tests_dir'])
    return {'assets': dict(sorted(rows.items())),
            'without_entry': sorted(k for k, v in rows.items() if not ok(v))}


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
        'data_declaration_profile': probe_data_declarations(assets, metas),
        'structure_profile': probe_structure(root, assets),
        'envelope_profile': probe_envelope(root),
        'outside_refs_profile': probe_outside_refs(root, assets, metas),
        'doc_refs_profile': probe_doc_refs(root, assets),
        'version_profile': probe_version_consistency(assets, metas),
        'verify_doc_profile': probe_verify_docs(assets),
        'verify_entry_profile': probe_verify_entry(assets),
    }


RISKY_LICENSE = ('仅限内部', '仅限项目内部', '禁止再分发', '禁止修改', '不得再分发', 'internal only', 'no redistribution')
CHECKLIST = Path(__file__).resolve().parents[1] / 'assets' / 'audit-checks.json'


def evaluate(checks: dict, intent: str = 'delivery') -> dict:
    """按固定清单（P0×4 / P1×5）把事实映射成结论。严重度写死，执行者不得升降。"""
    try:
        spec = json.loads(CHECKLIST.read_text(encoding='utf-8'))
    except Exception:  # noqa: BLE001
        return {'error': '读不到 assets/audit-checks.json', 'findings': []}
    checks = dict(checks)
    metas = {k: {'asset_type': t}
             for t, n in ((checks.get('package_summary') or {}).get('asset_types') or {}).items()
             for k in [f'{t}#{i}' for i in range(n)]}
    findings = []

    def add(cid, detail):
        meta = next((c for c in spec['checks'] if c['id'] == cid), {})
        # 归属：detail 的 key 通常是资产目录名；没有 key 或 key 不像资产名就是包级
        blob = json.dumps(detail, ensure_ascii=False, default=str)
        known = sorted((checks.get('package_summary') or {}).get('asset_dirs') or [])
        owners = [a for a in known if a and a in blob]
        scope = 'asset' if owners else 'package'
        findings.append({'check_id': cid, 'level': meta.get('level'), 'name': meta.get('name'),
                         'status': 'fail', 'detail': detail, 'criterion': meta.get('criterion'),
                         'scope': scope, 'assets': owners})

    # ---------- P0 ----------
    st = checks.get('structure_profile') or {}
    struct_detail = {}
    if st.get('non_asset_dirs'):
        struct_detail['non_asset_dirs'] = st['non_asset_dirs']
        struct_detail['asset_dir_count'] = st.get('asset_dir_count')
    if st.get('root_asset_json'):
        struct_detail['root_asset_json'] = '包根存在 asset.json：资产必须作为资产包的子目录存在'
    if struct_detail:
        add('P0-STRUCT', struct_detail)

    env = checks.get('envelope_profile') or {}
    if env.get('errors'):
        by_asset = {}
        for e in env['errors']:
            by_asset.setdefault(str(e.get('asset') or 'unknown'), []).append(
                f"{e.get('where')}: {e.get('message')}")
        add('P0-ENVELOPE', by_asset)

    art = checks.get('artifact_integrity') or {}
    out_refs = checks.get('outside_refs_profile') or {}
    complete_detail = {k: art.get(k) for k in ('missing', 'size_mismatch', 'sha_mismatch') if art.get(k)}
    if out_refs.get('outside_refs'):
        complete_detail['outside_refs'] = out_refs['outside_refs']
    if out_refs.get('escaping_symlinks'):
        complete_detail['escaping_symlinks'] = out_refs['escaping_symlinks']
    if complete_detail:
        add('P0-COMPLETE', complete_detail)

    ventry = checks.get('verify_entry_profile') or {}
    if ventry.get('without_entry'):
        add('P0-VERIFY', {'without_entry': ventry['without_entry']})

    # ---------- P1 ----------
    docs = (checks.get('doc_refs_profile') or {}).get('missing') or {}
    if docs:
        add('P1-DOC-REF', docs)

    ver = (checks.get('version_profile') or {}).get('mismatch') or {}
    if ver:
        add('P1-VERSION', ver)

    # 声明与实体/实现不符：数据声明数值不一致
    decl = {}
    for name, info in ((checks.get('data_declaration_profile') or {}).get('assets') or {}).items():
        issues = {k: info.get(k) for k in ('records_mismatch', 'columns_mismatch', 'bbox_mismatch', 'grid_incomplete')
                  if info.get(k)}
        dev, tol = info.get('formula_max_dev'), info.get('formula_tolerance')
        if info.get('formula_error'):
            issues['formula_error'] = info['formula_error']
        elif dev is not None and isinstance(tol, (int, float)) and dev > tol:
            issues['formula_out_of_tolerance'] = {'max_dev': dev, 'tolerance': tol}
        if issues:
            decl[name] = issues
    if decl:
        add('P1-DECL', decl)

    vdoc = (checks.get('verify_doc_profile') or {}).get('mismatch') or {}
    if vdoc:
        add('P1-VERIFY-DOC', vdoc)

    # ---------- 覆盖表 ----------
    failed = {f['check_id'] for f in findings}
    dprof = (checks.get('data_declaration_profile') or {}).get('assets') or {}
    na_rules = {
        'P1-DECL': (any(v.get('has_metadata') for v in dprof.values()), '没有 data 资产声明 metadata，无可核对项'),
        'P1-VERSION': (bool((checks.get('version_profile') or {})), '版本一致性检查不可用'),
        'P1-VERIFY-DOC': (any(v.get('has_cases_json') for v in ((checks.get('verify_entry_profile') or {}).get('assets') or {}).values()),
                          '没有资产提供 validate/cases.json，无口径可比'),
        'P1-DOC-FACT': (False, '事实/算术类错误需人工判读，机器不自动判'),
    }
    coverage = []
    for c in spec['checks']:
        if c['id'] in failed:
            status, reason = 'fail', ''
        elif c['id'] in na_rules and not na_rules[c['id']][0]:
            status, reason = 'na', na_rules[c['id']][1]
        elif not (checks.get('package_summary') or {}).get('asset_count'):
            status, reason = 'na', '包内没有资产'
        else:
            status, reason = 'pass', ''
        coverage.append({'check_id': c['id'], 'level': c.get('level'), 'status': status, 'na_reason': reason})

    return {'intent': intent, 'findings': findings, 'coverage': coverage,
            'coverage_summary': {k: sum(1 for c in coverage if c['status'] == k) for k in ('pass', 'fail', 'na')},
            'summary': {'P0': sum(1 for f in findings if f['level'] == 'P0'),
                        'P1': sum(1 for f in findings if f['level'] == 'P1')},
            'note': 'P0/P1 只来自本清单；P2 是建议，可由执行者自由补充（含清单外观察）'}


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
        print(f"按固定清单判定（intent={v['intent']}）：P0 {v['summary']['P0']} / P1 {v['summary']['P1']}"
              '（P2 为建议级，由执行者按需补充）')
        for f in v['findings']:
            print(f"  [{f['level']}] {f['check_id']} {f['name']}")
        cs = v.get('coverage_summary') or {}
        print(f"  覆盖：pass {cs.get('pass', 0)} / fail {cs.get('fail', 0)} / na {cs.get('na', 0)}"
              '（na 表示该检查项对本包不适用，理由写进 probe-report.json 的 coverage）')
    if args.json_out:
        p = Path(args.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
