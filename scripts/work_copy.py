#!/usr/bin/env python3
"""资产副本工作区：在副本上执行，产物写回原始资产包。

约定
----
* 原始资产包**只写两处**：`<资产根目录>/asset-runs/<run_id>/`（最终产物：inventory/usage/state/
  cases/comparison/validation-report/登记表/部署记录等）与 `asset-runs/<run_id>/work-copy.json`。
* 所有会执行包内代码、写中间文件、需要清空输出目录的操作，都在**副本**里做：
  `WORK_ROOT/<run_id>/`，默认 `WORK_ROOT = <资产根目录父目录>/.asset-work`，
  可用 `--work-root` 或环境变量 `ASSET_WORK_ROOT` 覆盖；不可写时回退 `$TMPDIR/asset-work-<run_id>`。
* 校验结束后 `clean`：删副本 + 复核原始包摘要（存在 `SHA256SUMS` 时用 `LC_ALL=C sha256sum -c`），
  把清理与复核结果写进 `work-copy.json`。

用法
----
  python scripts/work_copy.py init  <资产根目录> --run-id <run_id> [--work-root DIR] [--manifest]
  python scripts/work_copy.py status <资产根目录> --run-id <run_id>
  python scripts/work_copy.py clean <资产根目录> --run-id <run_id> [--verify]

`init` 打印副本绝对路径（供后续命令 cd 进去使用）；退出码 0 成功、1 校验失败、2 参数/IO 错误。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

try:                                     # 版本戳：报告与证据 JSON 都要能追溯校验 skill 版本
    from _version import stamp as _stamp
except Exception:                        # noqa: BLE001
    def _stamp(payload):
        return payload

STATE_NAME = 'work-copy.json'
RUNS_DIR = 'asset-runs'


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


def run_dir(asset_root: Path, run_id: str) -> Path:
    return asset_root / RUNS_DIR / run_id


def state_path(asset_root: Path, run_id: str) -> Path:
    return run_dir(asset_root, run_id) / STATE_NAME


def load_state(asset_root: Path, run_id: str) -> dict | None:
    p = state_path(asset_root, run_id)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding='utf-8'))


def save_state(asset_root: Path, run_id: str, state: dict) -> Path:
    p = state_path(asset_root, run_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(_stamp(state), ensure_ascii=False, indent=2), encoding='utf-8')
    return p


def resolve_work_root(asset_root: Path, explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get('ASSET_WORK_ROOT')
    if env:
        return Path(env).expanduser().resolve()
    candidate = asset_root.parent / '.asset-work'
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / '.write-probe'
        probe.write_text('ok', encoding='utf-8')
        probe.unlink()
        return candidate
    except Exception:  # noqa: BLE001
        fallback = Path(tempfile.gettempdir()) / 'asset-work'
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


SKIP_PARTS = ('asset-runs', '__pycache__')
SKIP_SUFFIX = ('.pyc',)


def count_tree(root: Path, skip_runs: bool = False) -> tuple[int, int]:
    files = bytes_ = 0
    for p in root.rglob('*'):
        if not p.is_file():
            continue
        if skip_runs:
            rel = p.relative_to(root)
            if any(part in SKIP_PARTS for part in rel.parts) or p.suffix in SKIP_SUFFIX:
                continue
        files += 1
        try:
            bytes_ += p.stat().st_size
        except OSError:
            pass
    return files, bytes_


def verify_copy(source: Path, copy: Path) -> dict:
    """副本建立后的完整性自检：文件数/字节数与源一致，并在源有 SHA256SUMS 时逐个复核副本。"""
    src_files, src_bytes = count_tree(source, skip_runs=True)
    cp_files, cp_bytes = count_tree(copy, skip_runs=True)
    out = {'source_files': src_files, 'source_bytes': src_bytes,
           'copy_files': cp_files, 'copy_bytes': cp_bytes,
           'files_match': src_files == cp_files, 'bytes_match': src_bytes == cp_bytes,
           'sums': []}
    ok = out['files_match'] and out['bytes_match']
    env = dict(os.environ, LC_ALL='C')
    sums_files = sorted(source.glob('SHA256SUMS')) + sorted(source.glob('*/SHA256SUMS'))
    for sums in sums_files:
        rel_dir = sums.parent.relative_to(source)
        cp_dir = copy / rel_dir
        if not (cp_dir / sums.name).exists():
            out['sums'].append({'sums_file': str(sums.relative_to(source)), 'skipped': '副本中没有对应 SHA256SUMS'})
            continue
        proc = subprocess.run(['sha256sum', '-c', sums.name], cwd=cp_dir,
                              capture_output=True, text=True, env=env)
        lines = (proc.stdout or '').splitlines()
        ok_n = sum(1 for l in lines if l.endswith(': OK'))
        bad = [l for l in lines if l and not l.endswith(': OK')] + \
              [l for l in (proc.stderr or '').splitlines() if l]
        out['sums'].append({'sums_file': str(sums.relative_to(source)), 'exit_code': proc.returncode,
                            'ok': ok_n, 'bad': len(bad), 'bad_examples': bad[:5]})
        if proc.returncode != 0:
            ok = False
    out['ok'] = ok
    return out


def write_manifest(root: Path, out: Path) -> int:
    rows = []
    for p in sorted(root.rglob('*')):
        if p.is_file() and RUNS_DIR not in p.relative_to(root).parts:
            st = p.stat()
            rows.append({'path': str(p.relative_to(root)), 'bytes': st.st_size, 'mtime': int(st.st_mtime)})
    out.write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
    return len(rows)


def verify_sums(root: Path) -> dict:
    """在原始包上复核全部 SHA256SUMS（若存在）。中文 locale 会打印「成功」，必须强制 C locale。"""
    sums_files = sorted(root.glob('SHA256SUMS')) + sorted(root.glob('*/SHA256SUMS'))
    if not sums_files:
        return {'checked': False, 'reason': '包内无 SHA256SUMS'}
    env = dict(os.environ, LC_ALL='C')
    details, total_ok, total_bad, worst = [], 0, 0, 0
    for sums in sums_files:
        proc = subprocess.run(['sha256sum', '-c', sums.name], cwd=sums.parent,
                              capture_output=True, text=True, env=env)
        lines = (proc.stdout or '').splitlines()
        ok_lines = [l for l in lines if l.endswith(': OK')]
        bad_lines = [l for l in lines if l and not l.endswith(': OK')]
        stderr_lines = [l for l in (proc.stderr or '').splitlines() if l]
        total_ok += len(ok_lines)
        total_bad += len(bad_lines) + len(stderr_lines)
        worst = max(worst, proc.returncode)
        details.append({'sums_file': str(sums.relative_to(root)), 'exit_code': proc.returncode,
                        'ok': len(ok_lines), 'bad': len(bad_lines) + len(stderr_lines),
                        'bad_examples': (bad_lines + stderr_lines)[:5]})
    return {'checked': True, 'locale': 'LC_ALL=C', 'files_checked': len(sums_files),
            'ok_total': total_ok, 'bad_total': total_bad, 'worst_exit_code': worst,
            'details': details}


def cmd_init(args) -> int:
    root = Path(args.asset_root).expanduser().resolve()
    if not root.is_dir():
        print(f'资产根目录不存在：{root}', file=sys.stderr)
        return 2
    work_root = resolve_work_root(root, args.work_root)
    copy = work_root / args.run_id
    if copy.exists():
        print(f'副本已存在，复用：{copy}')
    else:
        copy.parent.mkdir(parents=True, exist_ok=True)
        # 只复制资产内容，不复制历史 run 产物
        shutil.copytree(root, copy, symlinks=True,
                        ignore=shutil.ignore_patterns(RUNS_DIR, '__pycache__', '*.pyc'))
    verify = verify_copy(root, copy)
    files, bytes_ = count_tree(copy)
    state = load_state(root, args.run_id) or {}
    state_update = {'init_verify': verify}
    if not verify['ok']:
        shutil.rmtree(copy, ignore_errors=True)
        state.update(state_update)
        state.update({'run_id': args.run_id, 'source_root': str(root), 'work_copy': str(copy),
                      'copy_removed': True, 'failure': 'init 自检失败：副本与源不一致，已删除副本'})
        sp = save_state(root, args.run_id, state)
        print(f'init 自检失败：files {verify["source_files"]}→{verify["copy_files"]}，'
              f'bytes {verify["source_bytes"]}→{verify["copy_bytes"]}；副本已删除。记录：{sp}', file=sys.stderr)
        return 1
    state.update({'run_id': args.run_id, 'source_root': str(root), 'work_copy': str(copy),
                  'work_root_origin': ('explicit' if args.work_root else
                                       ('env' if os.environ.get('ASSET_WORK_ROOT') else 'default')),
                  'created_at': state.get('created_at') or now(),
                  'copy_files': files, 'copy_bytes': bytes_,
                  'init_verify': verify,
                  'rule': '执行只在副本中；原始包仅写 asset-runs/<run_id>/'})
    if args.manifest:
        mf = run_dir(root, args.run_id) / 'manifest-before.json'
        mf.parent.mkdir(parents=True, exist_ok=True)
        n = write_manifest(root, mf)
        state['manifest_before'] = {'file': str(mf.relative_to(root)), 'entries': n}
    sp = save_state(root, args.run_id, state)
    print(str(copy))
    print(f'# 副本就绪：{files} 个文件 / {bytes_} 字节；记录：{sp}', file=sys.stderr)
    return 0


def cmd_status(args) -> int:
    root = Path(args.asset_root).expanduser().resolve()
    state = load_state(root, args.run_id)
    if not state:
        print(f'未找到 {STATE_NAME}：{state_path(root, args.run_id)}', file=sys.stderr)
        return 2
    copy = Path(state['work_copy'])
    exists = copy.exists()
    out = {'run_id': args.run_id, 'source_root': state['source_root'], 'work_copy': str(copy),
           'copy_exists': exists}
    if exists:
        files, bytes_ = count_tree(copy)
        out.update({'copy_files': files, 'copy_bytes': bytes_})
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_clean(args) -> int:
    root = Path(args.asset_root).expanduser().resolve()
    state = load_state(root, args.run_id)
    if not state:
        print(f'未找到 {STATE_NAME}：{state_path(root, args.run_id)}', file=sys.stderr)
        return 2
    copy = Path(state['work_copy'])
    if copy.exists():
        # 安全边界：只删自己建的那个 <work_root>/<run_id>，且必须在源目录之外
        if root == copy or root in copy.parents:
            print(f'拒绝删除：副本路径位于资产根目录内（{copy}）', file=sys.stderr)
            return 2
        shutil.rmtree(copy)
        cleaned = True
    else:
        cleaned = False
    state['cleaned_at'] = now()
    state['copy_removed'] = cleaned
    state['copy_exists_after'] = copy.exists()
    if args.verify:
        state['verify_after_clean'] = verify_sums(root)
    save_state(root, args.run_id, state)
    print(json.dumps({k: state[k] for k in ('run_id', 'cleaned_at', 'copy_removed',
                                            'copy_exists_after', 'verify_after_clean')
                      if k in state}, ensure_ascii=False, indent=2))
    v = state.get('verify_after_clean') or {}
    if v.get('checked') and (v.get('worst_exit_code') or 0) != 0:
        return 1
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='资产副本工作区：init / status / clean')
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('init', 'status', 'clean'):
        p = sub.add_parser(name)
        p.add_argument('asset_root', help='原始资产根目录')
        p.add_argument('--run-id', required=True)
        if name == 'init':
            p.add_argument('--work-root', help='副本父目录（默认 <资产根目录父目录>/.asset-work）')
            p.add_argument('--manifest', action='store_true', help='记录原始包文件清单到 run 目录')
        if name == 'clean':
            p.add_argument('--verify', action='store_true', help='删除后复核原始包 SHA256SUMS')
    args = ap.parse_args(argv)
    return {'init': cmd_init, 'status': cmd_status, 'clean': cmd_clean}[args.cmd](args)


if __name__ == '__main__':
    sys.exit(main())
