"""DeepSeek Harness（DSH）适配器。"""
from .base import HarnessAdapter

ADAPTER = HarnessAdapter(
    name="dsh",
    display="DeepSeek Harness",
    bins=("dsh",),
    argv=("{bin}", "--profile", "headless", "--permission-mode", "workspace-write", "--", "{prompt}"),
    order=1,
    sage={"kind": "patch", "provider": "inkstone"},
    note="dsh --profile headless 一次性执行；--permission-mode workspace-write 允许在工作区读写。",
    verified="ok",
)
