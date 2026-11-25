"""ZCode CLI 适配器（z.ai 的 GLM 编码 agent）。

注意区分两个可执行文件：
* ``zcode``     —— Electron 桌面应用，只启动 GUI，**不能**无头调用；
* ``zcode-cli`` —— 终端版，支持 ``--prompt`` 单次执行（``--prompt`` 下 ``--mode`` 默认 yolo）。
"""
from .base import HarnessAdapter

ADAPTER = HarnessAdapter(
    name="zcode",
    display="ZCode CLI",
    bins=("zcode-cli",),
    argv=("{bin}", "--prompt", "{prompt}"),
    order=8,
    model_flag=("--model", "{model}"),
    sage={"kind": "env", "env": {"OPENAI_BASE_URL": "{base_url}", "OPENAI_API_KEY": "{api_key}"}},
    note="zcode-cli --prompt 单次执行；--prompt 下权限模式默认 yolo（自动放行）。"
         "模型与登录态走宿主 ~/.zcode 配置（`zcode-cli login`）。"
         "注意别用 `zcode`（桌面 GUI，无 headless）。",
    verified="ok",
)
