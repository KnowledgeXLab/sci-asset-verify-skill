"""ZCode 适配器。

ZCode（z.ai 的 GLM 编码 agent）目前是 **Electron 桌面应用**，`zcode` 可执行文件只启动 GUI，
没有可用的非交互 CLI。按适配器约定显式声明 headless=False，调用方必须报错而不是硬跑 GUI。
"""
from .base import HarnessAdapter

ADAPTER = HarnessAdapter(
    name="zcode",
    display="ZCode（桌面应用）",
    bins=("zcode",),
    argv=("{bin}", "{prompt}"),
    order=8,
    headless=False,
    not_headless_reason="ZCode 是 Electron 桌面应用（/opt/ZCode/zcode 只启动 GUI），没有非交互 CLI；"
                        "无法用于 --isolated 或子智能体自动核验。如后续版本提供 headless 子命令，"
                        "只需改本文件的 argv 并把 headless 置回 True。",
    sage={"kind": "unsupported", "why": "无 headless CLI，无法注入 Sage 后端"},
    note="仅登记用途，暂不可自动调用。",
    verified="blocked:桌面应用，无 headless CLI",
)
