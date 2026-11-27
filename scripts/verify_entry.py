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


def render(package: Path, run: Path, intent: str, copy: str = "", mode: str = "copy") -> str:
    tpl = TEMPLATE.read_text(encoding="utf-8")
    meta = skill_meta()
    values = {
        "package": str(package),
        "run": str(run),
        "run_name": run.name,
        "intent": intent,
        "skill_dir": str(SKILL_DIR),
        "copy": copy or str(package),
        "exec_root": copy or str(package),
        "exec_mode": ("copy（副本内执行，原包只写 asset-runs/）" if copy else "inplace（原地执行）"),
        "work_root": str((Path(copy).parent if copy else package.parent)),
        **meta,
    }
    out = tpl
    for k, v in values.items():
        out = out.replace("{" + k + "}", v)
    return out


STAGES = ("version_check", "static_review_origin", "prepare_copy", "inventory_usage",
          "cases", "harbor", "sage", "deployment",
          "verdict_rerun", "report_gates", "registration", "prune_stamp", "cleanup")


def _run_script(name: str, args: list[str], timeout: int = 600) -> tuple[int, str]:
    proc = subprocess.run([sys.executable, str(SKILL_DIR / "scripts" / name), *args],
                          capture_output=True, text=True, timeout=timeout)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def stage1_prepare(package: Path, run: Path, do_copy: bool = True) -> dict:
    """阶段一收尾：建副本 → 外部资产校验/挂载 → 副本二次静态审查 → 盘点台账。

    返回 {copy, mount, review, inventory}；任何一步失败都在这里暴露，
    由调用方决定是终止（副本/挂载失败不可回退）还是继续（台账失败只提示）。
    """
    info: dict = {"copy": "", "errors": [], "notes": []}

    if do_copy:
        rc, out = _run_script("work_copy.py", ["init", str(package), "--run-id", run.name, "--manifest"])
        state_f = run / "work-copy.json"
        if rc != 0 or not state_f.is_file():
            info["errors"].append(f"建副本失败（rc={rc}）：{out.strip()[-300:]}")
            return info
        try:
            copy = json.loads(state_f.read_text(encoding="utf-8")).get("work_copy") or ""
        except Exception:                                     # noqa: BLE001
            copy = ""
        info["copy"] = copy
        info["notes"].append(f"副本：{copy or '(未记录)'}")
    else:
        info["notes"].append("按 --no-copy 原地执行，未建副本")

    exec_root = Path(info["copy"]) if info["copy"] else package

    # 外部资产：先校验引用，再按声明挂载（挂到副本里，不动原包）
    rc, out = _run_script("external_refs.py", ["--package", str(exec_root), "--check",
                                               "--json-out", str(run / "external-refs.json")])
    info["external_refs_rc"] = rc
    if rc == 1:
        info["errors"].append(f"external_assets.json 校验未通过：{out.strip()[-300:]}")
    if info["copy"]:
        rc, out = _run_script("mount_external.py", ["--copy-root", str(exec_root),
                                                    "--origin-package", str(package),
                                                    "--json-out", str(run / "external-mounts.json")])
        info["mount_rc"] = rc
        if rc != 0:
            info["errors"].append(f"外部资产挂载失败（rc={rc}）：{out.strip()[-300:]}")
        else:
            info["notes"].append("外部资产已按 external_assets.json 挂载到副本")

    # 副本可信性检查（替代"重跑一遍预审"）：只查复制/挂载才会暴露的三类问题
    #   ① 文件集是否忠实于原包 ② 软链是否越出副本（隔离失效）③ required 外部资产是否挂上
    if info["copy"]:
        rc, out = _run_script("check_copy.py", ["--source", str(package), "--copy", str(exec_root),
                                                "--mounts", str(run / "external-mounts.json"),
                                                "--json-out", str(run / "copy-check.json")])
        info["copy_check_rc"] = rc
        if rc != 0:
            info["errors"].append("副本可信性检查未通过：" + out.strip()[-400:])
        else:
            info["notes"].append("副本可信性：文件集忠实、软链未越界、外部资产已挂载")

    # 副本静态事实（供盘点台账与报告引用；不作为门禁——合规性已由首次预审判过）
    rc, out = _run_script("probe_package.py", ["--package", str(exec_root), "--evaluate",
                                               "--static-only", "--json-out", str(run / "probe-static.json")])
    info["static_review_rc"] = rc
    if rc != 0:
        info["notes"].append(f"副本静态审查未能执行（rc={rc}）：{out.strip()[-200:]}")
    else:
        try:
            v = json.loads((run / "probe-static.json").read_text(encoding="utf-8")).get("verdict") or {}
            info["static_summary"] = v.get("summary")
            p0 = [f.get("check_id") for f in (v.get("findings") or []) if str(f.get("level")) == "P0"]
            if p0:
                info["static_p0"] = p0
                info["notes"].append("副本静态预判 P0：" + "、".join(p0) + "（记录，须在报告中体现）")
        except Exception:                                     # noqa: BLE001
            pass

    # 盘点台账（check_coverage 的 --inventory 依赖它）
    rc, out = _run_script("build_inventory.py", ["--probe-report", str(run / "probe-static.json"),
                                                 "--package", str(exec_root),
                                                 "--out", str(run / "inventory.json")])
    info["inventory_rc"] = rc
    if rc != 0:
        info["notes"].append(f"盘点台账生成失败（rc={rc}）：{out.strip()[-200:]}")
    else:
        info["notes"].append(out.strip().splitlines()[-1] if out.strip() else "盘点台账已生成")
    return info


def write_state(run: Path, package: Path, intent: str, stage1: dict | None) -> dict:
    """state.json：分阶段状态机（pending / running / completed / failed / blocked / not_applicable）。"""
    state = {"tool": "verify_entry.py",
             "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
             "package": str(package), "run": run.name, "intent": intent,
             "contract": "verify-task.md",
             "exec_mode": ("copy" if (stage1 or {}).get("copy") else "inplace"),
             "work_copy": (stage1 or {}).get("copy") or "",
             "stages": {name: {"status": "pending"} for name in STAGES},
             **skill_meta()}
    if stage1:
        state["stages"]["version_check"]["status"] = "completed"
        state["stages"]["static_review_origin"]["status"] = "completed"
        # prepare_copy 一个阶段、三个子步骤：建副本 → 外部资产挂载 → 副本可信性检查
        substeps = {
            "work_copy": {"status": ("completed" if stage1.get("copy") else "not_applicable"),
                          "path": stage1.get("copy") or ""},
            "external_mount": {"status": ("completed" if stage1.get("mount_rc") == 0
                                          else ("not_applicable" if not stage1.get("copy") else "failed"))},
            "copy_check": {"status": ("completed" if stage1.get("copy_check_rc") == 0
                                      else ("not_applicable" if not stage1.get("copy") else "failed"))},
        }
        failed_sub = [k for k, v in substeps.items() if v["status"] == "failed"]
        state["stages"]["prepare_copy"] = {
            "status": ("failed" if failed_sub else
                       ("completed" if stage1.get("copy") else "not_applicable")),
            "substeps": substeps,
            "path": stage1.get("copy") or "",
            "summary": stage1.get("static_summary")}
        state["stages"]["inventory_usage"] = {"status": ("completed" if stage1.get("inventory_rc") == 0
                                                         else "failed")}
    return state


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
        print(f"[版本检查] ⚠ 有新版本 {result.get('latest')}（当前 {result.get('current')}），但无法自动更新",
              file=sys.stderr)
        print(f"[版本检查] 原因：{result.get('reason')}", file=sys.stderr)
        for how in result.get("howto") or []:
            print(f"[版本检查] 处理：{how}", file=sys.stderr)
        print("[版本检查] 更新后重试；确认要用当前版本继续可加 --ignore-update。", file=sys.stderr)
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
    sage_block = any(b.get("code") == "PREFLIGHT-SAGE" for b in report["blockers"])
    if sage_block:
        print("→ 这是**环境前置**（Sage 配置）而非资产缺陷：按上面的清单提供 SAGE_BASE_URL / SAGE_API_KEY 后，"
              "重跑本命令即可继续核验。", file=sys.stderr)
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
    ap.add_argument("--no-copy", action="store_true",
                    help="不建副本，直接在原包里执行（默认建副本：原包只写 asset-runs/）")
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
    stage1 = stage1_prepare(package, run, do_copy=not a.no_copy) if not a.emit_prompt else None
    if stage1 and stage1.get("errors"):
        print("[阶段一] 未通过，已终止（未启动 agent）：", file=sys.stderr)
        for e in stage1["errors"]:
            print(f"  - {e}", file=sys.stderr)
        (run / "state.json").write_text(json.dumps(write_state(run, package, a.intent, stage1),
                                                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("修复后重试；确认误报可加 --no-copy 或 --skip-preflight。", file=sys.stderr)
        return 1
    if stage1:
        for n in stage1.get("notes") or []:
            print(f"[阶段一] {n}")
    text = render(package, run, a.intent, copy=stage1.get("copy", "") if stage1 else "")
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
    (run / "state.json").write_text(
        json.dumps(write_state(run, package, a.intent, stage1), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
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
