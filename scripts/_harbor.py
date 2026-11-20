#!/usr/bin/env python3
"""Harbor 任务包（harbor-framework/harbor 格式）的识别、解析与确定性事实抽取。

本模块只产出**事实**（不判严重度），供 `probe_package.py` 的固定检查清单判定，
以及 `harbor_run.py` 的 oracle/nop 执行使用。同一份包多次运行结果一致。

支持的形态（按 SKILL.md §2.6）：
  task             单任务包：资产目录本身就是任务目录（含 task.toml）
  dataset_explicit 显式数据集：含 dataset.toml（声明任务名 + sha256 摘要）+ 任务目录
  dataset_implicit 隐式数据集：无 dataset.toml，子目录里有多个任务目录

格式依据 harbor 0.26（task.toml schema_version=1.4 / dataset.toml schema_version=1.0）：
  任务目录：instruction.md + task.toml + environment/(或 [environment].docker_image)
            + solution/solve.sh(.bat) + tests/test.sh(.bat)
  dataset.toml：[dataset] name="org/name" + [[tasks]] name/digest("sha256:<64hex>")
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

try:
    import tomllib
except Exception:  # noqa: BLE001 — Python < 3.11
    tomllib = None

SKIP_DIRS = {'asset-runs', '.git', '__pycache__', '.asset-work', 'node_modules'}
DEFAULT_EXCLUDE_FILES = {'.DS_Store', '.harborignore'}
EXCLUDE_SUFFIXES = ('.pyc', '.swp', '.swo')

ENV_REF = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-[^}]*)?\}')
LLM_HINTS = ('ANTHROPIC_API_KEY', 'OPENAI_API_KEY', 'chat/completions',
             'OPENAI_BASE_URL', 'llm', 'LLM')
WINDOWS = 'windows'


def parse_toml(path: Path):
    """解析 TOML；返回 (doc, error)。tomllib 缺失时按不支持处理（错误信息给修复命令）。"""
    if tomllib is None:
        return None, '本解释器缺少 tomllib（需 Python ≥ 3.11）'
    try:
        with path.open('rb') as f:
            return tomllib.load(f), None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)[:200]


def iter_task_dirs(root: Path):
    """递归找出所有含 task.toml 的目录（跳过 asset-runs/缓存/隐藏目录），返回相对路径列表。"""
    out = []
    for f in sorted(root.rglob('task.toml')):
        rel = f.parent.relative_to(root)
        if SKIP_DIRS & set(rel.parts) or any(p.startswith('.') for p in rel.parts):
            continue
        out.append(f.parent)
    return out


def find_harbor_assets(root: Path, asset_dirs: list) -> dict:
    """把 task.toml 位置归入所属资产目录。

    返回 {'assets': {资产目录名: 形态信息}, 'unregistered': [task 目录相对路径]}
    （unregistered = 不在任何 asset.json 资产目录内的任务目录，即"包内有 harbor 任务但没有登记条目"）
    """
    root = root.resolve()
    asset_by_path = {d.resolve(): d for d in asset_dirs}
    assets: dict[str, dict] = {}
    unregistered = []
    for td in iter_task_dirs(root):
        tdr = td.resolve()
        owner = None
        for apath, adir in asset_by_path.items():
            if tdr == apath or apath in tdr.parents:
                if owner is None or len(apath.parts) > len(owner.parts):
                    owner = adir
        if owner is None:
            unregistered.append(str(tdr.relative_to(root)))
            continue
        info = assets.setdefault(owner.name, {'dir': owner.resolve(), 'task_dirs': []})
        info['task_dirs'].append(tdr)
    return {'assets': assets, 'unregistered': sorted(set(unregistered))}


def _script_present(task_dir: Path, base: str, *, windows: bool) -> tuple[bool, str | None]:
    """按 harbor 的发现顺序找脚本：声明 windows 时 .bat 优先，否则 .sh 优先（sh > bat）。"""
    order = (f'{base}.bat', f'{base}.sh') if windows else (f'{base}.sh', f'{base}.bat')
    for rel in (f'solution/{p}' for p in order):
        if (task_dir / rel).is_file():
            return True, rel
    return False, None


def _tests_script_present(task_dir: Path, *, windows: bool) -> tuple[bool, str | None]:
    order = ('test.bat', 'test.sh') if windows else ('test.sh', 'test.bat')
    for rel in (f'tests/{p}' for p in order):
        if (task_dir / rel).is_file():
            return True, rel
    return False, None


def _env_var_refs(doc: dict) -> list:
    """从 [environment]/[solution]/[verifier] 的 env 值里抽 ${VAR} 引用（执行前核对宿主是否齐备）。"""
    refs = set()
    for section in ('environment', 'solution', 'verifier', 'agent'):
        env = ((doc.get(section) or {}).get('env')) or {}
        if isinstance(env, dict):
            for v in env.values():
                refs.update(ENV_REF.findall(str(v)))
    return sorted(refs)


def _llm_hints(task_dir: Path) -> list:
    """确定性扫描 tests/ 里的 LLM 判分证据（只提事实，不作判定）。"""
    hits = set()
    for f in sorted((task_dir / 'tests').rglob('*')) if (task_dir / 'tests').is_dir() else []:
        if not f.is_file() or f.suffix not in ('.sh', '.py', '.bat', '.toml', '.json', '.txt', '.yaml', '.yml'):
            continue
        try:
            text = f.read_text(encoding='utf-8', errors='replace')[:200_000]
        except OSError:
            continue
        hits.update(k for k in LLM_HINTS if k in text)
    return sorted(hits)


def probe_task(task_dir: Path) -> dict:
    """抽取单个任务目录的确定性事实。"""
    doc, toml_err = parse_toml(task_dir / 'task.toml')
    doc = doc or {}
    env_cfg = doc.get('environment') or {}
    os_name = str(env_cfg.get('os') or 'linux')
    windows = os_name == WINDOWS
    has_sol, sol_script = _script_present(task_dir, 'solve', windows=windows)
    has_test, test_script = _tests_script_present(task_dir, windows=windows)
    instr = task_dir / 'instruction.md'
    instr_empty = None
    if instr.is_file():
        instr_empty = not instr.read_text(encoding='utf-8', errors='replace').strip()
    env_dir = task_dir / 'environment'
    has_env_dir = env_dir.is_dir() and any(env_dir.iterdir())
    docker_image = env_cfg.get('docker_image')
    task_sec = doc.get('task') or {}
    return {
        'toml_ok': toml_err is None,
        'toml_error': toml_err,
        'schema_version': str(doc.get('schema_version') or ''),
        'task_name': str(task_sec.get('name') or '') or None,
        'has_instruction': instr.is_file(),
        'instruction_empty': instr_empty,
        'has_environment_dir': has_env_dir,
        'declared_docker_image': str(docker_image) if docker_image else None,
        'has_solution': has_sol,
        'solution_script': sol_script,
        'has_tests': has_test,
        'test_script': test_script,
        'declared_os': os_name,
        'declared_gpus': env_cfg.get('gpus') if isinstance(env_cfg.get('gpus'), int) else None,
        'declared_network_mode': str(env_cfg.get('network_mode') or ''),
        'env_var_refs': _env_var_refs(doc),
        'multi_step': bool(doc.get('steps')),
        'verifier_llm_hints': _llm_hints(task_dir),
    }


def compute_task_digest(task_dir: Path) -> str:
    """按 harbor Packager.compute_content_hash 的口径近似复算任务目录内容摘要。

    注意：harbor 的文件收集还处理 .harborignore 规则，本实现只做默认排除
    （__pycache__、*.pyc、.DS_Store、*.swp/.swo、*~、.harborignore）；任务用了
    .harborignore 时结果可能不同，digest_mismatch 只作参考事实（P2），不判 P0/P1。
    """
    outer = hashlib.sha256()
    files = []
    for f in sorted(task_dir.rglob('*')):
        if not f.is_file():
            continue
        rel = f.relative_to(task_dir)
        if SKIP_DIRS & set(rel.parts) or any(p.startswith('.') for p in rel.parts[:-1]):
            continue
        if f.name in DEFAULT_EXCLUDE_FILES or f.name.endswith(EXCLUDE_SUFFIXES) or f.name.endswith('~'):
            continue
        files.append(f)
    files.sort(key=lambda p: p.relative_to(task_dir).as_posix())
    for f in files:
        h = hashlib.sha256(f.read_bytes()).hexdigest()
        outer.update(f"{f.relative_to(task_dir).as_posix()}\0{h}\n".encode())
    return f'sha256:{outer.hexdigest()}'


def probe_dataset_form(asset_dir: Path, task_dirs: list) -> dict:
    """显式数据集：dataset.toml 声明 ↔ 本地任务目录双向核对。"""
    doc, toml_err = parse_toml(asset_dir / 'dataset.toml')
    doc = doc or {}
    declared = []
    bad_digest = []
    for t in doc.get('tasks') or []:
        if not isinstance(t, dict):
            continue
        name = str(t.get('name') or '')
        digest = str(t.get('digest') or '')
        if name:
            declared.append(name)
        if digest and not re.match(r'^sha256:[a-f0-9]{64}$', digest):
            bad_digest.append(name or '(未命名)')
    dataset_sec = doc.get('dataset') or {}
    local = {}
    for td in task_dirs:
        facts = probe_task(td)
        short = (facts.get('task_name') or '').split('/')[-1] or td.name
        local[short] = {'facts': facts, 'digest': compute_task_digest(td)}
    declared_short = [n.split('/')[-1] for n in declared]
    missing = [n for n in declared_short if n not in local]
    extra = [k for k in local if k not in declared_short]
    digest_mismatch = []
    for t in doc.get('tasks') or []:
        if not isinstance(t, dict):
            continue
        short = str(t.get('name') or '').split('/')[-1]
        declared_digest = str(t.get('digest') or '')
        info = local.get(short)
        if info and declared_digest and info['digest'] != declared_digest:
            digest_mismatch.append(short)
    return {
        'toml_ok': toml_err is None,
        'toml_error': toml_err,
        'schema_version': str(doc.get('schema_version') or ''),
        'dataset_name': str(dataset_sec.get('name') or '') or None,
        'declared_tasks': declared,
        'digest_bad_format': bad_digest,
        'missing_task_dirs': sorted(set(missing)),
        'undeclared_task_dirs': sorted(set(extra)),
        'digest_mismatch': sorted(set(digest_mismatch)),
        'local_tasks': {k: {kk: vv for kk, vv in v['facts'].items()} for k, v in local.items()},
    }


def probe_harbor(root: Path, asset_dirs: list) -> dict:
    """入口：返回 harbor_profile（纯事实，进 probe-report 的 checks）。"""
    root = root.resolve()
    found = find_harbor_assets(root, asset_dirs)
    assets = {}
    for name, info in sorted(found['assets'].items()):
        d = info['dir']
        task_dirs = sorted(info['task_dirs'], key=lambda p: str(p.relative_to(root)))
        has_manifest = (d / 'dataset.toml').is_file()
        if has_manifest:
            form = 'dataset_explicit'
            entry = {'form': form, 'dataset': probe_dataset_form(d, task_dirs)}
        elif len(task_dirs) == 1 and task_dirs[0] == d:
            form = 'task'
            entry = {'form': form, 'task': probe_task(d)}
        else:
            form = 'dataset_implicit'
            entry = {'form': form,
                     'task_count': len(task_dirs),
                     'tasks': {str(t.relative_to(d)): probe_task(t) for t in task_dirs}}
        assets[name] = entry
    return {'asset_count': len(assets),
            'assets': assets,
            'unregistered_task_dirs': found['unregistered']}


def static_issues(profile: dict) -> dict:
    """把事实折成检查清单要看的四类问题键（不含动态执行结果）：
    missing_files / envelope_mismatch / dataset_mismatch / implicit_no_manifest
    """
    missing_files, envelope_mismatch, dataset_mismatch, implicit = {}, {}, {}, []
    for name, e in (profile.get('assets') or {}).items():
        tasks = {}
        if e['form'] == 'task':
            tasks = {'.': e['task']}
        elif e['form'] == 'dataset_explicit':
            tasks = e['dataset']['local_tasks']
        else:
            tasks = e.get('tasks') or {}
        bad = {}
        for rel, f in tasks.items():
            probs = []
            if not f.get('toml_ok'):
                probs.append(f'task.toml 不可解析（{rel}）：{f.get("toml_error")}')
            if not f.get('has_instruction'):
                probs.append(f'缺少 instruction.md（{rel}）')
            elif f.get('instruction_empty'):
                probs.append(f'instruction.md 为空（{rel}）')
            if not f.get('has_environment_dir') and not f.get('declared_docker_image'):
                probs.append(f'environment/ 与 [environment].docker_image 双缺（{rel}）')
            if not f.get('has_solution'):
                probs.append(f'缺少 solution/solve.sh（或 solve.bat，{rel}）——oracle 验证无法执行')
            if not f.get('has_tests'):
                probs.append(f'缺少 tests/test.sh（或 test.bat，{rel}）')
            if probs:
                bad[rel] = probs
        if bad:
            missing_files[name] = bad
        ds = e.get('dataset') or {}
        if e['form'] == 'dataset_explicit':
            probs = {}
            if not ds.get('toml_ok'):
                probs['dataset.toml'] = [f'dataset.toml 不可解析：{ds.get("toml_error")}']
            if ds.get('missing_task_dirs'):
                probs['missing_task_dirs'] = ds['missing_task_dirs']
            if ds.get('undeclared_task_dirs'):
                probs['undeclared_task_dirs'] = ds['undeclared_task_dirs']
            if ds.get('digest_bad_format'):
                probs['digest_bad_format'] = ds['digest_bad_format']
            if probs:
                dataset_mismatch[name] = probs
        if e['form'] == 'dataset_implicit':
            implicit.append(name)
    return {'missing_files': missing_files, 'envelope_mismatch': envelope_mismatch,
            'dataset_mismatch': dataset_mismatch, 'implicit_no_manifest': implicit,
            'unregistered_task_dirs': profile.get('unregistered_task_dirs') or []}
