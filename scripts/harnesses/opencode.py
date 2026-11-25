"""OpenCode 适配器。"""
from .base import HarnessAdapter

ADAPTER = HarnessAdapter(
    name="opencode",
    display="OpenCode",
    bins=("opencode",),
    argv=("{bin}", "run", "--title", "资产包核验", "--auto", "{model}", "{prompt}"),
    order=4,
    model_flag=("--model", "{model}"),
    sage={"kind": "env", "env": {"OPENAI_BASE_URL": "{base_url}", "OPENAI_API_KEY": "{api_key}"}},
    note="opencode run（非交互）。必须 --title（否则用默认模型生成标题，provider 不可达时卡死）与 --auto"
         "（否则等审批挂住）；模型默认取 ~/.config/opencode/opencode.json 的默认值。",
    verified="ok",
)
