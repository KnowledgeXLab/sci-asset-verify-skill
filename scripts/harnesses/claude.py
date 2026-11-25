"""Claude Code 适配器。"""
from .base import HarnessAdapter

ADAPTER = HarnessAdapter(
    name="claude",
    display="Claude Code",
    bins=("claude",),
    argv=("{bin}", "-p", "{prompt}", "--permission-mode", "bypassPermissions",
          "--add-dir", "{skill_dir}", "--add-dir", "{package}"),
    order=3,
    model_flag=("--model", "{model}"),
    sage={"kind": "env", "env": {"ANTHROPIC_BASE_URL": "{base_url}", "ANTHROPIC_AUTH_TOKEN": "{api_key}"}},
    note="claude -p 打印模式。非交互必须 bypassPermissions，并用 --add-dir 把 skill 与资产包目录加进允许范围；"
         "端点与鉴权走 ~/.claude/settings.json 与宿主环境。",
    verified="ok",
)
