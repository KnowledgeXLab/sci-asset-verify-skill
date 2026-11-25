"""Qwen Code 适配器（Gemini CLI 同源）。"""
from .base import HarnessAdapter

ADAPTER = HarnessAdapter(
    name="qwen",
    display="Qwen Code",
    bins=("qwen",),
    argv=("{bin}", "-p", "{prompt}", "--approval-mode", "yolo"),
    order=7,
    model_flag=("-m", "{model}"),
    sage={"kind": "env", "env": {"OPENAI_BASE_URL": "{base_url}", "OPENAI_API_KEY": "{api_key}"}},
    note="qwen -p 单次非交互执行（Gemini CLI 同源）；--approval-mode yolo 让工具调用自动放行。"
         "端点与鉴权走宿主 ~/.qwen/settings.json 与其自身登录态。",
    verified="",
)
