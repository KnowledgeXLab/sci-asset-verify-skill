"""Cursor CLI 适配器。"""
from .base import HarnessAdapter

ADAPTER = HarnessAdapter(
    name="cursor",
    display="Cursor CLI",
    bins=("agent", "cursor-agent", "cursor"),
    argv=("{bin}", "-p", "--force", "--output-format", "text", "{prompt}"),
    order=5,
    sage={"kind": "unsupported", "why": "Cursor 只连自家后端，无法指向自定义 base_url"},
    note="Cursor CLI 的 agent -p（headless）；--force 跳过审批。鉴权走 CURSOR_API_KEY 或先 `agent login`；"
         "团队关闭 headless 模式时 -p 会直接报错。",
    verified="blocked:未鉴权（需 agent login 或 CURSOR_API_KEY）",
)
