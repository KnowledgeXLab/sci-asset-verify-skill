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

`--isolated` 用适配器包（scripts/harnesses/）在**全新进程**里跑合同，
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


def update_gate() -> tuple[dict | None, int]:
    """启动前检查远端版本。

    * 已是最新 / 离线读不到 → 只记录，继续；
    * 有新版且可自动更新 → 提示"可运行 update_skill.py --apply"，继续（不在核验中途改磁盘）；
    * **有新版但无法自动完成 → 直接终止**，要求用户手动更新后重试（避免用旧版跑出不可复现的结论）。
    """
    sys.path.insert(0, str(SKILL_DIR / "scripts"))
    import update_skill as us                                 # noqa: WPS433
    try:
        result = us.check()
    except Exception as exc:                                  # noqa: BLE001
        print(f"[版本检查] 跳过（检查失败：{exc}）")
        return None, 0
    status = result.get("status")
    if status == "update_available_manual":
        print(f"[版本检查] ⚠ {result.get('reason')}", file=sys.stderr)
        print(f"[版本检查] 请先手动更新到 {result.get('latest')} 后重试；"
              f"确认要用当前版本继续可加 --ignore-update。", file=sys.stderr)
        return result, 3
    if status == "behind":
        print(f"[版本检查] 有新版本 {result.get('latest')}（当前 {result.get('current')}）："
              f"可运行 scripts/update_skill.py --apply 更新（本次仍按当前版本核验）")
    elif status == "up_to_date":
        print(f"[版本检查] 已是最新（{result.get('current')}）")
    else:
        print(f"[版本检查] {result.get('reason') or status}")
    return result, 0


def run_preflight(package: Path, json_out: str | None = None) -> int:
    """只跑预审。退出码与 preflight.py 一致：0 通过 / 1 包问题 / 2 环境问题。"""
    sys.path.insert(0, str(SKILL_DIR / "scripts"))
    import preflight as pf                                   # noqa: WPS433
    report = pf.run(package)
    pf.print_report(report)
    if json_out:
        out = Path(json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"tool": "preflight.py", **report}, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    if report["ok"]:
        return 0
    return 2 if report["env_blocked"] else 1


def preflight_gate(package: Path) -> tuple[dict | None, int]:
    """预审门禁：不通过就打印问题并返回退出码，调用方据此终止。"""
    sys.path.insert(0, str(SKILL_DIR / "scripts"))
    import preflight as pf                                   # noqa: WPS433
    report = pf.run(package)
    if report["ok"]:
        print(f"[预审] 通过（提示 {len(report['warnings'])} 条），继续核验")
        return report, 0
    print(f"[预审] 未通过：阻断 {len(report['blockers'])} 条，已终止（未启动 agent、未消耗核验时间）",
          file=sys.stderr)
    pf.print_report(report, stream=sys.stderr)
    print("修复上述问题后重试；如确认是预审误报，可加 --skip-preflight 强制继续。", file=sys.stderr)
    return report, (2 if report["env_blocked"] else 1)


def _load_runner():
    """复用 run_skill_agent.py 的隔离 DSH_HOME / 清理逻辑；harness 由适配器包提供。"""
    sys.path.insert(0, str(SKILL_DIR / "scripts"))
    import run_skill_agent as rsa  # noqa: WPS433
    from harnesses import get as get_adapter, describe_all  # noqa: WPS433
    rsa.get_adapter = get_adapter
    rsa.describe_all = describe_all
    return rsa


def isolated_exec(package: Path, run: Path, contract: Path, harness_name: str,
                  timeout: int, dsh_home: str, dry_run: bool) -> int:
    """在全新进程里按合同执行核验；主进程只保留摘要与日志路径。"""
    rsa = _load_runner()
    from harnesses import REGISTRY, ORDER, available  # noqa: WPS433
    name = harness_name if harness_name != "auto" else (available()[0].name if available() else ORDER[0])
    adapter = REGISTRY.get(name)
    if adapter is None:
        print(f"未知 harness：{name}；可用：{', '.join(ORDER)}", file=sys.stderr)
        return 2
    if not adapter.headless:
        print(f"harness {name} 不支持无头调用：{adapter.not_headless_reason}", file=sys.stderr)
        return 2

    prompt = (
        f"严格按执行合同 {contract} 完成资产包核验。合同里列出的命令必须真实执行，"
        f"事实一律落盘到 {run} 下，不要引用任何历史会话或既有批次的判断。"
        f"完成后只回报：P0/P1 数量、覆盖表摘要、未执行阶段及原因、报告路径。"
    )
    stub = os.environ.get("VERIFY_ENTRY_STUB_CMD")     # 仅供自测注入；生产不使用
    exe = ""
    if not stub:
        exe = adapter.resolve_bin()
        if not exe:
            print(f"harness {name} 未安装（找过 {list(adapter.bins)}）；可用 --harness 指定已装的",
                  file=sys.stderr)
            return 2
        argv = adapter.build(prompt, {"bin": exe, "skill_dir": str(SKILL_DIR), "package": str(package),
                                      "run": str(run), "contract": str(contract)})
        argv = adapter.wrap(argv)          # 登录 shell：让宿主默认配置（端点/代理/PATH）生效
    else:
        argv = shlex.split(stub)

    outdir = run / "isolated-agent"
    outdir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    # subprocess(cwd=...) 只改实际工作目录，不改 PWD 环境变量；opencode 等工具会信任 PWD，
    # 于是把父进程所在目录（本次是 skill 仓库）当成 project 去做 copy/index，表现为卡死。
    env["PWD"] = str(run)
    env.pop("OLDPWD", None)
    # 默认**不注入任何凭据/端点变量**：一律用宿主机的默认配置（登录 shell 已带上宿主环境）。
    # 唯一例外是 Sage：它由 sage_probe / run_skill_agent 的 sage 流程显式注入 SAGE_*。
    injected: list[str] = []
    resolved: list[str] = []
    harness_env_sources: dict = {}
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

    proc = subprocess.run(argv, cwd=str(run), env=env, capture_output=True, text=True,
                          timeout=timeout, stdin=subprocess.DEVNULL)
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

    # 常见环境问题的明确提示（只在真的失败时给，避免 agent 正常输出里的普通字样触发）
    err = (proc.stderr or "") if (proc.returncode != 0 or not (run / "validation-report.md").is_file()) else ""
    hints = []
    if any(k in err for k in ("Authentication required", "agent login", "CURSOR_API_KEY")):
        hints.append("harness 未鉴权：先执行 `agent login`，或 export CURSOR_API_KEY 后重试")
    if any(k in err for k in ("Missing environment variable", "unauthorized", "Unauthorized", "401")):
        hints.append("harness 缺少凭证：按该 harness 自身配置文件/登录态准备（skill 不注入环境变量）")
    if "requires approval" in err or "permission" in err.lower() and "denied" in err.lower():
        hints.append("harness 权限不足：检查 argv 里的 --force / bypassPermissions / --auto 是否生效")
    if "unable to verify the first certificate" in err or "CERTIFICATE_VERIFY_FAILED" in err:
        hints.append("TLS 证书校验失败：内网自签网关需 NODE_EXTRA_CA_CERTS 或用户既有的 NODE_TLS_REJECT_UNAUTHORIZED")
    if "stream error" in err or "UnknownError" in err:
        hints.append("模型侧报错（provider/model 不可用）：换 --harness 或检查该 harness 的模型配置")

    print(f"[isolated] harness={name} rc={proc.returncode}")
    if summary["verdict"]:
        vd = summary["verdict"]
        print(f"[isolated] 判定：P0 {vd.get('P0')} / P1 {vd.get('P1')}；覆盖 {vd.get('coverage')}")
    else:
        print("[isolated] 未生成 probe-report.json —— 请在日志里确认为何没跑到判定阶段")
    print(f"[isolated] 报告：{summary['report'] or '未生成'}")
    print(f"[isolated] 日志：{outdir}（stdout/stderr/cmd/summary）")
    for h in hints:
        print(f"[isolated] 提示：{h}")
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
    ap.add_argument("--ignore-update", action="store_true",
                    help="跳过启动前的远端版本检查/提示（不建议：新版本可能已修掉你要跑的问题）")
    ap.add_argument("--preflight", action="store_true",
                    help="只做环境与包格式预审（不建批次、不跑核验）")
    ap.add_argument("--skip-preflight", action="store_true",
                    help="跳过预审（仅在明确知道预审误报时使用）")
    ap.add_argument("--json-out")
    a = ap.parse_args(argv)

    package = Path(a.package).expanduser().resolve()
    if not package.is_dir():
        print(f"资产包不存在：{package}", file=sys.stderr)
        return 1
    if a.preflight:
        return run_preflight(package, a.json_out)

    if not (a.init or a.emit_prompt or a.isolated):
        print("需要 --init / --emit-prompt / --isolated 之一", file=sys.stderr)
        return 2

    # 版本检查：先问远端有没有更新版本（不依赖是否 git 安装）
    update_info = None
    if not a.ignore_update and not a.emit_prompt:
        update_info, rc = update_gate()
        if rc:
            return rc

    # 预审：环境 + 资产包格式。任何阻断都在起 agent 之前终止，避免白跑完整流程。
    preflight_report = None
    if not a.skip_preflight and not a.emit_prompt:
        preflight_report, rc = preflight_gate(package)
        if rc:
            return rc

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
    if update_info is not None:
        (run / "skill-update.json").write_text(
            json.dumps({"tool": "update_skill.py", **update_info}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
    if preflight_report is not None:
        (run / "preflight-report.json").write_text(
            json.dumps({"tool": "preflight.py", **preflight_report}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
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
