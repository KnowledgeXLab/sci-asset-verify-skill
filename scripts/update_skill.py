#!/usr/bin/env python3
"""开跑前检查远端最新 tag，按需把本 skill 更新到该 tag（让使用方不必人工 pull）。

设计原则
--------
* **不阻断核验**：无网络、非 git 安装、工作区有未提交改动时一律降级为「提示 + 记录」，核验照常进行。
* **可追溯**：更新结果写 `<run>/skill-update.json`；报告 front matter 里的 `skill_version`/`skill_commit`
  由 `_version.py` 从当前磁盘版本读取，因此更新后自动反映新版本。
* **安全**：只在工作区干净时切换；用 `git checkout <tag>` 定位到 release tag（可复现），不合并、不改写历史。
* **可关闭**：`--no-update` 或环境变量 `ASSET_VERIFY_NO_UPDATE=1` 跳过。

用法
----
  python scripts/update_skill.py --check                     # 只看有没有新版
  python scripts/update_skill.py --apply --json-out F        # 有新版就切过去
  python scripts/update_skill.py --apply --no-update         # 显式跳过（记录原因）

退出码：0 已是最新/已更新/按策略跳过；1 更新失败（工作区脏、切换失败等）；2 参数或 IO 错误。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
# 非 git 安装（拷贝/解压/容器内分发）也要能查远端版本：用登记仓库地址
DEFAULT_REMOTE = 'git@github.com:KnowledgeXLab/sci-asset-verify-skill.git'
MANUAL_HINT = '发现新版本 {latest}（当前 {cur}），但当前安装方式/权限不支持自动更新，请手动更新 skill 后重试'
TAG_RE = re.compile(r'^v?(\d+)\.(\d+)(?:\.(\d+))?(?:-(alpha|beta|rc)\.?(\d+))?$')
STAGE = {'alpha': 0, 'beta': 1, 'rc': 2, None: 3}


def parse_version(text: str):
    raw = str(text).strip()
    raw = re.sub(r'-(dev|dirty|snapshot)$', '', raw)   # 开发版后缀按同号处理
    m = TAG_RE.match(raw)
    if not m:
        return None
    major, minor, patch, stage, num = m.groups()
    return (int(major), int(minor), int(patch or 0), STAGE.get(stage, 3), int(num or 0))


def local_version() -> str:
    f = SKILL_ROOT / 'VERSION'
    return f.read_text(encoding='utf-8').strip() if f.is_file() else ''


def git(*args, cwd=SKILL_ROOT) -> tuple[int, str]:
    try:
        p = subprocess.run(['git', *args], cwd=str(cwd), capture_output=True, text=True, timeout=30)
        return p.returncode, (p.stdout or '') + (p.stderr or '')
    except Exception as exc:  # noqa: BLE001
        return 127, str(exc)


def is_git_repo() -> bool:
    return (SKILL_ROOT / '.git').exists() or git('rev-parse', '--git-dir')[0] == 0


def remote_url() -> str:
    """远端地址：优先 origin，其次环境变量，最后内置的登记仓库（非 git 安装也能查版本）。"""
    if is_git_repo():
        rc, out = git('remote', 'get-url', 'origin')
        if rc == 0 and out.strip():
            return out.strip().splitlines()[0]
    return os.environ.get('ASSET_VERIFY_SKILL_REMOTE') or DEFAULT_REMOTE


def remote_tags() -> list[str]:
    url = remote_url()
    rc, out = git('ls-remote', '--tags', url) if url else (1, '')
    if rc != 0:
        return []
    tags = []
    for line in out.splitlines():
        if 'refs/tags/' not in line or line.strip().endswith('^{}'):
            continue
        tags.append(line.split('refs/tags/', 1)[1].strip())
    return tags


def newest(tags: list[str]) -> str:
    parsed = [(parse_version(t), t) for t in tags]
    parsed = [(v, t) for v, t in parsed if v]
    return max(parsed)[1] if parsed else ''


def writable() -> tuple[bool, str]:
    """能否写本 skill 的目录（自动更新要写工作区与 .git）。"""
    import tempfile
    for target in (SKILL_ROOT, SKILL_ROOT / '.git'):
        if not target.exists():
            continue
        try:
            with tempfile.NamedTemporaryFile(dir=str(target), prefix='.write-probe-', delete=True):
                pass
        except Exception as exc:  # noqa: BLE001
            return False, f'{target} 不可写：{exc}'
    return True, ''


def dirty() -> bool:
    rc, out = git('status', '--porcelain')
    return rc == 0 and bool(out.strip())


def current_branch() -> str:
    rc, out = git('rev-parse', '--abbrev-ref', 'HEAD')
    return out.strip() if rc == 0 else ''


def is_dev_checkout() -> bool:
    """开发分支（非 main/master/detached）不参与自动切换版本，避免把开发工作区切走。"""
    br = current_branch()
    return bool(br) and br not in ('main', 'master', 'HEAD')


def current_tag() -> str:
    rc, out = git('describe', '--tags', '--exact-match')
    if rc == 0 and out.strip():
        return out.strip().splitlines()[0]
    rc, out = git('describe', '--tags', '--abbrev=0')
    return out.strip().splitlines()[0] if rc == 0 and out.strip() else ''


def check() -> dict:
    """无论是否 git 安装，都先问远端有没有更新版本；不能自动更新时明确要求用户手动更新。"""
    cur = local_version()
    tags = remote_tags()
    if not tags:
        return {'status': 'offline_or_no_remote', 'current': cur, 'remote': remote_url(),
                'reason': '读不到远端 tag（离线或仓库不可达），无法判断是否有更新'}
    latest = newest(tags)
    if not latest:
        return {'status': 'no_tags', 'current': cur, 'reason': '远端没有可比较的版本 tag'}
    base = newest([x for x in (cur, current_tag()) if x]) or cur
    if parse_version(latest) and parse_version(base) and parse_version(latest) <= parse_version(base):
        return {'status': 'up_to_date', 'current': cur, 'latest': latest}

    # 有更新：判断能不能自动完成
    if not is_git_repo():
        return {'status': 'update_available_manual', 'current': cur, 'latest': latest,
                'reason': MANUAL_HINT.format(latest=latest, cur=cur)
                          + '（当前安装不是 git 仓库：请手动下载最新版并替换目录，或改用 git clone 安装）'}
    if is_dev_checkout():
        return {'status': 'dev_branch', 'current': cur, 'latest': latest,
                'reason': f'当前在开发分支 {current_branch()}，不自动切换发布版本；'
                          f'若这是部署目录，请手动 git checkout {latest}'}
    ok, why = writable()
    if not ok:
        return {'status': 'update_available_manual', 'current': cur, 'latest': latest,
                'reason': MANUAL_HINT.format(latest=latest, cur=cur) + f'（{why}）'}
    return {'status': 'behind', 'current': cur, 'latest': latest}


def apply_update(latest: str) -> dict:
    ok, why = writable()
    if not ok:
        return {'status': 'not_writable', 'action': 'skipped',
                'reason': f'没有写权限，无法自动更新（{why}）。'
                          '可在核验会话里放宽写权限，或由使用者手动 git pull / 手动下载最新版替换'}
    if dirty():
        return {'status': 'dirty_worktree', 'action': 'skipped',
                'reason': '工作区有未提交改动，自动更新会覆盖你的修改；请先提交或 stash'}
    rc, out = git('fetch', '--tags', '--quiet', 'origin')
    if rc != 0:
        low = out.lower()
        if any(k in out for k in ('权限不够', 'permission denied', 'EACCES')) or 'denied' in low:
            return {'status': 'not_writable', 'action': 'skipped',
                    'reason': '没有写权限，无法自动更新（git fetch 被拒）。'
                              '可放宽会话写权限，或手动 pull / 手动下载最新版替换'}
        return {'status': 'fetch_failed', 'action': 'skipped', 'reason': out.strip()[:300]}
    rc, out = git('checkout', '--quiet', latest)
    if rc != 0:
        return {'status': 'checkout_failed', 'action': 'skipped', 'reason': out.strip()[:300]}
    return {'status': 'updated', 'action': 'checked_out', 'to': latest,
            'new_version': local_version()}


def run_selftest() -> dict:
    t = SKILL_ROOT / 'tests' / 'selftest.py'
    if not t.is_file():
        return {'ran': False}
    try:
        p = subprocess.run([sys.executable, str(t)], cwd=str(SKILL_ROOT),
                           capture_output=True, text=True, timeout=180)
        tail = [l for l in (p.stdout or '').splitlines() if '自测结果' in l]
        return {'ran': True, 'rc': p.returncode, 'summary': tail[-1] if tail else ''}
    except Exception as exc:  # noqa: BLE001
        return {'ran': True, 'rc': -1, 'error': str(exc)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='开跑前检查/更新本 skill 到远端最新 tag')
    ap.add_argument('--check', action='store_true', help='只检查，不更新')
    ap.add_argument('--apply', action='store_true', help='有新版本就切换过去')
    ap.add_argument('--no-update', action='store_true', help='显式跳过自动更新')
    ap.add_argument('--json-out')
    args = ap.parse_args(argv)

    if args.no_update or os.environ.get('ASSET_VERIFY_NO_UPDATE') == '1':
        result = {'status': 'disabled', 'current': local_version(),
                  'reason': '按 --no-update / ASSET_VERIFY_NO_UPDATE=1 跳过'}
    else:
        result = check()
        if args.apply and result['status'] == 'behind' and not args.check:
            result.update(apply_update(result['latest']))
            if result.get('status') == 'updated':
                result['selftest'] = run_selftest()
            elif result.get('status') in ('not_writable', 'dirty_worktree', 'fetch_failed', 'checkout_failed'):
                # 有新版但自动更新没成：统一升级为"需手动更新"，并给出明确指引
                result['status'] = 'update_available_manual'
                result['reason'] = MANUAL_HINT.format(latest=result['latest'], cur=result['current']) \
                    + f'（{result.get("reason", "")}）'
                result['auto_update'] = 'failed'

    payload = {'tool': 'update_skill.py', 'skill_root': str(SKILL_ROOT), **result}
    label = {'up_to_date': '已是最新', 'updated': '已更新', 'behind': '有新版（未更新）',
             'update_available_manual': '有新版，需手动更新',
             'not_writable': '无写权限，跳过自动更新',
             'dev_branch': '开发分支，跳过自动更新',
             'not_git': '非 git 安装，跳过', 'offline_or_no_remote': '离线/无 origin，跳过',
             'dirty_worktree': '工作区脏，跳过', 'disabled': '已关闭自动更新',
             'fetch_failed': '拉取失败', 'checkout_failed': '切换失败'}.get(result['status'], result['status'])
    print(f"[skill 版本] {label}：当前 {result.get('current')}"
          + (f" → 远端 {result.get('latest')}" if result.get('latest') else '')
          + (f"（{result['reason']}）" if result.get('reason') else ''))
    if result.get('selftest', {}).get('ran'):
        print(f"[skill 版本] 更新后自测：{result['selftest'].get('summary') or result['selftest'].get('rc')}")
    if args.json_out:
        p = Path(args.json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if result['status'] in ('up_to_date', 'updated', 'behind', 'not_git',
                                     'offline_or_no_remote', 'disabled', 'not_writable',
                                     'dev_branch', 'update_available_manual') else 1


if __name__ == '__main__':
    raise SystemExit(main())
