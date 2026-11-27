"""environment 资产：镜像/依赖/网络策略（当前无专项检查，声明层由通用检查覆盖）

占位模块：接口与其他类型一致，目前不产专项 checks（返回空字典），
将来加检查时只改本文件，不动 probe_package.py。
"""
from __future__ import annotations

from .base import Ctx

NAME = 'environment'
APPLIES_TO = ('environment',)


def probe(ctx: Ctx) -> dict:
    return {}
