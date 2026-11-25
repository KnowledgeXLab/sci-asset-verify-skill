"""OpenAI Codex CLI 适配器。"""
from .base import HarnessAdapter

ADAPTER = HarnessAdapter(
    name="codex",
    display="OpenAI Codex CLI",
    bins=("codex",),
    argv=("{bin}", "exec", "--skip-git-repo-check", "-s", "workspace-write", "{prompt}"),
    order=2,
    priority=1,
    model_flag=("-m", "{model}"),
    sage={"kind": "env", "env": {"OPENAI_BASE_URL": "{base_url}", "OPENAI_API_KEY": "{api_key}"}},
    note="codex exec 非交互；-s workspace-write 允许在 workspace 内执行。端点与密钥走 ~/.codex/config.toml 与宿主环境。",
    verified="ok",
)
