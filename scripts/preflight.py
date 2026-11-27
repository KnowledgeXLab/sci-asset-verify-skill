#!/usr/bin/env python3
"""预审：在启动 agent、跑完整核验之前，用程序快速检查"环境"和"资产包格式规范"。

目的
----
完整核验要起独立 agent、跑 7+ 个脚本、烧几分钟到几十分钟。若环境缺件或包结构不合规，
这些开销全是白费。预审把这类**确定性错误**提前挡住，直接终止并给出可执行的问题清单。

检查内容
--------
环境（env）
  * Python 版本、skill 自身完整性（VERSION / scripts / tests）
  * 是否有**可用的 harness**（适配器 auto 候选非空）
  * docker / harbor（缺失只 warning：harbor 用例会 blocked，不影响其余判定）
  * Sage 配置（只 warning：是否真需要由资产包 sage-need 决定）
资产包（package）
  * 包根结构：不允许包根 `asset.json`（属 P0-STRUCT）
  * 一级子目录必须是资产目录（含 asset.json），否则 blocker
  * 每个 asset.json：JSON 可解析、必需字段齐全、asset_type 合法、asset_id 四段式（项目编号/命名空间/类型/名称）
  * asset_id 重复、目录名与 name 不一致
  * 包络校验（调用 validate_envelope.py 的快速模式）
  * validate/ 与 cases.json 缺失 → warning（可能本来就没有用例）

退出码：0 预审通过；1 资产包有阻断问题；2 环境阻断；3 参数错误。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
ASSET_TYPES = {"data", "tool", "model", "environment", "skill", "task", "trajectory", "benchmark"}
REQUIRED_FIELDS = ("schema_version", "asset_id", "asset_type", "name", "description",
                   "asset_version", "domains", "task_classes", "provenance", "governance", "spec")
ID_RE = re.compile(r"^sciasset://[^/]+/[^/]+/(data|tool|model|environment|skill|task|trajectory|benchmark)/[^/]+$")


def _add(store: list, level: str, code: str, msg: str, where: str = "") -> None:
    store.append({"level": level, "code": code, "message": msg, "where": where})


def check_env(pkg: Path | None = None) -> dict:
    blockers, warnings = [], []
    if sys.version_info < (3, 9):
        _add(blockers, "error", "PREFLIGHT-PY", f"Python 版本过低：{sys.version.split()[0]}（需 ≥3.9）")

    for rel in ("VERSION", "scripts/probe_package.py", "scripts/check_report.py"):
        if not (SKILL_DIR / rel).is_file():
            _add(blockers, "error", "PREFLIGHT-SKILL", f"skill 缺少文件：{rel}")

    sys.path.insert(0, str(SKILL_DIR / "scripts"))
    try:
        from harnesses import available, ORDER, REGISTRY      # noqa: WPS433
        auto = [a.name for a in available()]
    except Exception as exc:                                   # noqa: BLE001
        auto = []
        _add(blockers, "error", "PREFLIGHT-HARNESS", f"适配器加载失败：{exc}")
    if not auto:
        _add(blockers, "error", "PREFLIGHT-HARNESS",
             "没有可用的 harness：请安装 dsh/codex/claude/opencode/kimi/qwen 之一并完成其自身配置")

    if not shutil.which("docker"):
        _add(warnings, "warn", "PREFLIGHT-DOCKER", "未找到 docker：harbor 用例会记 blocked（其余判定不受影响）")
    if not (Path.home() / ".harbor").exists() and not shutil.which("harbor"):
        _add(warnings, "warn", "PREFLIGHT-HARBOR", "未检测到 harbor CLI：task 类资产只能跑到用例层")

    sage = {"configured": False}
    probe = SKILL_DIR / "scripts" / "sage_probe.py"
    if probe.is_file():
        try:
            p = subprocess.run([sys.executable, str(probe), "config", "--probe"], capture_output=True,
                               text=True, timeout=25)
            ok = p.returncode == 0
            sage = {"configured": ok, "rc": p.returncode, "detail": (p.stdout or p.stderr or "").strip()[-200:]}
            # 该包到底需不需要 Sage：detect 是纯静态扫描，预审阶段就能判
            needed, why = False, ''
            if pkg is not None and pkg.is_dir():
                try:
                    d = subprocess.run([sys.executable, str(probe.parent / "sage_probe.py"), "detect",
                                        str(pkg)], capture_output=True, text=True, timeout=60)
                    need = json.loads(d.stdout or "{}") if d.stdout.strip().startswith("{") else {}
                    needed = bool(need.get("needed"))
                    why = "、".join((need.get("reasons") or [])[:2])
                except Exception:                             # noqa: BLE001
                    needed = False
            sage = {"configured": ok, "needed": needed, "reason": why,
                    "rc": p.returncode, "detail": (p.stdout or p.stderr or "").strip()[-200:]}
            if needed and not ok:
                # 需要 Sage 却没配置：**暂停核验**并请用户提供，提供后重跑即可继续
                ask = ""
                try:
                    sys.path.insert(0, str(SKILL_DIR / "scripts"))
                    import sage_probe as _sp                      # noqa: WPS433
                    ask = "\n" + _sp.user_request_block(["base_url", "api_key"])
                except Exception:                                 # noqa: BLE001
                    ask = "\n请在环境变量里设置 SAGE_BASE_URL 与 SAGE_API_KEY（模型名固定 Sage-A1.5）"
                _add(blockers, "error", "PREFLIGHT-SAGE",
                     "本包**需要** Sage A1.5 附加测试（" + (why or "涉及大模型调用") + "），"
                     "但当前环境未配置 Sage —— 核验已暂停，请提供配置后重跑：" + ask)
            elif not ok:
                _add(warnings, "warn", "PREFLIGHT-SAGE",
                     "Sage-A1.5 未配置；本包检测为**不需要** Sage（若判断有误，动态阶段会按 P0-SAGE 处理）")
        except Exception as exc:                               # noqa: BLE001
            sage = {"configured": False, "error": str(exc)}
            _add(warnings, "warn", "PREFLIGHT-SAGE", f"Sage 配置探测失败：{exc}")
    return {"blockers": blockers, "warnings": warnings, "auto_harnesses": auto, "sage": sage,
            "python": sys.version.split()[0]}


def _load_asset(path: Path) -> tuple[dict | None, str]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), ""
    except Exception as exc:                                   # noqa: BLE001
        return None, str(exc)


def check_package(pkg: Path) -> dict:
    blockers, warnings = [], []
    if not pkg.is_dir():
        _add(blockers, "error", "PREFLIGHT-PKG", f"资产包目录不存在：{pkg}")
        return {"blockers": blockers, "warnings": warnings, "assets": []}

    if (pkg / "asset.json").exists():
        _add(blockers, "error", "P0-STRUCT",
             "包根不应有 asset.json：资产包根只放资产目录，包级元数据不进包根", str(pkg))

    assets, seen_ids = [], {}
    product_dirs = {"asset-runs", "out", "outputs", "logs", "tmp", "artifacts", "runs",
                    "experiments", "cache", "__pycache__", "node_modules"}
    subdirs = [d for d in sorted(pkg.iterdir())
               if d.is_dir() and not d.name.startswith(".") and d.name not in product_dirs]
    for d in subdirs:
        aj = d / "asset.json"
        if not aj.is_file():
            _add(blockers, "error", "PREFLIGHT-STRUCT",
                 f"目录 {d.name} 下没有 asset.json：不是资产目录，或资产缺少必需声明", str(d))
            continue
        data, err = _load_asset(aj)
        if data is None:
            _add(blockers, "error", "PREFLIGHT-JSON", f"{d.name}/asset.json 不是合法 JSON：{err}", str(aj))
            continue
        missing = [k for k in REQUIRED_FIELDS if k not in data or data[k] in (None, "", [], {})]
        if missing:
            _add(blockers, "error", "P0-ENVELOPE",
                 f"{d.name}/asset.json 缺必需字段：{', '.join(missing)}", str(aj))
        atype = str(data.get("asset_type") or "")
        if atype and atype not in ASSET_TYPES:
            _add(blockers, "error", "P0-ENVELOPE",
                 f"{d.name}: asset_type={atype!r} 不在八类规范内（{', '.join(sorted(ASSET_TYPES))}）", str(aj))
        aid = str(data.get("asset_id") or "")
        if aid and not ID_RE.match(aid):
            _add(blockers, "error", "P0-ENVELOPE",
                 f"{d.name}: asset_id 不符合 sciasset://<项目编号>/<命名空间>/<类型>/<名称>：{aid}"
                 "（第一段是项目编号，见项目任务书首页；随后是自拟命名空间、资产类型、资产名称）", str(aj))
        if aid:
            if aid in seen_ids:
                _add(blockers, "error", "P0-ENVELOPE",
                     f"asset_id 重复：{aid}（{seen_ids[aid]} 与 {d.name}）", str(aj))
            else:
                seen_ids[aid] = d.name
        for sub in ("validate",):
            if not (d / sub).is_dir():
                _add(warnings, "warn", "PREFLIGHT-CASES",
                     f"{d.name}: 没有 {sub}/ 目录（无给定用例的资产，验证深度只能到声明层）", str(d))
        cases = d / "validate" / "cases.json"
        if cases.is_file():
            cdata, cerr = _load_asset(cases)
            if cdata is None:
                _add(blockers, "error", "PREFLIGHT-JSON", f"{d.name}/validate/cases.json 不可解析：{cerr}", str(cases))
        assets.append({"dir": d.name, "asset_id": aid, "asset_type": atype,
                       "name": data.get("name"), "version": data.get("asset_version")})
    if not subdirs:
        _add(blockers, "error", "P0-STRUCT", "资产包里没有任何资产目录", str(pkg))

    # probe 静态判定（跳过需 run 产物的三项）：把 P0 级 findings 直接转成阻断项
    probe = SKILL_DIR / "scripts" / "probe_package.py"
    if probe.is_file() and subdirs:
        try:
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                out = Path(td) / "preflight-probe.json"
                p = subprocess.run([sys.executable, str(probe), "--package", str(pkg), "--evaluate",
                                    "--static-only", "--json-out", str(out)],
                                   capture_output=True, text=True, timeout=180)
                doc = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {}
                verdict = doc.get("verdict") or {}
                # 只有「包不可读/不可解析」类才阻断；其余静态 P0 是**判定结论**，留给报告，
                # 预审阶段强行拦掉会让交付方拿不到一份写清问题的报告。
                BLOCKING = {"P0-STRUCT", "P0-ENVELOPE"}
                for f in verdict.get("findings") or []:
                    cid = str(f.get("check_id") or "P0-UNKNOWN")
                    owners = ", ".join(f.get("assets") or []) or "包级"
                    detail = json.dumps(f.get("detail") or {}, ensure_ascii=False)[:180]
                    if cid in BLOCKING:
                        _add(blockers, "error", cid, f"{f.get('name') or ''}：{detail}", owners)
                    else:
                        _add(warnings, "warn", f"PREFLIGHT-{cid}",
                             f"静态预判 {cid}（{owners}）：{detail} —— 不阻断，需在报告问题清单中体现")
                cov = verdict.get("coverage_summary") or {}
                if cov:
                    report_stats = {"pass": cov.get("pass"), "fail": cov.get("fail"),
                                    "na": cov.get("na"), "deferred": cov.get("deferred")}
                    if True:
                        _add(warnings, "info", "PREFLIGHT-PROBE",
                             f"静态判定覆盖：pass {cov.get('pass')} / fail {cov.get('fail')} / "
                             f"na {cov.get('na')} / deferred {cov.get('deferred')}（运行期三项）")
        except Exception as exc:                               # noqa: BLE001
            _add(warnings, "warn", "PREFLIGHT-PROBE", f"静态判定执行失败（跳过）：{exc}")

    # external_assets.json 格式规范（只在建副本后才能挂载，但**格式**现在就该验）
    extf = SKILL_DIR / "scripts" / "external_refs.py"
    if extf.is_file() and subdirs:
        try:
            p = subprocess.run([sys.executable, str(extf), "--package", str(pkg), "--check",
                                "--json-out", str(Path(tempfile.gettempdir()) / "preflight-external-refs.json")],
                               capture_output=True, text=True, timeout=120)
            txt = (p.stdout or "") + (p.stderr or "")
            if p.returncode == 1:
                bad = [l.strip() for l in txt.splitlines() if l.strip().startswith(("!", "×"))][:6]
                _add(blockers, "error", "PREFLIGHT-EXTERNAL",
                     "external_assets.json 格式不合规：" + ("；".join(bad) or "见 external_refs.py 输出"))
            elif "问题" in txt and "0 条" not in txt.split("问题")[-1][:8]:
                _add(warnings, "warn", "PREFLIGHT-EXTERNAL", txt.strip().splitlines()[-1][:160])
        except Exception as exc:                               # noqa: BLE001
            _add(warnings, "warn", "PREFLIGHT-EXTERNAL", f"external_assets.json 校验执行失败（跳过）：{exc}")

    # 自包含（越界引用）：静态可查，越界即 P0-COMPLETE 风险
    sc = SKILL_DIR / "scripts" / "check_selfcontained.py"
    if sc.is_file() and subdirs and not any(b["code"] == "P0-COMPLETE" for b in blockers):
        try:
            p = subprocess.run([sys.executable, str(sc), "--package", str(pkg)],
                               capture_output=True, text=True, timeout=180)
            if p.returncode == 1:
                tail = [l.strip() for l in (p.stdout or "").splitlines() if l.strip()][-6:]
                _add(blockers, "error", "P0-COMPLETE",
                     "自包含校验未通过（越界引用）：" + ("；".join(tail) or "见 check_selfcontained 输出"))
        except Exception as exc:                               # noqa: BLE001
            _add(warnings, "warn", "PREFLIGHT-SELF", f"自包含校验执行失败（跳过）：{exc}")

    val = SKILL_DIR / "scripts" / "validate_envelope.py"
    if val.is_file() and subdirs and not any(b["code"] == "P0-ENVELOPE" for b in blockers):
        try:
            p = subprocess.run([sys.executable, str(val), str(pkg)], capture_output=True, text=True, timeout=120)
            if p.returncode != 0:
                tail = [l.strip() for l in (p.stdout or "").splitlines() if l.strip().startswith(("error", "[FAIL"))]
                _add(blockers, "error", "P0-ENVELOPE",
                     "包络校验未通过：" + ("；".join(tail[:5]) or (p.stderr or "")[:200]))
        except Exception as exc:                               # noqa: BLE001
            _add(warnings, "warn", "PREFLIGHT-ENVELOPE", f"包络校验执行失败（跳过）：{exc}")
    return {"blockers": blockers, "warnings": warnings, "assets": assets}


def run(pkg: Path) -> dict:
    env = check_env(pkg)
    pkgr = check_package(pkg)
    blockers = env["blockers"] + pkgr["blockers"]
    warnings = env["warnings"] + pkgr["warnings"]
    env_blocked = any(b["code"].startswith("PREFLIGHT-") and b["level"] == "error" for b in env["blockers"])
    return {"ok": not blockers, "env_blocked": env_blocked, "blockers": blockers, "warnings": warnings,
            "env": {k: v for k, v in env.items() if k not in ("blockers", "warnings")},
            "package": {"assets": pkgr["assets"]}, "package_path": str(pkg)}


def print_report(report: dict, stream=None) -> None:
    stream = stream or sys.stdout
    print(f"预审：{'通过' if report['ok'] else '未通过'}"
          f"（阻断 {len(report['blockers'])}，提示 {len(report['warnings'])}）"
          f" 资产 {len(report['package']['assets'])} 个；"
          f"harness 候选 {', '.join(report['env'].get('auto_harnesses') or []) or '无'}", file=stream)
    for b in report["blockers"]:
        print(f"  [阻断] {b['code']}：{b['message']}", file=stream)
    for w in report["warnings"]:
        print(f"  [提示] {w['code']}：{w['message']}", file=stream)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="核验前的环境与资产包格式预审")
    ap.add_argument("--package", required=True)
    ap.add_argument("--json-out")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    pkg = Path(args.package).expanduser().resolve()
    if not pkg.exists() and pkg.is_dir() is False and not pkg.parent.is_dir():
        print(f"资产包不存在：{pkg}", file=sys.stderr)
        return 3
    report = run(pkg)
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"tool": "preflight.py", **report}, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    if not args.quiet:
        print_report(report)
    if report["ok"]:
        return 0
    return 2 if report["env_blocked"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
