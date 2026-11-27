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
import os
from collections import Counter
from pathlib import PurePosixPath
from pathlib import Path

import asset_types

try:
    from _layout import is_mounted_external as _is_mounted
except Exception:                        # noqa: BLE001
    def _is_mounted(d):
        return (d / '.mounted-external.json').is_file()

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
    # validate_envelope.py 的输出结构是 results[]（每条含 file/asset_id/errors[{path,message}]）；
    # 早期版本用 assets[]，这里两种都认，避免"summary 说有错、errors 却为空"的假阴性。
    entries = data.get('results') or data.get('assets') or []
    errs = []
    for a in entries:
        name = a.get('asset_dir') or a.get('dir') or a.get('file') or ''
        if Path(str(name)).name in ('', root.name) and str(name) in ('.', '', root.name):
            continue                                                   # 包根文件不校验（另由 P0-STRUCT 判）
        label = Path(str(name)).parent.name if str(name).endswith('.json') else str(name) or '?'
        for e in (a.get('errors') or []):
            errs.append({'asset': label, 'where': e.get('where') or e.get('path'),
                         'message': e.get('message')})
    return {'errors': errs, 'summary': data.get('summary') or {}, 'rc': proc.returncode}


def _declared_targets(d: Path) -> set[str]:
    """资产 external_assets.json 里声明的 target_path（规范化成 POSIX 相对路径）。"""
    f = d / 'external_assets.json'
    if not f.is_file():
        return set()
    try:
        doc = json.loads(f.read_text(encoding='utf-8'))
    except Exception:  # noqa: BLE001
        return set()
    out = set()
    for a in doc.get('assets') or []:
        tp = str((a or {}).get('target_path') or '').strip()
        if tp:
            out.add(PurePosixPath(tp).as_posix().rstrip('/'))
    return out


def probe_outside_refs(root: Path, assets: list, metas: dict) -> dict:
    """声明引用包外路径（../、绝对、file://）与指向包外的软链。"""
    outside, links, absolute = [], [], []
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
            # 挂载进来的外部资产（external_assets.json 解析后由 mount_external 软链）是合规的
            if _is_mounted(tgt):
                continue
            # 该软链若正是本资产 external_assets.json 里声明的 target_path，也属合规引用
            if PurePosixPath(str(p.relative_to(d))).as_posix().rstrip('/') in _declared_targets(d):
                continue
            # 绝对路径软链：即使现在指向包内，资产包一旦搬迁就失效或指到旧位置（规范禁止绝对路径）
            try:
                raw = os.readlink(p)
            except OSError:
                raw = ''
            if raw.startswith('/'):
                absolute.append(f'{d.name}:{p.relative_to(d)} -> {raw}')
                continue
            if d.resolve() in tgt.parents or tgt == d.resolve():
                continue
            links.append(f'{d.name}:{p.relative_to(d)}')
    return {'outside_refs': sorted(outside), 'escaping_symlinks': sorted(links),
            'absolute_symlinks': sorted(absolute)}


def probe_doc_refs(root: Path, assets: list) -> dict:
    """包内文档里出现的包内路径是否存在（只看形如 a/b.ext 的相对路径 token）。"""
    # 只认 ASCII 路径 token，避免把中文说明和路径粘连（如「119条预测与expected/x.tsv」）
    # 只看脚本/文档/契约，数据样例文件不判。
    # 末尾必须是非文件名边界，否则 `.tar.sha256` 会被截成 `.tar.sh`、`.shp` 会被截成 `.sh`
    token = re.compile(r'(?<![A-Za-z0-9_./-])((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.'
                       r'(?:py|sh|json|jsonl|md|ya?ml))(?![-.A-Za-z0-9])')
    missing, case_only = {}, {}
    for d in assets:
        bad, soft = set(), set()
        for f in list(d.rglob('*.md')):
            text = f.read_text(encoding='utf-8', errors='replace')
            # markdown 链接只认目标，不认链接文字：`[a/b.md](c/d.md)` → 只看 c/d.md
            text = re.sub(r'\[([^\]]*)\]\(([^)]*)\)', r' \2 ', text)
            for tok in token.findall(text):
                if tok.startswith(('http', 'validate/cases')) or '<' in tok:
                    continue
                if tok.split('/')[0] in ('out', 'output', 'outputs', 'results', 'dist', 'build', 'tmp', 'logs',
                                         'experiments', 'runs', 'artifacts', 'cache'):
                    continue                       # 常见产物目录，不算"引用了不存在的包内文件"
                # 第 4 个候选：skill 自身仓库（文档常引用 skill 提供的脚本/清单，如 scripts/harbor_run.py）
                cands = [(f.parent / tok), (d / tok), (root / tok), (SKILL_DIR / tok)]
                if any(c.exists() for c in cands):
                    continue
                # 文档常以"包内子仓库"为基准（如 payload/source/）：若包内存在以该 token 结尾的唯一文件，
                # 视为路径存在（只是基准未写明），不判"引用了不存在的文件"
                tail = '/'.join(tok.split('/')[-2:]) if tok.count('/') >= 2 else tok
                hits = [q for q in d.rglob('*') if q.is_file() and str(q).endswith('/' + tok)]
                if not hits and tail != tok:
                    hits = [q for q in d.rglob('*') if q.is_file() and str(q).endswith('/' + tail)]
                if len(hits) == 1:
                    soft.add(tok)
                    continue
                # 仅大小写不同的同名文件：不算"引用了不存在的文件"，单列提示
                lower = {str(c).lower() for c in cands}
                if any(p_.exists() and str(p_).lower() in lower
                       for base in (f.parent, d, root) for p_ in base.rglob('*') if p_.is_file()):
                    soft.add(tok)
                    continue
                bad.add(tok)
        if bad:
            missing[d.name] = sorted(bad)[:10]
        if soft:
            case_only[d.name] = sorted(soft)[:10]      # 大小写不同 / 包内后缀唯一命中：均视为存在
    return {'missing': dict(sorted(missing.items())),
            'case_only_mismatch': dict(sorted(case_only.items()))}


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
        cases_path = d / 'validate' / 'cases.json'
        if not cases_path.is_file():
            found = sorted(d.rglob('cases.json'))
            cases_path = found[0] if found else cases_path
        cases = load(cases_path) or {}
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


SECRET_NAME = re.compile(r'(\.credentials\.ya?ml$|(^|/)\.env$|(^|/)auth\.json$|secret|token|credential)', re.I)
# 密钥样式：必须落在词边界上、同一行内、且值不像"变量名/URL"这类普通标识符。
# 典型误报：`task-01-without-knowledge` 里的 `sk-01-without…`、`LLM_API_KEY=`（空值）跨行吃到下一行变量名。
SECRET_VALUE = re.compile(
    r'(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}'                       # OpenAI 风格，前面不能是字母（排除 task-01-…）
    r'|(?<![A-Za-z0-9])Bearer[ \t]+[A-Za-z0-9._-]{20,}'             # Bearer token
    r'|[A-Z][A-Z0-9_]*_API_KEY[ \t]*[:=][ \t]*["\']?'
    r'(?![A-Z0-9_]{4,}(?![a-z0-9])[\s"\']|\s|$)["\']?([A-Za-z0-9][A-Za-z0-9._-]{15,})')  # 同行非空值


def probe_secrets(root: Path, assets: list) -> dict:
    """包内是否有凭据文件或明文密钥（交付包不得含密钥）。"""
    hits = []
    for p_ in sorted(root.rglob('*')):
        rel = p_.relative_to(root)
        parts = set(rel.parts)
        if ({'asset-runs', '.git', '.asset-work', 'node_modules', 'vendor', '.tmp', 'site-packages'}
                & parts):
            continue
        if any(x.startswith(('.dsh-home-', 'codex-home', 'claude-home')) for x in parts):
            continue
        name = p_.name
        if p_.is_dir() and name.startswith('.dsh-home-'):
            hits.append({'path': str(rel), 'why': '隔离 home 目录（可能含凭据）'})
            continue
        if not p_.is_file():
            continue
        if SECRET_NAME.search(name) and p_.suffix.lower() in ('.yaml', '.yml', '.env', '.json', '.txt', ''):
            hits.append({'path': str(rel), 'why': f'文件名疑似凭据：{name}'})
            continue
        if p_.stat().st_size > 2 * 1024 * 1024 or p_.suffix.lower() not in (
                '.json', '.yaml', '.yml', '.env', '.txt', '.md', '.py', '.sh', '.cfg', '.ini', '.toml'):
            continue
        try:
            text = p_.read_text(encoding='utf-8', errors='replace')
        except Exception:  # noqa: BLE001
            continue
        m = SECRET_VALUE.search(text)
        if m:
            hits.append({'path': str(rel), 'why': '文件内含明文密钥样式', 'kind': m.group(0)[:12] + '…'})
    return {'hits': hits[:50], 'count': len(hits)}


SAGE_HINT = ('Sage 配置未就绪：请在运行环境设置 SAGE_BASE_URL / SAGE_API_KEY 后重跑（模型名固定 Sage-A1.5）；'
             'API URL 与 API KEY 可在端砚平台（书生·Intern-Discovery）获取。'
             '未完成配置前不得以「未测」结案。')


def probe_sage(run_dir: Path) -> dict:
    """Sage 附加测试的就绪状态（需不需要测、配置是否齐、是否跑通）。"""
    def load(name):
        f = run_dir / name
        if not f.is_file():
            return {}
        try:
            return json.loads(f.read_text(encoding='utf-8'))
        except Exception:  # noqa: BLE001
            return {}

    need = load('sage-need.json')
    detected = bool(need)
    cfg = load('sage-config.json')
    rep = load('sage-a1.5-report.json')
    needed = bool(need.get('needed'))
    report_status = rep.get('status')
    runs = rep.get('runs') or []
    failed_runs = [r for r in runs if not (r.get('ok') if 'ok' in r else r.get('passed', True))]
    ran_ok = report_status == 'completed' and not failed_runs
    error_kind = ''
    blob = json.dumps(rep, ensure_ascii=False).lower() if rep else ''
    if needed and not ran_ok:
        if cfg.get('status') == 'missing_config' or cfg.get('missing'):
            error_kind = 'config_missing'
        elif cfg.get('probe_ok') is False or report_status in ('blocked_missing_config', 'blocked_unreachable', 'blocked'):
            error_kind = 'config_invalid'
        elif any(k in blob for k in ('401', '403', 'unauthorized', 'invalid api key', 'connect', 'timeout', 'refused')):
            error_kind = 'config_invalid'
        else:
            error_kind = 'not_run'
    return {'needed': needed, 'detected': detected,
            'needs_detect': (not detected and run_dir is not None),
            'config_status': cfg.get('status'), 'missing': cfg.get('missing') or [],
            'probe_ok': cfg.get('probe_ok'), 'report_status': report_status,
            'failed_runs': len(failed_runs), 'ran_ok': ran_ok, 'error_kind': error_kind,
            'hint': SAGE_HINT if (needed and not ran_ok) else ''}


def probe_harbor_run(run_dir: Path) -> dict:
    """harbor_run.py 的执行结果（oracle/nop 双 trial、lint 发现、blocked）。"""
    f = run_dir / 'harbor-run-report.json'
    if not f.is_file():
        return {'executed': False, 'report_present': False}
    try:
        doc = json.loads(f.read_text(encoding='utf-8'))
    except Exception:  # noqa: BLE001
        return {'executed': False, 'error': 'harbor-run-report.json 不可解析'}
    tasks = doc.get('tasks') or doc.get('results') or []
    failed, blocked, passed = [], [], []
    for t in tasks:
        if not isinstance(t, dict):
            continue
        verdict = str(t.get('verdict') or t.get('status') or '').lower()
        name = t.get('task') or t.get('asset') or t.get('name') or '?'
        if verdict in ('pass', 'passed', 'ok'):
            passed.append(name)
        elif verdict.startswith('blocked'):
            blocked.append({'task': name, 'why': t.get('reason') or verdict})
        else:
            failed.append({'task': name, 'oracle': t.get('oracle_reward'), 'nop': t.get('nop_reward'),
                           'why': t.get('reason') or verdict})
    lint = doc.get('lint') or {}
    return {'executed': True, 'summary': doc.get('summary') or {},
            'passed': passed, 'failed': failed, 'blocked': blocked,
            'lint_correctness': lint.get('correctness') or [],
            'lint_reproducibility': lint.get('reproducibility') or [],
            'lint_note': lint.get('note')}


RUNTIME_CHECKS = ('P0-SAGE', 'P0-HARB-RUN', 'P1-HARB-LINT')


def _asset_partial_picklable(args: tuple) -> dict:
    root_s, metas, d_s = args
    return asset_partial(Path(root_s), metas, Path(d_s))


ASSET_KEYS_COUNTER = ('artifacts_checked',)


def asset_partial(root: Path, metas: dict, d: Path) -> dict:
    """单个资产的静态事实（供按资产并行；键与 checks 一致，值为该资产的片段）。"""
    atype = str((metas.get(d.name) or {}).get('asset_type') or '')
    part = {
        'artifact_integrity': probe_artifacts(root, [d], metas),
        'cases_profile': probe_cases([d]),
        'external_refs_profile': probe_external_refs([d]),
        'outside_refs_profile': probe_outside_refs(root, [d], metas),
        'doc_refs_profile': probe_doc_refs(root, [d]),
        'version_profile': probe_version_consistency([d], metas),
        'verify_doc_profile': probe_verify_docs([d]),
        'verify_entry_profile': probe_verify_entry([d]),
        'secret_profile': probe_secrets(root, [d]),
        '_type': asset_types.probe_asset(atype, root, [d], metas),
    }
    return part


def merge_partials(parts: list[dict]) -> dict:
    """把各资产的片段合并成与顺序执行完全一致的聚合结构。"""
    out: dict = {k: {} for k in ('artifact_integrity', 'cases_profile', 'external_refs_profile',
                                 'outside_refs_profile', 'doc_refs_profile', 'version_profile',
                                 'verify_doc_profile', 'verify_entry_profile', 'secret_profile')}
    counts = {'artifacts_checked': 0, 'assets_with_cases': 0, 'secret_hits': 0}
    lists: dict = {('artifact_integrity', k): [] for k in ('missing', 'size_mismatch', 'sha_mismatch')}
    lists.update({('outside_refs_profile', k): [] for k in ('outside_refs', 'escaping_symlinks', 'absolute_symlinks')})
    lists[('verify_entry_profile', 'without_entry')] = []
    lists[('secret_profile', 'hits')] = []
    dicts: dict = {('cases_profile', 'assets'): {}, ('external_refs_profile', 'assets'): {},
                   ('doc_refs_profile', 'missing'): {}, ('doc_refs_profile', 'case_only_mismatch'): {},
                   ('version_profile', 'mismatch'): {}, ('verify_doc_profile', 'mismatch'): {},
                   ('verify_entry_profile', 'assets'): {}}
    type_merged = {'harbor_profile': {'assets': {}, 'unregistered_task_dirs': []}}
    type_lists: dict = {}
    type_dicts: dict = {}

    for part in parts:
        ai = part.get('artifact_integrity') or {}
        counts['artifacts_checked'] += int(ai.get('artifacts_checked') or 0)
        for k in ('missing', 'size_mismatch', 'sha_mismatch'):
            lists[('artifact_integrity', k)].extend(ai.get(k) or [])
        cp = part.get('cases_profile') or {}
        dicts[('cases_profile', 'assets')].update(cp.get('assets') or {})
        counts['assets_with_cases'] += int(cp.get('assets_with_cases') or 0)
        for e in part.get('external_refs_profile', {}).get('assets') or {}:
            dicts[('external_refs_profile', 'assets')][e] = part['external_refs_profile']['assets'][e]
        for k in ('missing', 'case_only_mismatch'):
            dicts[('doc_refs_profile', k)].update((part.get('doc_refs_profile') or {}).get(k) or {})
        for k in ('missing', 'version_profile', 'verify_doc_profile'):
            pass
        dicts[('version_profile', 'mismatch')].update((part.get('version_profile') or {}).get('mismatch') or {})
        dicts[('verify_doc_profile', 'mismatch')].update((part.get('verify_doc_profile') or {}).get('mismatch') or {})
        ve = part.get('verify_entry_profile') or {}
        dicts[('verify_entry_profile', 'assets')].update(ve.get('assets') or {})
        lists[('verify_entry_profile', 'without_entry')].extend(ve.get('without_entry') or [])
        for k in ('outside_refs', 'escaping_symlinks', 'absolute_symlinks'):
            lists[('outside_refs_profile', k)].extend((part.get('outside_refs_profile') or {}).get(k) or [])
        sp = part.get('secret_profile') or {}
        lists[('secret_profile', 'hits')].extend(sp.get('hits') or [])
        counts['secret_hits'] += int(sp.get('count') or 0)
        for k, v in (part.get('_type') or {}).items():
            if isinstance(v, dict) and 'assets' in v:
                type_dicts.setdefault((k, 'assets'), {}).update(v.get('assets') or {})
                for kk, vv in v.items():
                    if isinstance(vv, list):
                        type_lists.setdefault((k, kk), []).extend(vv)
                    elif kk != 'assets':
                        type_merged.setdefault(k, {})[kk] = vv
                type_merged.setdefault(k, {})
            elif isinstance(v, dict) and 'unregistered_task_dirs' in v:
                type_merged[k] = v
            elif isinstance(v, dict):
                type_merged.setdefault(k, {}).update(v)

    out['artifact_integrity'] = {'artifacts_checked': counts['artifacts_checked'],
                                 'missing': sorted(set(lists[('artifact_integrity', 'missing')])),
                                 'size_mismatch': sorted(set(lists[('artifact_integrity', 'size_mismatch')])),
                                 'sha_mismatch': sorted(set(lists[('artifact_integrity', 'sha_mismatch')]))}
    out['cases_profile'] = {'assets': dict(sorted(dicts[('cases_profile', 'assets')].items())),
                            'assets_with_cases': counts['assets_with_cases'],
                            'assets_without_cases': sorted(
                                k for k, v in dicts[('cases_profile', 'assets')].items()
                                if not (v or {}).get('case_count'))}
    out['external_refs_profile'] = {'assets': dict(sorted(dicts[('external_refs_profile', 'assets')].items()))}
    out['outside_refs_profile'] = {k: sorted(set(lists[('outside_refs_profile', k)]))
                                   for k in ('outside_refs', 'escaping_symlinks', 'absolute_symlinks')}
    out['doc_refs_profile'] = {'missing': dict(sorted(dicts[('doc_refs_profile', 'missing')].items())),
                               'case_only_mismatch': dict(sorted(dicts[('doc_refs_profile', 'case_only_mismatch')].items()))}
    out['version_profile'] = {'mismatch': dict(sorted(dicts[('version_profile', 'mismatch')].items()))}
    out['verify_doc_profile'] = {'mismatch': dict(sorted(dicts[('verify_doc_profile', 'mismatch')].items()))}
    out['verify_entry_profile'] = {'assets': dict(sorted(dicts[('verify_entry_profile', 'assets')].items())),
                                   'without_entry': sorted(set(lists[('verify_entry_profile', 'without_entry')]))}
    out['secret_profile'] = {'hits': lists[('secret_profile', 'hits')][:50], 'count': counts['secret_hits']}
    for (k, kk), v in type_dicts.items():
        type_merged.setdefault(k, {})[kk] = dict(sorted(v.items()))
    for (k, kk), v in type_lists.items():
        type_merged.setdefault(k, {})[kk] = sorted(set(v))
    out.update(type_merged)
    return out


def checks_of(root: Path, run_dir: Path | None = None, static_only: bool = False,
              jobs: int = 1) -> dict:
    assets = package_asset_dirs(root)
    metas = {}
    for d in assets:
        metas[d.name] = load(d / 'asset.json') or {}
    # 逐资产静态事实：并发轴是**资产**（不是类型）——一种类型的包也能并行
    if jobs > 1 and len(assets) > 1:
        import concurrent.futures as _cf
        with _cf.ProcessPoolExecutor(max_workers=min(jobs, len(assets))) as pool:
            parts = list(pool.map(_asset_partial_picklable,
                                  [(str(root), metas, str(d)) for d in assets]))
    else:
        parts = [asset_partial(root, metas, d) for d in assets]
    per_asset = merge_partials(parts)
    # harbor 任务包结构是包级事实（要能发现"不在任何 asset.json 下"的 task 目录）
    per_asset['harbor_profile'] = asset_types.probe_asset('task', root, assets, metas).get(
        'harbor_profile', {'assets': {}, 'unregistered_task_dirs': []})
    return {
        'package_summary': probe_package(root, assets),
        'governance_profile': probe_governance(metas),
        'lifecycle_profile': probe_lifecycle(metas),
        **per_asset,
        'structure_profile': probe_structure(root, assets),
        'envelope_profile': probe_envelope(root),
        'harbor_run_profile': ({} if static_only else (probe_harbor_run(run_dir) if run_dir else {})),
        'sage_profile': ({} if static_only else (probe_sage(run_dir) if run_dir else {})),
        'static_only': static_only,
    }


try:                      # harbor 任务包（terminal-bench-science 格式）探测模块
    from _harbor import probe_harbor, static_issues
except Exception:  # noqa: BLE001
    probe_harbor = None
    static_issues = None

RISKY_LICENSE = ('仅限内部', '仅限项目内部', '禁止再分发', '禁止修改', '不得再分发', 'internal only', 'no redistribution')
SKILL_DIR = Path(__file__).resolve().parents[1]   # skill 仓库根（文档引用 skill 自带脚本时按它解析）
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
    notes = []          # P2 级建议：由清单外观察产生，不参与 P0/P1 判定

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
    if out_refs.get('absolute_symlinks'):
        complete_detail['absolute_symlinks'] = out_refs['absolute_symlinks']
    if complete_detail:
        add('P0-COMPLETE', complete_detail)

    harbor = checks.get('harbor_profile') or {}
    harbor_assets = harbor.get('assets') or {}
    harbor_run = checks.get('harbor_run_profile') or {}
    harbor_present = bool(harbor_assets or harbor.get('unregistered_task_dirs'))
    if static_issues and harbor_present:
        iss = static_issues(harbor)
        if iss.get('missing_files'):
            add('P0-HARB-STRUCT', {'missing_files': iss['missing_files']})
        dataset_mismatch = iss.get('dataset_mismatch') or {}
        if dataset_mismatch:
            add('P1-HARB-DATASET', {'dataset_mismatch': dataset_mismatch})
        envelope = {}
        if iss.get('unregistered_task_dirs'):
            envelope['unregistered_task_dirs'] = iss['unregistered_task_dirs']
        wrong_type = {n: str(e.get('declared_asset_type')) for n, e in harbor_assets.items()
                      if e.get('declared_asset_type') != 'task'}
        if wrong_type:
            envelope['declared_asset_type_not_task'] = wrong_type
        if envelope:
            add('P1-HARB-ENVELOPE', envelope)
        # P2 级建议：不进清单，作为建议输出
        if iss.get('implicit_no_manifest'):
            notes.append({'level': 'P2', 'topic': '隐式数据集缺 dataset.toml',
                          'detail': iss['implicit_no_manifest']})
        hints = {}
        for n, e in harbor_assets.items():
            tasks = ([e['task']] if e.get('task') else []) \
                + list(((e.get('dataset') or {}).get('local_tasks') or {}).values()) \
                + list((e.get('tasks') or {}).values())
            got = sorted({h for t2 in tasks for h in (t2.get('verifier_llm_hints') or [])})
            if got:
                hints[n] = got
        if hints:
            notes.append({'level': 'P2', 'topic': 'verifier 依赖外部模型打分（reward 可能非确定）',
                          'detail': hints})
    if harbor_run.get('executed'):
        if harbor_run.get('failed'):
            add('P0-HARB-RUN', {'failed': harbor_run['failed'],
                                'blocked': harbor_run.get('blocked') or []})
        if harbor_run.get('lint_correctness'):
            add('P1-HARB-LINT', {'findings': harbor_run['lint_correctness']})
        if harbor_run.get('lint_reproducibility'):
            notes.append({'level': 'P2', 'topic': 'harbor lint 可复现性建议',
                          'detail': harbor_run['lint_reproducibility']})

    sage = checks.get('sage_profile') or {}
    if sage.get('needs_detect'):
        notes.append({'level': 'P2', 'topic': 'Sage 需求未判定',
                      'detail': 'sage-need.json 缺失：本次 --run-dir 指向的批次还没跑 sage_probe.py detect，'
                                'P0-SAGE 只能记 na。跑完第二步后请带 --run-dir 重跑 probe_package.py。'})
    if sage.get('needed') and not sage.get('ran_ok'):
        add('P0-SAGE', {'error_kind': sage.get('error_kind'), 'config_status': sage.get('config_status'),
                        'missing': sage.get('missing'), 'report_status': sage.get('report_status'),
                        'hint': sage.get('hint')})

    sec = checks.get('secret_profile') or {}
    if sec.get('hits'):
        add('P0-SECRET', {'count': sec.get('count'), 'hits': sec['hits'][:20]})

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
    if not harbor_present:
        harb_run = (False, '包内没有 harbor 任务/数据集资产')
    elif harbor_run.get('executed'):
        harb_run = (True, '')
    else:
        if harbor_run.get('report_present'):
            harb_run = (False, 'oracle/nop 由 harbor_run.py 执行判定：本次未执行（需 docker + harbor CLI）')
        else:
            harb_run = (False, '本次还没跑 harbor_run.py（第二步产物缺失）——'
                              '跑完第二步后必须带 --run-dir 重跑 probe_package.py 再判定')
    na_rules = {
        'P0-HARB-RUN': harb_run,
        'P0-HARB-STRUCT': (harbor_present, '包内没有 harbor 任务/数据集资产'),
        'P1-HARB-DATASET': (any(e.get('form') == 'dataset_explicit' for e in harbor_assets.values()),
                            '包内没有显式 dataset.toml 数据集'),
        'P1-HARB-ENVELOPE': (harbor_present, '包内没有 harbor 任务/数据集资产'),
        'P1-HARB-LINT': (bool(harbor_run.get('executed')), 'harbor lint 未采集（需 harbor CLI，由 harbor_run.py 执行）'),
        'P0-SAGE': (bool((checks.get('sage_profile') or {}).get('needed')),
                    ('本包是否需要 Sage 尚未判定：先跑 sage_probe.py detect 写 sage-need.json，'
                     '再带 --run-dir 重跑 probe_package.py'
                     if (checks.get('sage_profile') or {}).get('needs_detect')
                     else '本包不需要 Sage 附加测试（无 skill/agent 类资产或未判定为需要）')),
        'P1-DECL': (any(v.get('has_metadata') for v in dprof.values()), '没有 data 资产声明 metadata，无可核对项'),
        'P1-VERSION': (bool((checks.get('version_profile') or {})), '版本一致性检查不可用'),
        'P1-VERIFY-DOC': (any(v.get('cases_json') for v in ((checks.get('verify_entry_profile') or {}).get('assets') or {}).values()),
                          '没有资产提供 cases.json，无口径可比'),
        'P1-DOC-FACT': (False, '事实/算术类错误需人工判读，机器不自动判'),
    }
    static_only = bool(checks.get('static_only'))
    coverage = []
    for c in spec['checks']:
        if c['id'] in failed:
            status, reason = 'fail', ''
        elif static_only and c['id'] in RUNTIME_CHECKS:
            status, reason = 'deferred', '运行期检查：第二步跑完后带 --run-dir 重跑判定'
        elif c['id'] in na_rules and not na_rules[c['id']][0]:
            status, reason = 'na', na_rules[c['id']][1]
        elif not (checks.get('package_summary') or {}).get('asset_count'):
            status, reason = 'na', '包内没有资产'
        else:
            status, reason = 'pass', ''
        coverage.append({'check_id': c['id'], 'level': c.get('level'), 'status': status, 'na_reason': reason})

    return {'intent': intent, 'findings': findings, 'notes': notes, 'coverage': coverage,
            'coverage_summary': {k: sum(1 for c in coverage if c['status'] == k)
                                 for k in ('pass', 'fail', 'na', 'deferred')},
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
    ap.add_argument('--run-dir', help='本次核验的 run 目录：从中读 sage-need/sage-config/sage 报告，判 P0-SAGE')
    ap.add_argument('--jobs', type=int, default=0,
                    help='逐资产并发的进程数（0=自动：资产数<16 用 1，否则 min(8,CPU)）')
    ap.add_argument('--static-only', action='store_true',
                    help='只做静态审查（跳过依赖 run 产物的 P0-SAGE/P0-HARB-RUN/P1-HARB-LINT，覆盖表记 deferred）')
    ap.add_argument('--intent', choices=['delivery', 'review'], default='delivery',
                    help='交付用途：delivery=对外交付（默认）/ review=内部评审')
    args = ap.parse_args(argv)

    root = Path(args.package).expanduser().resolve()
    if not root.is_dir():
        print(f'包不存在：{root}', file=sys.stderr)
        return 2
    run_dir = Path(args.run_dir).expanduser().resolve() if args.run_dir else None
    if run_dir is not None and not run_dir.is_dir():
        print(f'run 目录不存在：{run_dir}', file=sys.stderr)
        return 2
    jobs = args.jobs if args.jobs > 0 else (1 if len(package_asset_dirs(root)) < 16
                                            else min(8, os.cpu_count() or 4))
    checks = checks_of(root, run_dir, static_only=args.static_only, jobs=jobs)
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
        extra = f" / deferred {cs.get('deferred', 0)}（运行期检查，第二步后重跑）" if cs.get('deferred') else ''
        print(f"  覆盖：pass {cs.get('pass', 0)} / fail {cs.get('fail', 0)} / na {cs.get('na', 0)}{extra}"
              '（na 表示该检查项对本包不适用，理由写进 probe-report.json 的 coverage）')
    if args.json_out:
        p = Path(args.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
