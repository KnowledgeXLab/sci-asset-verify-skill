#!/usr/bin/env python3
"""跨 harness 的统一入口：把"固定 prompt"变成 skill 的产物，而不是人的记忆。

任何 harness（DSH / Claude Code / Codex / Cursor / 其它）都只需要：

  python3 scripts/verify_entry.py --package <包根> --init

它会创建规范批次目录 `asset-runs/run-YYYYMMDD-hhmmss/`，渲染固定执行合同
`<run>/verify-task.md`（模板 `prompts/verify-task.md.tmpl`），并写 `state.json` 骨架。
执行者（人或 agent）**照这份合同跑**，结论由脚本门禁校验，不依赖模型自觉。

只想看要执行什么（不落盘）：

  python3 scripts/verify_entry.py --package <包根> --emit-prompt

  python3 scripts/verify_entry.py --package <包根> --isolated          # 在独立进程/新上下文里执行
  python3 scripts/verify_entry.py --package <包根> --isolated --dry-run  # 只看会启动什么命令

`--isolated` 用 harness 注册表（assets/harness-registry.json）在**全新进程**里跑合同，
主进程只收摘要——历史会话物理上进不去。凭据只经环境变量注入，不落盘；跑完会清理残留凭据。

退出码：0 成功 / 1 资产包不存在或隔离执行失败 / 2 参数错误。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
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


def _load_runner():
    """复用 run_skill_agent.py 的 harness 解析 / 隔离 DSH_HOME / 凭据注入 / 清理逻辑。"""
    sys.path.insert(0, str(SKILL_DIR / "scripts"))
    import run_skill_agent as rsa  # noqa: WPS433
    return rsa


def isolated_exec(package: Path, run: Path, contract: Path, harness_name: str,
                  timeout: int, dsh_home: str, dry_run: bool) -> int:
    """在全新进程里按合同执行核验；主进程只保留摘要与日志路径。"""
    rsa = _load_runner()
    reg = rsa.load_registry()
    harnesses = reg.get("harnesses") or {}
    name = harness_name if harness_name != "auto" else (reg.get("preference") or ["dsh"])[0]
    if name not in harnesses:
        print(f"未知 harness：{name}；可用：{', '.join(sorted(harnesses))}", file=sys.stderr)
        return 2
    preset = harnesses[name]

    prompt = (
        f"严格按执行合同 {contract} 完成资产包核验。合同里列出的命令必须真实执行，"
        f"事实一律落盘到 {run} 下，不要引用任何历史会话或既有批次的判断。"
        f"完成后只回报：P0/P1 数量、覆盖表摘要、未执行阶段及原因、报告路径。"
    )
    stub = os.environ.get("VERIFY_ENTRY_STUB_CMD")     # 仅供自测注入；生产不使用
    exe = ""
    if not stub:
        import shutil
        for b in preset.get("bins") or []:
            exe = shutil.which(b) or ""
            if exe:
                break
        if not exe:
            print(f"harness {name} 未安装（找过 {preset.get('bins')}）；可用 --harness 指定已装的",
                  file=sys.stderr)
            return 2
        argv = rsa.build_argv({"name": name, "preset": preset, "bin": exe}, prompt, "",
                              {"skill_dir": str(SKILL_DIR), "package": str(package),
                               "run": str(run), "contract": str(contract)})
    else:
        argv = shlex.split(stub)

    outdir = run / "isolated-agent"
    outdir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    creds = rsa.credential_env(Path.home() / ".dsh")
    env.update(creds)                                          # 凭据只进环境变量，不落盘
    harness_env, harness_env_sources = rsa.resolve_harness_env(preset.get("env_map"))
    env.update(harness_env)                                    # harness 需要的变量按注册表解析
    injected = sorted(creds)
    resolved = sorted(harness_env_sources)
    dsh_note = ""
    if name == "dsh" and not stub:
        extra, dsh_note = rsa.dsh_home_runtime(outdir, "verify", dsh_home)
        env.update(extra)

    (outdir / "cmd.json").write_text(json.dumps(
        {"tool": "verify_entry.py", "mode": "isolated", "harness": name, "argv": argv,
         "cwd": str(run), "contract": str(contract), "injected_env_keys": injected,
         "harness_env_keys": resolved,
         "harness_env_sources": harness_env_sources,
         "dsh_home": dsh_note, "timeout_sec": timeout}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")

    if dry_run:
        print("将执行：", " ".join(shlex.quote(a) for a in argv))
        if dsh_note:
            print("环境：", dsh_note)
        print(f"日志目录：{outdir}")
        return 0

    proc = subprocess.run(argv, cwd=str(run), env=env, capture_output=True, text=True, timeout=timeout)
    (outdir / "stdout.txt").write_text(proc.stdout or "", encoding="utf-8")
    (outdir / "stderr.txt").write_text(proc.stderr or "", encoding="utf-8")
    scrubbed = rsa.scrub_secrets(outdir)

    summary = {"rc": proc.returncode, "harness": name, "logs": str(outdir),
               "secrets_scrubbed": scrubbed, "report": None, "verdict": None}
    probe = run / "probe-report.json"
    if probe.is_file():
        try:
            v = (json.loads(probe.read_text(encoding="utf-8")).get("verdict") or {})
            summary["verdict"] = {"P0": (v.get("summary") or {}).get("P0"),
                                  "P1": (v.get("summary") or {}).get("P1"),
                                  "coverage": v.get("coverage_summary")}
        except Exception:  # noqa: BLE001
            pass
    rp = run / "validation-report.md"
    if rp.is_file():
        summary["report"] = str(rp)
    (outdir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                                         encoding="utf-8")

    print(f"[isolated] harness={name} rc={proc.returncode}")
    if summary["verdict"]:
        vd = summary["verdict"]
        print(f"[isolated] 判定：P0 {vd.get('P0')} / P1 {vd.get('P1')}；覆盖 {vd.get('coverage')}")
    else:
        print("[isolated] 未生成 probe-report.json —— 请在日志里确认为何没跑到判定阶段")
    print(f"[isolated] 报告：{summary['report'] or '未生成'}")
    print(f"[isolated] 日志：{outdir}（stdout/stderr/cmd/summary）")
    if scrubbed:
        print(f"[isolated] 已清理残留凭据 {len(scrubbed)} 项")
    return 0 if proc.returncode == 0 and summary["report"] else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="跨 harness 统一入口：生成固定执行合同")
    ap.add_argument("--package", required=True, help="资产包根目录")
    ap.add_argument("--init", action="store_true", help="创建批次目录并写入 verify-task.md / state.json")
    ap.add_argument("--emit-prompt", action="store_true", help="只打印执行合同（不落盘）")
    ap.add_argument("--intent", choices=["delivery", "review"], default="delivery")
    ap.add_argument("--at", help="指定批次时间（补录/复现用，ISO 格式）")
    ap.add_argument("--isolated", action="store_true", help="在独立进程/新上下文里执行合同")
    ap.add_argument("--harness", default="auto", help="isolated 用哪个 harness（默认取注册表首选）")
    ap.add_argument("--dsh-home", default="auto", choices=["auto", "default", "isolated"])
    ap.add_argument("--timeout", type=int, default=3600, help="isolated 执行超时（秒）")
    ap.add_argument("--dry-run", action="store_true", help="isolated 时只打印将执行的命令")
    ap.add_argument("--json-out")
    a = ap.parse_args(argv)

    package = Path(a.package).expanduser().resolve()
    if not package.is_dir():
        print(f"资产包不存在：{package}", file=sys.stderr)
        return 1
    if not (a.init or a.emit_prompt or a.isolated):
        print("需要 --init / --emit-prompt / --isolated 之一", file=sys.stderr)
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
    if a.isolated:
        return isolated_exec(package, run, run / "verify-task.md", a.harness,
                             a.timeout, a.dsh_home, a.dry_run)
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
