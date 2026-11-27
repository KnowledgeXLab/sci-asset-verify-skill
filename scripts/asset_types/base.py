"""类型专项检查的公共上下文。

约定：每个 `scripts/types/<type>.py` 暴露
  NAME          —— 资产类型（与 asset_type 取值一致）
  APPLIES_TO    —— 该模块检查哪些资产（None 表示全部）
  probe(ctx)    —— 返回 {checks 键: 结构}，键名与 probe_package 的 checks 完全一致（口径不变）
新增一种资产的专项检查：加一个文件 + 在 __init__ 的 MODULES 里追加一行。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Ctx:
    """一次探测的只读上下文。"""
    root: Path
    assets: list[Path]
    metas: dict[str, dict]
    run_dir: Path | None = None
    static_only: bool = False

    def of_type(self, *types: str) -> list[Path]:
        return [d for d in self.assets
                if str((self.metas.get(d.name) or {}).get('asset_type')) in types]

    def meta(self, d: Path) -> dict:
        return self.metas.get(d.name) or {}


def load(path: Path):
    """读 JSON；失败返回 None（与 probe_package 原来的 load 语义一致，调用方据此判"有没有文件"）。"""
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:  # noqa: BLE001
        return None
