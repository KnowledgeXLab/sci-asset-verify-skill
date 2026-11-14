#!/usr/bin/env python3
"""包内布局约定：哪些目录算「本包的资产」，哪些是核验期临时放进来的东西。

为什么单独抽出来：`mount_external.py` 会把包外的被引资产**复制进工作副本**（`<副本根>/<目录名>/`），
并在里面放一个 `.mounted-external.json` 标记。校验器扫 `asset.json` 时必须跳过这些目录——它们不是
本次核验的资产，把它们算进包络/自包含/资产ID/清单会让"4 个资产"变成"5 个"。
"""
from __future__ import annotations

from pathlib import Path

EXTERNAL_MARKER = '.mounted-external.json'      # mount_external.py 复制外部资产时留下的标记
SKIP_PARTS = {'asset-runs', '.git', '__pycache__', 'node_modules', '.venv', 'venv',
              '.asset-work', '.mypy_cache', '.pytest_cache'}


def is_mounted_external(d: Path) -> bool:
    """该目录是核验期挂载进来的外部资产副本吗？"""
    try:
        return (Path(d) / EXTERNAL_MARKER).is_file()
    except OSError:
        return False


def is_package_asset_dir(d: Path, root: Path) -> bool:
    """该目录是不是「本包声明的资产目录」：有 asset.json、不在临时目录里、不是挂载进来的。"""
    d = Path(d)
    try:
        rel = d.relative_to(Path(root))
    except ValueError:
        rel = d
    if SKIP_PARTS & set(rel.parts):
        return False
    if not (d / 'asset.json').is_file():
        return False
    return not is_mounted_external(d)


def package_asset_dirs(root: Path) -> list[Path]:
    """包内资产目录（按路径排序，已剔除挂载进来的外部资产）。"""
    out, seen = [], set()
    for f in sorted(Path(root).rglob('asset.json')):
        d = f.parent
        if d in seen:
            continue
        if is_package_asset_dir(d, root):
            seen.add(d)
            out.append(d)
    return out
