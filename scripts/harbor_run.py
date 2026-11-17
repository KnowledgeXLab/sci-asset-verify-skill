#!/usr/bin/env python3
"""Harbor 任务包全量 oracle/nop 验证（阶段四的 Harbor 任务验证）。

对包内每个 harbor 任务跑两条 trial（官方 CLI，伪 agent）：
  oracle  注入 solution 后跑 verifier —— 期望 reward = 1
  nop     不注入 solution 直接跑 verifier —— 期望 reward = 0
两条都符合才 pass；任一不符判 P0（CHK-HARB-02）；跑不了记 blocked（核验环境问题，
不判资产有罪），写明原因。**全量执行，不允许抽样。**

用法：
  python scripts/harbor_run.py --package <副本根> --run-dir <run目录> [--asset <目录名>] \
      [--task-timeout 1800] [--json-out <run>/harbor-run-report.json]

前置与降级：
  1. docker 不可用 → 全部 blocked_docker（exit 3），不烧 trial
  2. harbor CLI 不在 → 自动 `pip install harbor` 装进独立 venv（默认 $TMPDIR 下复用；
     ACI_ASSET_VERIFY_AUTO_INSTALL=0 关闭自动安装）→ 装不上 blocked_missing_harbor
  3. 单任务声明 windows / GPUs 而核验环境不具备 → 该任务 blocked_unsupported_platform /
     blocked_no_gpu；task.toml 引用的 ${VAR} 宿主缺失 → blocked_missing_env

结果文件：<run>/harbor/<任务>/oracle|nop/（harbor 原始 job 输出，供 prune 清理）
        <run>/harbor-run-report.json（每任务 reward、判定、blocked 原因、lint 发现）
退出码：0 全部通过（或包内无 harbor 任务）；1 有 P0/P1；3 存在 blocked；2 用法/读取错误。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from _harbor import iter_task_dirs, probe_task
except Exception:  # noqa: BLE001
    iter_task_dirs = probe_task = None

try:
    from _version import stamp as _stamp
except Exception:  # noqa: BLE001
    def _stamp(payload):
        return payload

LINT_CORRECTNESS = {'invalid-task-config', 'invalid-task-files', 'shared-verifier',
                    'agent-image-test-leak', 'overlapping-artifacts', 'artifact-log-overlap',
                    'compose-privileged', 'compose-additional-capabilities',
                    'compose-writable-bind-mount', 'compose-external-bind-mount',
                    'compose-symlink-bind-mount', 'unused-harborignore'}
LINT_REPRO = {'unpinned-image', 'unpinned-python-dependency', 'verifier-network-download',
              'verifier-runtime-install'}


# --------------------------------------------------------------------------- reward 解析
def _scalar(v):
    """reward 归一：数值直接用；labeled dict 全部同值时取该值，否则 None。"""
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict) and v:
        vals = {_scalar(x) for x in v.values()}
        return vals.pop() if len(vals) == 1 and None not in vals else None
    return None


def parse_job_reward(job_dir: Path) -> dict:
    """从 harbor job 输出解析 reward。按可靠性顺序：
    1. 逐 trial 的 result.json → verifier_result.rewards（harbor 0.23+ 实测格式）
    2. results.jsonl → verified_reward/reward/score（更新版本的行式汇总，前向兼容）
    3. trial 目录的 verifier/reward.txt|reward.json（verifier 原始落盘）
    4. job 级 result.json 的唯一 eval mean（兜底）
    找不到记 reward=None。"""
    if not job_dir.is_dir():
        return {'reward': None, 'source': None, 'note': f'job 目录不存在：{job_dir}'}
    for f in sorted(job_dir.rglob('result.json')):
        try:
            rec = json.loads(f.read_text(encoding='utf-8', errors='replace'))
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(rec, dict):
            continue
        if 'verifier_result' in rec and isinstance(rec['verifier_result'], dict):
            rewards = rec['verifier_result'].get('rewards') or {}
            vals = [_scalar(v) for v in rewards.values()] if isinstance(rewards, dict) else []
            vals = [v for v in vals if v is not None]
            if vals:
                return {'reward': min(vals), 'source': str(f)}
    for f in sorted(job_dir.rglob('results.jsonl')):
        try:
            for line in f.read_text(encoding='utf-8', errors='replace').splitlines():
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                if not isinstance(rec, dict):
                    continue
                for key in ('verified_reward', 'reward', 'score'):
                    if key in rec:
                        s = _scalar(rec[key])
                        if s is not None:
                            return {'reward': s, 'source': str(f)}
        except Exception:  # noqa: BLE001
            continue
    for f in sorted(job_dir.rglob('reward.json')):
        try:
            s = _scalar(json.loads(f.read_text(encoding='utf-8', errors='replace')))
            if s is not None:
                return {'reward': s, 'source': str(f)}
        except Exception:  # noqa: BLE001
            continue
    for f in sorted(job_dir.rglob('reward.txt')):
        try:
            s = _scalar(float(f.read_text(encoding='utf-8', errors='replace').strip()))
            if s is not None:
                return {'reward': s, 'source': str(f)}
        except Exception:  # noqa: BLE001
            continue
    try:
        job = json.loads((job_dir / 'result.json').read_text(encoding='utf-8', errors='replace'))
        evals = ((job.get('stats') or {}).get('evals')) or {}
        if len(evals) == 1:
            metrics = next(iter(evals.values())).get('metrics') or []
            means = [m.get('mean') for m in metrics if isinstance(m, dict) and m.get('mean') is not None]
            if len(means) == 1:
                return {'reward': float(means[0]), 'source': str(job_dir / 'result.json'),
                        'note': 'job 级聚合 mean（未找到 trial 级 reward）'}
    except Exception:  # noqa: BLE001
        pass
    return {'reward': None, 'source': None, 'note': '未找到 trial/result reward 或 verifier reward 文件'}


# --------------------------------------------------------------------------- 环境准备
def docker_ok() -> tuple[bool, str]:
    exe = shutil.which('docker')
    if not exe:
        return False, '未找到 docker 可执行文件'
    try:
        proc = subprocess.run([exe, 'info'], capture_output=True, text=True, timeout=60,
                              stdin=subprocess.DEVNULL)
    except Exception as exc:  # noqa: BLE001
        return False, f'docker info 失败：{exc}'
    if proc.returncode != 0:
        return False, f'docker info 退出码 {proc.returncode}：{(proc.stderr or proc.stdout).strip()[:200]}'
    return True, proc.stdout.strip()[:200]


def gpu_ok() -> bool:
    if shutil.which('nvidia-smi'):
        return True
    try:
        proc = subprocess.run(['docker', 'info', '--format', '{{json .Runtimes}}'],
                              capture_output=True, text=True, timeout=60)
        return 'nvidia' in (proc.stdout or '').lower()
    except Exception:  # noqa: BLE001
        return False


def ensure_harbor(venv_dir: Path, verbose: bool = True) -> dict:
    """定位或自动安装 harbor CLI。返回 {'bin': 路径, 'version': str|None, 'source': str}；
    装不到返回 {'bin': None, 'reason': str}。"""
    exe = shutil.which('harbor')
    if exe:
        ver = subprocess.run([exe, '--version'], capture_output=True, text=True, timeout=60).stdout.strip()
        return {'bin': exe, 'version': ver or None, 'source': 'environment'}
    if os.environ.get('ACI_ASSET_VERIFY_AUTO_INSTALL', '1') == '0':
        return {'bin': None, 'reason': 'harbor CLI 未安装，且 ACI_ASSET_VERIFY_AUTO_INSTALL=0 禁止自动安装'}
    py = venv_dir / 'bin' / 'python'
    if not py.is_file():
        try:
            venv_dir.mkdir(parents=True, exist_ok=True)
            proc = subprocess.run([sys.executable, '-m', 'venv', str(venv_dir)],
                                  capture_output=True, text=True, timeout=120)
            if proc.returncode != 0:
                return {'bin': None, 'reason': f'创建 venv 失败：{proc.stderr.strip()[:200]}'}
        except Exception as exc:  # noqa: BLE001
            return {'bin': None, 'reason': f'创建 venv 失败：{exc}'}
    try:
        proc = subprocess.run([str(py), '-m', 'pip', 'install', '--quiet', 'harbor'],
                              capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
    except Exception as exc:  # noqa: BLE001
        return {'bin': None, 'reason': f'pip install harbor 失败：{exc}'}
    if proc.returncode != 0:
        return {'bin': None, 'reason': (f'pip install harbor 退出码 {proc.returncode}：'
                                        f'{(proc.stderr or proc.stdout).strip()[:300]}')}
    bin_ = venv_dir / 'bin' / 'harbor'
    ver = subprocess.run([str(bin_), '--version'], capture_output=True, text=True,
                         timeout=60).stdout.strip()
    if verbose:
        print(f'[harbor] 已自动安装 harbor CLI（{ver or "版本未知"}）→ {venv_dir}', file=sys.stderr)
    return {'bin': str(bin_), 'version': ver or None, 'source': 'auto-install'}


# --------------------------------------------------------------------------- lint
def _lint_available(harbor_bin: str) -> bool:
    try:
        proc = subprocess.run([harbor_bin, 'lint', '--help'], capture_output=True, text=True,
                              timeout=30, stdin=subprocess.DEVNULL)
        return proc.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def run_lint(harbor_bin: str, task_dir: Path, timeout: int) -> dict:
    """`harbor lint --json <task>`；解析失败时如实记 lint_unparsed，不编造发现。"""
    try:
        proc = subprocess.run([harbor_bin, 'lint', '--json', str(task_dir)],
                              capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'error': str(exc)[:200]}
    raw = (proc.stdout or '').strip()
    doc = None
    for start in (raw.find('{'), raw.find('[')):
        if start >= 0:
            try:
                doc = json.loads(raw[start:])
                break
            except Exception:  # noqa: BLE001
                continue
    if doc is None:
        if proc.returncode == 0 and not raw:
            return {'ok': True, 'rules': [], 'correctness': [], 'reproducibility': []}
        return {'ok': False, 'error': f'lint 输出无法解析（rc={proc.returncode}）：{raw[:200]}'}
    items = doc if isinstance(doc, list) else (doc.get('diagnostics') or doc.get('violations')
                                               or doc.get('issues') or [])
    correctness, reproducibility, other = [], [], []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        rule = str(it.get('rule') or it.get('rule_name') or it.get('check') or '')
        msg = str(it.get('message') or it.get('msg') or '')[:200]
        entry = {'rule': rule, 'message': msg}
        if rule in LINT_CORRECTNESS:
            correctness.append(entry)
        elif rule in LINT_REPRO:
            reproducibility.append(entry)
        else:
            other.append(entry)
    return {'ok': True, 'rules': [e['rule'] for e in correctness + reproducibility + other],
            'correctness': correctness, 'reproducibility': reproducibility, 'other': other}


# --------------------------------------------------------------------------- 主流程
def run_trial(harbor_bin: str, task_dir: Path, agent: str, out_dir: Path, timeout: int) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [harbor_bin, 'run', '-p', str(task_dir), '-a', agent, '-o', str(out_dir),
           '--job-name', agent, '--yes']
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return {'status': 'blocked_timeout', 'cmd': cmd, 'timeout_sec': timeout}
    except Exception as exc:  # noqa: BLE001
        return {'status': 'blocked_error', 'cmd': cmd, 'error': str(exc)[:200]}
    if proc.returncode != 0:
        err = ((proc.stderr or '') + (proc.stdout or '')).strip()
        if 'Either datasets or tasks must be provided' in err:
            return {'status': 'blocked_not_recognized_as_task', 'cmd': cmd,
                    'error': 'harbor 未把该目录识别为任务目录（本 harbor 版本要求 environment/ '
                             '目录实际存在；仅 [environment].docker_image 在 0.24+ 才被接受）。'
                             '请在任务目录下补 environment/，或升级 harbor CLI。'}
        return {'status': 'blocked_error', 'cmd': cmd, 'exit_code': proc.returncode,
                'error': err[:400]}
    reward = parse_job_reward(out_dir / agent)
    return {'status': 'ran', 'cmd': cmd, 'job_dir': str(out_dir / agent), **reward}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='harbor 任务包全量 oracle/nop 验证')
    ap.add_argument('--package', required=True, help='包根（建议传工作副本，执行不污染原包）')
    ap.add_argument('--run-dir', help='run 目录，默认取 --package 下的 asset-runs 最新批次')
    ap.add_argument('--asset', action='append', help='只验证这些资产目录（默认全部 harbor 任务）')
    ap.add_argument('--task-timeout', type=int, default=1800, help='单条 trial 超时秒数（默认 1800）')
    ap.add_argument('--venv-dir', help='自动安装 harbor 的 venv 位置（默认 $TMPDIR 复用）')
    ap.add_argument('--json-out')
    ap.add_argument('--skip-lint', action='store_true', help='不跑 harbor lint')
    args = ap.parse_args(argv)

    root = Path(args.package).expanduser().resolve()
    if not root.is_dir():
        print(f'包不存在：{root}', file=sys.stderr)
        return 2
    if iter_task_dirs is None or probe_task is None:
        print('scripts/_harbor.py 缺失，无法识别 harbor 任务', file=sys.stderr)
        return 2

    run_dir = Path(args.run_dir).resolve() if args.run_dir else None
    if run_dir is None:
        runs = sorted((root / 'asset-runs').glob('run-*')) if (root / 'asset-runs').is_dir() else []
        if not runs:
            print('找不到 run 目录：请传 --run-dir 或先建 asset-runs/run-*', file=sys.stderr)
            return 2
        run_dir = runs[-1]

    all_tasks = iter_task_dirs(root)
    if args.asset:
        wanted = {Path(a).resolve().name for a in args.asset}
        all_tasks = [t for t in all_tasks if any(p.name in wanted for p in [t, *t.parents][:4])]
    if not all_tasks:
        print('包内没有 harbor 任务（无 task.toml）——nothing to do')
        (run_dir / 'harbor-run-report.json').parent.mkdir(parents=True, exist_ok=True)
        (run_dir / 'harbor-run-report.json').write_text(json.dumps(_stamp({
            'tool': 'harbor_run.py', 'verdict': 'not_applicable_no_harbor_tasks',
            'tasks': []}, ), ensure_ascii=False, indent=2), encoding='utf-8')
        return 0

    ok_docker, docker_msg = docker_ok()
    if not ok_docker:
        blocked = [{'task': str(t), 'status': 'blocked_docker', 'reason': docker_msg} for t in all_tasks]
        _write_report(run_dir, args, [], blocked, [], harbor_info={'docker': docker_msg})
        print(f'docker 不可用：{docker_msg}\n全部 {len(blocked)} 个任务记 blocked（exit 3）', file=sys.stderr)
        return 3

    venv_dir = Path(args.venv_dir).expanduser().resolve() if args.venv_dir else \
        Path(os.environ.get('TMPDIR', '/tmp')) / 'sci-asset-verify-harbor-venv'
    hb = ensure_harbor(venv_dir)
    if hb.get('bin') is None:
        blocked = [{'task': str(t), 'status': 'blocked_missing_harbor', 'reason': hb['reason']}
                   for t in all_tasks]
        _write_report(run_dir, args, [], blocked, [], harbor_info={'docker': docker_msg,
                                                                   'harbor': hb})
        print(f"harbor CLI 不可用：{hb['reason']}\n全部任务记 blocked（exit 3）", file=sys.stderr)
        return 3

    has_gpu = gpu_ok()
    host_env = set(os.environ)
    lint_supported = (not args.skip_lint) and _lint_available(hb['bin'])
    if (not args.skip_lint) and not lint_supported:
        lint_note = ('harbor 版本无 lint 子命令（0.23 及更早），CHK-HARB-07/08 未采集；'
                     '建议用 harbor≥0.24 复跑采集')
        print(f'[harbor] {lint_note}', file=sys.stderr)
    else:
        lint_note = None
    results, blocked, findings = [], [], []

    def add_blocked(task, status, reason):
        blocked.append({'task': str(task), 'status': status, 'reason': reason})

    for task in all_tasks:
        rel = task.relative_to(root)
        facts = probe_task(task)
        if facts.get('declared_os') == 'windows':
            add_blocked(task, 'blocked_unsupported_platform',
                        '任务声明 environment.os=windows，核验环境为 Linux，无法构建 Windows 容器')
            continue
        gpus = facts.get('declared_gpus') or 0
        if gpus > 0 and not has_gpu:
            add_blocked(task, 'blocked_no_gpu',
                        f'任务声明 gpus={gpus}，核验环境无 NVIDIA GPU / nvidia runtime')
            continue
        missing_env = [v for v in facts.get('env_var_refs') or [] if v not in host_env]
        if missing_env:
            add_blocked(task, 'blocked_missing_env',
                        f'task.toml 引用的宿主环境变量缺失：{", ".join(missing_env)}'
                        '（oracle/nop 也需要这些变量进入环境/verifier）')
            continue

        out_base = run_dir / 'harbor' / rel
        oracle = run_trial(hb['bin'], task, 'oracle', out_base, args.task_timeout)
        nop = run_trial(hb['bin'], task, 'nop', out_base, args.task_timeout) \
            if oracle['status'] == 'ran' else {'status': 'skipped_oracle_not_run'}

        entry = {'task': str(rel), 'oracle': oracle, 'nop': nop}
        o_r, n_r = oracle.get('reward'), nop.get('reward')
        if oracle.get('status') == 'ran' and nop.get('status') == 'ran' \
                and o_r is not None and n_r is not None:
            ok = (o_r == 1.0 and n_r == 0.0)
            entry['verdict'] = 'pass' if ok else 'fail'
            if not ok:
                findings.append({
                    'check_id': 'CHK-HARB-02', 'level': 'P0', 'name': 'harbor oracle/nop 双 trial 通过',
                    'status': 'fail',
                    'detail': {'task': str(rel), 'oracle_reward': o_r, 'nop_reward': n_r,
                               'expectation': 'oracle=1 且 nop=0',
                               'evidence': [oracle.get('source'), nop.get('source')]}})
        else:
            reason = (oracle.get('error') or oracle.get('note')
                      or (f'oracle 未跑成（{oracle.get("status")}），nop 未执行'))
            entry['verdict'] = 'blocked'
            entry['reason'] = reason
            add_blocked(task, f"blocked_{oracle.get('status', 'error')}", reason)
        results.append(entry)

        if lint_supported and hb.get('bin'):
            lint = run_lint(hb['bin'], task, args.task_timeout)
            if lint.get('ok'):
                if lint.get('correctness'):
                    findings.append({'check_id': 'CHK-HARB-07', 'level': 'P1',
                                     'name': 'harbor lint 正确性发现', 'status': 'fail',
                                     'detail': {'task': str(rel), 'rules': lint['correctness']}})
                if lint.get('reproducibility'):
                    findings.append({'check_id': 'CHK-HARB-08', 'level': 'P2',
                                     'name': 'harbor lint 可复现性建议', 'status': 'fail',
                                     'detail': {'task': str(rel), 'rules': lint['reproducibility']}})
            else:
                results[-1]['lint_unparsed'] = lint.get('error')

    report_path = _write_report(run_dir, args, results, blocked, findings,
                                harbor_info={'docker': docker_msg, 'harbor': hb, 'has_gpu': has_gpu,
                                             'lint': ('skipped' if args.skip_lint else
                                                      'unavailable' if not lint_supported else 'collected'),
                                             'lint_note': lint_note})
    passed = sum(1 for r in results if r.get('verdict') == 'pass')
    failed = sum(1 for r in results if r.get('verdict') == 'fail')
    print(f"harbor 验证完成：通过 {passed} / 失败 {failed} / blocked {len(blocked)}"
          f"（共 {len(all_tasks)} 个任务）")
    for f in findings:
        print(f"  [{f['level']}] {f['check_id']} {f['name']}")
    print(f"报告：{report_path}")
    if failed or any(f['level'] == 'P1' for f in findings):
        return 1
    if blocked:
        return 3
    return 0


def _write_report(run_dir: Path, args, results, blocked, findings, harbor_info: dict) -> Path:
    path = Path(args.json_out) if args.json_out else run_dir / 'harbor-run-report.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'tool': 'harbor_run.py',
        'generated_at': datetime.now().astimezone().isoformat(timespec='seconds'),
        'package': str(Path(args.package).expanduser().resolve()),
        'harbor': harbor_info,
        'verdict': ('not_applicable_no_harbor_tasks' if not results and not blocked else
                    'fail' if any(f['level'] in ('P0', 'P1') for f in findings) else
                    'blocked' if blocked else 'pass'),
        'summary': {'total': len(results) + len(blocked),
                    'passed': sum(1 for r in results if r.get('verdict') == 'pass'),
                    'failed': sum(1 for r in results if r.get('verdict') == 'fail'),
                    'blocked': len(blocked)},
        'tasks': results,
        'blocked_tasks': blocked,
        'findings': findings,
    }
    path.write_text(json.dumps(_stamp(payload), ensure_ascii=False, indent=2), encoding='utf-8')
    return path


if __name__ == '__main__':
    raise SystemExit(main())
