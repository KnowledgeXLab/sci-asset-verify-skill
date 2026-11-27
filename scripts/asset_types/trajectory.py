"""trajectory 资产：ATIF 结构（当前无专项检查，格式层由 validate_envelope 覆盖）

占位模块：接口与其他类型一致，目前不产专项 checks（返回空字典），
将来加检查时只改本文件，不动 probe_package.py。
"""
from __future__ import annotations

from .base import Ctx

NAME = 'trajectory'
APPLIES_TO = ('trajectory',)


def probe(ctx: Ctx) -> dict:
    return {}
