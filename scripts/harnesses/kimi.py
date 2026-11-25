"""Kimi Code CLI 适配器。"""
from .base import HarnessAdapter

ADAPTER = HarnessAdapter(
    name="kimi",
    display="Kimi Code CLI",
    bins=("kimi",),
    argv=("{bin}", "-p", "{prompt}"),
    order=6,
    priority=2,
    model_flag=("-m", "{model}"),
    sage={"kind": "env", "env": {"OPENAI_BASE_URL": "{base_url}", "OPENAI_API_KEY": "{api_key}"}},
    note="kimi -p 单次非交互执行（注意：-p 与 --auto 互斥，不能一起用）。"
         "端点与模型走宿主 ~/.kimi/config.toml（默认模型），不注入任何环境变量。",
    verified="",
)
