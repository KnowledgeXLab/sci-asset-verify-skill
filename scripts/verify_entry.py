#!/usr/bin/env python3
"""跨 harness 的统一入口：把"固定 prompt"变成 skill 的产物，而不是人的记忆。

任何 harness（DSH / Claude Code / Codex / Cursor / 其它）都只需要：

  python3 scripts/verify_entry.py --package <包根> --init

它会创建规范批次目录 `asset-runs/run-YYYYMMDD-hhmmss/`，渲染固定执行合同
`<run>/verify-task.md`（模板 `prompts/verify-task.md.tmpl`），并写 `state.json` 骨架。
执行者（人或 agent）**照这份合同跑**，结论由脚本门禁校验，不依赖模型自觉。

只想看要执行什么（不落盘）：

  python3 scripts/verify_entry.py --package <包根> --emit-prompt

退出码：0 成功 / 1 资产包不存在 / 2 参数错误。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
TEMPLATE = SKILL_DIR / "prompts" / "verify-task.md.tmpl"
RUNS_DIR = "asset-runs"
RUN_ID = re.compile(r"^run-(\d{8})-(\d{6})$")


def skill_meta() -> dict:
    ver = ""
    f = SKILL_DIR / "VERSION"
    if f.is_file():
        ver = f.read_text(encoding="utf-8").strip()
    commit = ""
    try:
        import subprocess
        p = subprocess.run(["git", "-C", str(SKILL_DIR), "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=10)
        commit = (p.stdout or "").strip()
    except Exception:  # noqa: BLE001
        pass
    return {"skill_version": ver or "unknown", "skill_commit": commit or "unknown"}


def next_run_dir(package: Path, at: str | None = None) -> Path:
    """与 new_run.py 同口径：asset-runs/run-YYYYMMDD-hhmmss（不重名时自负+1 秒）。"""
    base = package / RUNS_DIR
    base.mkdir(parents=True, exist_ok=True)
    when = datetime.fromisoformat(at) if at else datetime.now()
    while True:
        rid = f"run-{when.strftime('%Y%m%d-%H%M%S')}"
        target = base / rid
        if not target.exists():
            return target
        when = when.fromtimestamp(when.timestamp() + 1)


def render(package: Path, run: Path, intent: str) -> str:
    tpl = TEMPLATE.read_text(encoding="utf-8")
    meta = skill_meta()
    values = {
        "package": str(package),
        "run": str(run),
        "intent": intent,
        "skill_dir": str(SKILL_DIR),
        **meta,
    }
    out = tpl
    for k, v in values.items():
        out = out.replace("{" + k + "}", v)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="跨 harness 统一入口：生成固定执行合同")
    ap.add_argument("--package", required=True, help="资产包根目录")
    ap.add_argument("--init", action="store_true", help="创建批次目录并写入 verify-task.md / state.json")
    ap.add_argument("--emit-prompt", action="store_true", help="只打印执行合同（不落盘）")
    ap.add_argument("--intent", choices=["delivery", "review"], default="delivery")
    ap.add_argument("--at", help="指定批次时间（补录/复现用，ISO 格式）")
    ap.add_argument("--json-out")
    a = ap.parse_args(argv)

    package = Path(a.package).expanduser().resolve()
    if not package.is_dir():
        print(f"资产包不存在：{package}", file=sys.stderr)
        return 1
    if not (a.init or a.emit_prompt):
        print("需要 --init（落盘创建批次）或 --emit-prompt（只打印）", file=sys.stderr)
        return 2

    run = next_run_dir(package, a.at)
    text = render(package, run, a.intent)

    if a.emit_prompt:
        print(text)
        if a.json_out:
            Path(a.json_out).write_text(json.dumps(
                {"tool": "verify_entry.py", "mode": "emit-prompt", "package": str(package),
                 "intent": a.intent, "prompt": text, **skill_meta()}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
        return 0

    run.mkdir(parents=True, exist_ok=True)
    (run / "verify-task.md").write_text(text + "\n", encoding="utf-8")
    state = {"tool": "verify_entry.py", "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
             "package": str(package), "run": run.name, "intent": a.intent,
             "contract": "verify-task.md", "stages": [], **skill_meta()}
    (run / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"批次目录：{run}")
    print(f"执行合同：{run / 'verify-task.md'}")
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(
            {"tool": "verify_entry.py", "mode": "init", "package": str(package), "run": str(run),
             "contract": str(run / "verify-task.md"), **skill_meta()}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
