#!/usr/bin/env python3
"""依赖引导：让清单表生成不因缺 openpyxl 而中断。

清单表是**必经步骤**，所以依赖必须随 skill 走。解析顺序：

  1. 当前环境已装的 openpyxl（尊重用户环境）
  2. skill 自带的 `vendor/`（openpyxl 3.1.5 + et_xmlfile 2.0.0，纯 Python，随仓库分发）
  3. 按需自动安装到 `vendor/`：`python -m pip install --target vendor "openpyxl>=3.1,<4"`
     （`ACI_ASSET_VERIFY_AUTO_INSTALL=0` 可关闭；离线环境建议用第 2 条）

三个都不成时抛 RuntimeError，并给出可直接复制的修复命令——不静默失败。

用法：
  import _deps
  _deps.ensure_openpyxl()      # 之后正常 import openpyxl 即可
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
VENDOR = SKILL_ROOT / 'vendor'
SPEC = 'openpyxl>=3.1,<4'
FIX_HINT = (
    '修复方式（任选其一）：\n'
    '  1) 用带 openpyxl 的解释器：python -m pip install openpyxl\n'
    f'  2) 手工装入 skill 自带 vendor/：python -m pip install --target "{VENDOR}" "{SPEC}"\n'
    '  3) 允许自动安装：不要设 ACI_ASSET_VERIFY_AUTO_INSTALL=0（默认就允许）'
)


def _probe() -> tuple[bool, str | None, str | None]:
    try:
        import openpyxl  # noqa: F401
    except Exception:  # noqa: BLE001
        return False, None, None
    return True, getattr(openpyxl, '__version__', '?'), getattr(openpyxl, '__file__', None)


def _auto_install(verbose: bool) -> bool:
    if os.environ.get('ACI_ASSET_VERIFY_AUTO_INSTALL', '1') == '0':
        return False
    try:
        VENDOR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    if verbose:
        print(f'[deps] 未找到 openpyxl，尝试自动安装到 {VENDOR} …', file=sys.stderr)
    for cmd in ([sys.executable, '-m', 'pip', 'install', '--quiet', '--target', str(VENDOR), SPEC],
                [sys.executable, '-m', 'pip', 'install', '--quiet', '--user', SPEC]):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except Exception:  # noqa: BLE001
            continue
        if proc.returncode == 0:
            if '--target' in cmd:
                sys.path.insert(0, str(VENDOR))
            return True
        if verbose:
            print(f"[deps] 安装失败：{' '.join(cmd[:6])} … {proc.stderr.strip()[:200]}", file=sys.stderr)
    return False


def ensure_openpyxl(verbose: bool = True) -> dict:
    """确保 openpyxl 可导入；返回 {ok, version, source}。失败抛 RuntimeError。"""
    ok, ver, where = _probe()
    if ok:
        return {'ok': True, 'version': ver, 'source': 'environment', 'path': where}

    if VENDOR.is_dir():
        sys.path.insert(0, str(VENDOR))
        ok, ver, where = _probe()
        if ok:
            if verbose:
                print(f'[deps] 使用 skill 自带 openpyxl {ver}（vendor/）', file=sys.stderr)
            return {'ok': True, 'version': ver, 'source': 'vendor', 'path': where}

    if _auto_install(verbose):
        ok, ver, where = _probe()
        if ok:
            if verbose:
                print(f'[deps] 已自动安装并使用 openpyxl {ver}', file=sys.stderr)
            return {'ok': True, 'version': ver, 'source': 'auto-install', 'path': where}

    raise RuntimeError('缺少 openpyxl（清单表生成/校验需要）。\n' + FIX_HINT)


if __name__ == '__main__':
    info = ensure_openpyxl()
    print(info)
