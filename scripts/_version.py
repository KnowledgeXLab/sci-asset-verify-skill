#!/usr/bin/env python3
"""skill 版本的单一来源。

为什么单独一个文件：报告和每一份证据 JSON 都要能回答「这次核验是用哪个版本的校验 skill 做的」，
若各脚本各写一份，必然漂移。版本号以仓库根的 `VERSION` 为准，有 git 时同时记录提交号，
便于回溯到确切的代码状态。

用法（其它脚本内）：
    try:
        from _version import stamp
    except Exception:
        def stamp(p): return p
    payload = stamp(payload)          # 写入 payload['skill'] = {name, version, commit, ...}
"""
from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

NAME = 'sci-asset-verify-skill'
ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / 'VERSION'


def _from_file():
    try:
        v = VERSION_FILE.read_text(encoding='utf-8').strip()
        return v or None
    except Exception:  # noqa: BLE001
        return None


def _git(*args):
    try:
        out = subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True,
                             text=True, timeout=5)
        return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None
    except Exception:  # noqa: BLE001
        return None


def skill_version() -> str:
    """优先 VERSION 文件；没有就用 git 描述；都没有记 unknown。"""
    return _from_file() or _git('describe', '--tags', '--always') or 'unknown'


def skill_info() -> dict:
    commit = _git('rev-parse', '--short', 'HEAD')
    src = 'VERSION' if _from_file() else ('git' if commit else 'unknown')
    return {'name': NAME, 'version': skill_version(), 'commit': commit, 'version_source': src}


def stamp(payload):
    """把 skill 版本写进任意 dict 型证据；非 dict 原样返回。"""
    if isinstance(payload, dict):
        payload['skill'] = skill_info()
    return payload


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


if __name__ == '__main__':
    import json
    print(json.dumps(skill_info(), ensure_ascii=False, indent=2))
