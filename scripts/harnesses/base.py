"""Harness adapters：把"某个 agent CLI 怎么非交互地跑一次 prompt"收敛成一个小对象。

设计要点
--------
* **一个 harness 一个文件、一个类**：新增 harness 只需新增文件 + 在 ``__init__`` 注册；
* **默认用宿主机的默认配置**：适配器不猜环境变量、不注入凭据；
  需要宿主 shell 配置（PATH、代理、端点）时，用登录 shell 包一层（``wrap_login_shell``）；
* **Sage 是唯一例外**：只由 Sage 相关流程显式注入 ``SAGE_*``（见 ``sage`` 字段）；
* **无头能力显式声明**：桌面应用（如 ZCode）标记 ``headless=False`` 并给出原因，
  调用方必须据此报错，而不是硬跑一个 GUI 进程。
"""
from __future__ import annotations

import shlex
import shutil
from dataclasses import dataclass, field


@dataclass(frozen=True)
class HarnessAdapter:
    """一个 harness CLI 的非交互调用契约。"""

    name: str
    display: str
    bins: tuple[str, ...]
    argv: tuple[str, ...]
    order: int = 100
    headless: bool = True
    not_headless_reason: str = ""
    model_flag: tuple[str, ...] = ()
    sage: dict = field(default_factory=lambda: {"kind": "env"})
    note: str = ""
    wrap_login_shell: bool = True      # bash -lc：让宿主 ~/.bashrc / profile 生效（默认端点配置）
    verified: str = ""                 # "" / "ok" / "blocked:<原因>"

    # ---- 解析与构造 ----
    def resolve_bin(self) -> str:
        for b in self.bins:
            exe = shutil.which(b)
            if exe:
                return exe
        return ""

    def build(self, prompt: str, ctx: dict | None = None) -> list[str]:
        """渲染 argv。占位符：{bin} {prompt} {model} {skill_dir} {package} {run} {contract}"""
        ctx = dict(ctx or {})
        exe = ctx.get("bin") or self.resolve_bin() or self.bins[0]
        model = str(ctx.get("model") or "")
        values = {"bin": exe, "prompt": prompt, "model": model, **ctx}
        argv: list[str] = []
        i = 0
        while i < len(self.argv):
            tok = self.argv[i]
            if tok == "{model}":
                if model:
                    argv.append(model)
                else:
                    # 未指定模型：把它连同前一个 flag 一起去掉，交给 harness 用它自己的默认模型
                    if argv and argv[-1] in (self.model_flag[:1] or ("--model", "-m")):
                        argv.pop()
                i += 1
                continue
            argv.append(str(values.get(tok[1:-1], tok)) if tok.startswith("{") and tok.endswith("}") else tok)
            i += 1
        if model and "{model}" not in self.argv and self.model_flag:
            argv += [model if t == "{model}" else t for t in self.model_flag]
        return argv

    def wrap(self, argv: list[str]) -> list[str]:
        """用**交互式** shell 包一层，复现宿主终端的默认配置（端点/代理/PATH）。

        为什么是 ``-ic`` 而不是 ``-lc``：多数 Linux 发行版的 ``~/.bashrc`` 开头有
        ``case $- in *i*) ;; *) return;; esac``，非交互 shell 会直接 return，
        于是用户在 bashrc 里配的端点变量不会生效。``-ic`` 与用户在终端里敲命令的环境一致，
        这也是"用宿主机默认配置、不猜环境变量"的最直接做法。
        """
        if not self.wrap_login_shell:
            return argv
        shell = shutil.which("bash") or "/bin/sh"
        return [shell, "-ic", shlex.join(argv)]

    def env(self, _ctx: dict | None = None) -> dict:
        """默认不注入任何环境变量：一切交给宿主默认配置。"""
        return {}

    # ---- 展示 ----
    def describe(self) -> dict:
        exe = self.resolve_bin()
        return {"name": self.name, "display": self.display, "bins": list(self.bins),
                "installed": bool(exe), "bin": exe, "headless": self.headless,
                "not_headless_reason": self.not_headless_reason,
                "argv": " ".join(self.argv), "sage": self.sage, "note": self.note,
                "verified": self.verified}
