#!/usr/bin/env python3
"""skill 自测：对七个脚本做正例 / 负例断言，不依赖任何真实资产包。

用法：
  python tests/selftest.py            # 跑全部
  python tests/selftest.py -v         # 保留临时目录并打印每个脚本输出

设计原则
--------
* 只用标准库 + skill 自带的 vendor/openpyxl，**不联网、不依赖真实交付包**
* 每个检查都断言"该被拦住的必须被拦住"，而不是只看退出码是 0
* 失败时打印该步骤的 stdout/stderr，便于定位

退出码：0 全部通过；1 有失败。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))

RESULTS: list[tuple[str, bool, str]] = []
VERBOSE = "-v" in sys.argv


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))


def run(args: list[str], cwd: Path, env_extra: dict | None = None, timeout: int = 180):
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run([sys.executable, *args], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env, timeout=timeout)
    if VERBOSE:
        print(f"    $ {' '.join(Path(a).name if a.endswith('.py') else a for a in args)}")
        print(f"      rc={proc.returncode} out={proc.stdout.strip()[:300]}")
    return proc


# --------------------------------------------------------------------------- fixtures
def minimal_envelope() -> dict:
    return {
        "schema_version": "1.0.0",
        "asset_id": "sciasset://2026ZD123456/earth/data/sample",
        "asset_type": "data",
        "name": "示例数据资产",
        "description": "自测用的最小合规数据资产。",
        "asset_version": "1.0.0",
        "lifecycle": "draft",
        "domains": [{"id": "domain:earth-science"}],
        "task_classes": [{"id": "operation:data-processing"}],
        "provenance": {
            "created_time": "2026-01-01T00:00:00+08:00",
            "provider": [{"name": "示例单位", "role": "provider"}],
            "source": {"kind": "original"},
        },
        "governance": {"license": "LicenseRef-Example", "access": "public", "risk_level": "low",
                       "privacy": "none", "usage_constraints": []},
        "spec": {"metadata": {"format": ["csv"], "modalities": ["CSV"]},
                 "artifacts": [{"uri": "data/sample/part.csv", "media_type": "text/csv"}],
                 "domain_attributes": {}, "asset_protocol": {"name": "example"}},
    }


def valid_rows() -> dict:
    return {"rows": [{
        "资产ID": "2026ZD123456/earth/data/sample",
        "资产名称": "示例数据资产", "资产类型": "data", "领域Profile": "地球科学",
        "资产摘要": "自测用的最小合规数据资产。", "任务|操作标签": "operation:data-processing",
        "科学对象": "样点", "资产提供单位": "示例单位",
        "来源|产出方式": "original", "版本": "1.0.0", "输入说明": "文件", "输出说明": "文件",
        "运行环境|资源要求": "CPU", "敏感级别": "内部",
    }], "cases": [{"用例编号": "c1", "资产名称": "示例数据资产", "资产版本": "1.0.0",
                   "执行状态": "completed", "验证结论": "pass"}]}


# --------------------------------------------------------------------------- T1 包络校验
def t1_envelope(tmp: Path) -> None:
    print("\n[1/19] validate_envelope.py — 包络只校验不构造")
    case = tmp / "t1"
    case.mkdir()

    def make_artifacts(d: Path, doc: dict) -> None:
        """本地实体必须真实存在，否则新增的路径校验会判缺失。"""
        for a in (doc.get("spec") or {}).get("artifacts") or []:
            uri = str(a.get("uri") or "")
            if not uri or "://" in uri or uri.startswith("/"):
                continue
            f = d / uri
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("x", encoding="utf-8")

    def check(doc: dict, name: str, expect_ok: bool, expect_path: str = "",
              make_files: bool = True) -> None:
        d = case / name
        d.mkdir(exist_ok=True)
        if make_files:
            make_artifacts(d, doc)
        f = d / "asset.json"
        f.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        out = d / "report.json"
        proc = run([str(SCRIPTS / "validate_envelope.py"), str(d), "--json-out", str(out)], case)
        rep = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
        res = (rep.get("results") or [{}])[0]
        errs = res.get("errors") or []
        hit = any(expect_path in (e.get("path") or "") for e in errs)
        ok = (proc.returncode == 0 and not errs) if expect_ok else (proc.returncode == 1 and hit)
        record(f"{'正例' if expect_ok else '负例'}：{name}", ok,
               f"rc={proc.returncode} errors={[e.get('path') for e in errs][:3]}")

    check(minimal_envelope(), "合规资产", True)

    bad = minimal_envelope(); bad.pop("asset_version")
    check(bad, "缺 asset_version", False, "asset_version")

    bad = minimal_envelope(); bad["asset_version"] = "1.0"
    check(bad, "版本非语义化", False, "asset_version")

    def as_skill(doc):
        doc["asset_type"] = "skill"
        doc["asset_id"] = "sciasset://2026ZD123456/earth/skill/sample"
        return doc

    bad = as_skill(minimal_envelope()); bad.pop("task_classes")
    check(bad, "skill 类资产缺 task_classes", False, "task_classes")

    bad = as_skill(minimal_envelope()); bad["task_classes"] = []
    check(bad, "skill 类资产 task_classes 为空", False, "task_classes")

    bad = minimal_envelope(); bad["lifecycle"] = "archived"
    check(bad, "lifecycle 非法", False, "lifecycle")

    bad = minimal_envelope(); bad["spec"]["artifacts"][0]["uri"] = "/opt/secret/part.csv"
    check(bad, "artifact 用了绝对路径", False, "spec.artifacts[0].uri")

    bad = minimal_envelope(); bad["governance"]["risk_level"] = "prohibited"
    check(bad, "prohibited 却带可下载内容", False, "spec.artifacts")

    bad = minimal_envelope()
    bad["spec"]["artifacts"][0]["uri"] = "missing/nope.csv"
    check(bad, "声明的实体不在包内", False, "spec.artifacts[0].uri", make_files=False)

    bad = minimal_envelope()
    bad["spec"]["artifacts"][0]["sha256"] = "0" * 64
    check(bad, "实体摘要与声明不符", False, "sha256")

    ok_bytes = minimal_envelope()
    ok_bytes["spec"]["artifacts"][0]["bytes"] = 1        # 文件内容是 "x"，实际 1 字节
    check(ok_bytes, "实体体积用 bytes 别名（等价 size_bytes）", True)

    bad = minimal_envelope()
    bad["spec"]["artifacts"][0]["bytes"] = 999
    check(bad, "bytes 声明的体积与实际不符", False, "bytes")

    bad = minimal_envelope()
    bad["spec"]["artifacts"][0]["uri"] = "../shared/part.csv"
    check(bad, "实体引用越出资产目录（自包含）", False, "spec.artifacts[0].uri")

    # asset_protocol 规范未明确要求：其内容不参与校验（越界也不报错）
    ok = minimal_envelope()
    ok["spec"]["asset_protocol"] = {"repository_uri": "../../asset_protocol_repo", "path": "x"}
    check(ok, "asset_protocol 不校验内容（即使指向目录外也放行）", True)

    bad = minimal_envelope(); bad["asset_id"] = "sciasset://2026ZD123456/earth/tool/sample"
    check(bad, "asset_id 类型段与 asset_type 不一致", False, "asset_id")

    bad = minimal_envelope(); bad["asset_id"] = "sciasset://mineralmind/data/sample"
    check(bad, "旧的三段格式（缺 project-id）", False, "asset_id")

    bad = minimal_envelope(); bad["asset_id"] = "sciasset://2026ZD123456/earth/data/sample-1.0.0"
    check(bad, "asset_id 带版本号", False, "asset_id")

    # 同名 asset_id 内容不一致 → 两者都被标记冲突
    d = case / "冲突"
    d.mkdir()
    a = minimal_envelope(); b = minimal_envelope(); b["description"] = "另一份不一致的描述。"
    (d / "a.json").write_text(json.dumps(a, ensure_ascii=False), encoding="utf-8")
    (d / "b.json").write_text(json.dumps(b, ensure_ascii=False), encoding="utf-8")
    out = d / "report.json"
    proc = run([str(SCRIPTS / "validate_envelope.py"), str(d / "a.json"), str(d / "b.json"),
                "--json-out", str(out)], case)
    rep = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    leg = case / "旧三段"
    leg.mkdir(exist_ok=True)
    doc = minimal_envelope(); doc["asset_id"] = "sciasset://mineralmind/data/sample"
    make_artifacts(leg, doc)
    (leg / "asset.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    proc = run([str(SCRIPTS / "validate_envelope.py"), str(leg)], case)
    record("正例：asset_id 报错文案写明格式规范与各段含义",
           "项目任务书首页" in proc.stdout and "自拟命名空间" in proc.stdout,
           "缺少规范说明")

    record("正例：schema 交叉校验有引擎标签，不再静默跳过",
           "builtin" in str(rep.get("summary", {}).get("jsonschema_cross_check"))
           or "jsonschema" in str(rep.get("summary", {}).get("jsonschema_cross_check")),
           f"engine={rep.get('summary', {}).get('jsonschema_cross_check')}")
    both = [r for r in (rep.get("results") or []) if r.get("errors")]
    record("负例：同名 asset_id 内容冲突（两份都标记）", proc.returncode == 1 and len(both) == 2,
           f"rc={proc.returncode} 标记数={len(both)}")


# --------------------------------------------------------------------------- T2 清单生成与复检
def t2_list(tmp: Path) -> None:
    print("\n[2/19] build_list.py + check_list.py — 生成即自检")
    import _deps
    _deps.ensure_openpyxl(verbose=False)
    from openpyxl import load_workbook

    case = tmp / "t2"
    case.mkdir()
    rows = case / "rows.json"
    rows.write_text(json.dumps(valid_rows(), ensure_ascii=False), encoding="utf-8")
    xlsx = case / "清单.xlsx"
    proc = run([str(SCRIPTS / "build_list.py"), "--rows", str(rows), "--out", str(xlsx)], case)
    rep = case / "check-list-report.json"
    body = json.loads(rep.read_text(encoding="utf-8")) if rep.exists() else {}
    record("正例：生成 23 列清单并自检通过",
           proc.returncode == 0 and (body.get("summary") or {}).get("errors") == 0,
           f"rc={proc.returncode} summary={body.get('summary')}")

    def tamper(name: str, mutate, expect_frag: str) -> None:
        d = case / f"负例_{name}"
        d.mkdir()
        shutil.copy(xlsx, d / "清单.xlsx")
        wb = load_workbook(d / "清单.xlsx")
        mutate(wb)
        wb.save(d / "清单.xlsx")
        out = d / "report.json"
        p = run([str(SCRIPTS / "check_list.py"), str(d / "清单.xlsx"), "--json-out", str(out)], case)
        r = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
        msgs = " ".join(x.get("message", "") for x in (r.get("errors") or []))
        record(f"负例：{name}", p.returncode == 1 and expect_frag in msgs, f"rc={p.returncode} msg={msgs[:80]}")

    def col(wb, name):
        ws = wb["资产明细"]
        for c in range(1, ws.max_column + 1):
            if ws.cell(row=1, column=c).value == name:
                return c
        raise KeyError(name)

    def _header(wb):
        wb["资产明细"].cell(row=1, column=1).value = "资产名字"

    def _enum(wb):
        wb["资产明细"].cell(row=2, column=col(wb, "资产类型")).value = "unknown"

    def _required(wb):
        wb["资产明细"].cell(row=2, column=col(wb, "资产名称")).value = ""

    def _semver(wb):
        wb["资产明细"].cell(row=2, column=col(wb, "版本")).value = "1.0"

    def _dup(wb):
        ws = wb["资产明细"]
        for c in range(1, ws.max_column + 1):
            ws.cell(row=3, column=c).value = ws.cell(row=2, column=c).value

    def _id_ver(wb):
        ws = wb["资产明细"]
        for c in range(1, ws.max_column + 1):
            if ws.cell(row=1, column=c).value == "资产ID":
                ws.cell(row=2, column=c).value = "2026ZD123456/earth/data/sample-1.0.0"
                break

    def _id_fmt(wb):
        ws = wb["资产明细"]
        for c in range(1, ws.max_column + 1):
            if ws.cell(row=1, column=c).value == "资产ID":
                ws.cell(row=2, column=c).value = "mineralmind/data/sample"
                break

    def _dangling(wb):
        ws = wb["用例明细"]
        for c in range(1, ws.max_column + 1):
            if ws.cell(row=1, column=c).value == "资产名称":
                ws.cell(row=2, column=c).value = "不存在的资产"
                break

    tamper("表头被改", _header, "表头")
    tamper("枚举值非法", _enum, "不在 schema options 内")
    tamper("必填为空", _required, "必填")
    tamper("版本非语义化", _semver, "语义化")
    tamper("行标识重复", _dup, "重复")
    tamper("用例明细引用不存在的资产", _dangling, "不存在")
    tamper("资产ID 带了版本号", _id_ver, "不应包含版本号")
    tamper("资产ID 格式不合法", _id_fmt, "资产ID")


# --------------------------------------------------------------------------- T3 覆盖度门禁
def t3_coverage(tmp: Path) -> None:
    print("\n[3/19] check_coverage.py — 低于下限必须拦住")
    case = tmp / "t3"
    case.mkdir()

    def make(n_cases: int, executed: int) -> Path:
        cases = []
        for i in range(n_cases):
            c = {"case_id": f"c{i}", "asset_id": "sciasset://2026ZD123456/earth/tool/x", "asset_type": "tool",
                 "execution_status": "completed" if i < executed else "planned",
                 "validation_status": "pass" if i < executed else "not_evaluated",
                 "expected_kind": "success"}
            if i == n_cases - 1:      # 末位固定为预期错误用例，满足 tool 的 negative_expected 要求
                c["expected_kind"] = "error"
                c["kind"] = "rejection"
            cases.append(c)
        d = case / f"n{n_cases}e{executed}"
        d.mkdir()
        (d / "cases.json").write_text(json.dumps({"cases": cases}, ensure_ascii=False), encoding="utf-8")
        (d / "inventory.json").write_text(json.dumps({"assets": [{"asset_id": "sciasset://2026ZD123456/earth/tool/x",
                                                                  "asset_type": "tool"}]}), encoding="utf-8")
        (d / "validation-report.md").write_text("# r\n\n## 5. 判定\n", encoding="utf-8")
        return d

    d = make(1, 1)
    proc = run([str(SCRIPTS / "check_coverage.py"), "--cases", str(d / "cases.json"),
                "--inventory", str(d / "inventory.json"), "--run-dir", str(d),
                "--report", str(d / "validation-report.md")], case)
    cov = json.loads((d / "coverage.json").read_text(encoding="utf-8")) if (d / "coverage.json").exists() else {}
    record("负例：tool 只有 1 组用例（下限 3）→ 判覆盖不足且退出码 1",
           proc.returncode == 1 and cov.get("verdict") == "insufficient",
           f"rc={proc.returncode} verdict={cov.get('verdict')}")

    d = make(4, 3)
    proc = run([str(SCRIPTS / "check_coverage.py"), "--cases", str(d / "cases.json"),
                "--inventory", str(d / "inventory.json"), "--run-dir", str(d),
                "--report", str(d / "validation-report.md")], case)
    cov = json.loads((d / "coverage.json").read_text(encoding="utf-8")) if (d / "coverage.json").exists() else {}
    record("正例：tool 4 组、执行 3 组 → 覆盖充足、退出码 0",
           proc.returncode == 0 and cov.get("verdict") == "sufficient",
           f"rc={proc.returncode} verdict={cov.get('verdict')}")


# --------------------------------------------------------------------------- T4 副本自检
def t4_work_copy(tmp: Path) -> None:
    print("\n[4/19] work_copy.py — 副本 init 自检与清理")
    case = tmp / "t4"
    pkg = case / "pkg"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "a.txt").write_text("a\n", encoding="utf-8")
    (pkg / "sub" / "b.txt").write_text("bb\n", encoding="utf-8")
    sums = subprocess.run(["sha256sum", "a.txt", "sub/b.txt"], cwd=pkg, capture_output=True, text=True)
    (pkg / "SHA256SUMS").write_text(sums.stdout, encoding="utf-8")
    work = case / "work"

    proc = run([str(SCRIPTS / "work_copy.py"), "init", str(pkg), "--run-id", "run-20260101-000001", "--work-root", str(work)], case)
    state = json.loads((pkg / "asset-runs" / "run-20260101-000001" / "work-copy.json").read_text(encoding="utf-8"))
    record("正例：init 复制并自检文件数/字节/摘要",
           proc.returncode == 0 and (state.get("init_verify") or {}).get("ok") is True,
           f"rc={proc.returncode} verify={(state.get('init_verify') or {}).get('ok')}")

    # 在副本里删掉一个文件，再 init：副本已存在被复用 → 自检应失败并删副本
    copy = Path(state["work_copy"])
    (copy / "sub" / "b.txt").unlink()
    proc = run([str(SCRIPTS / "work_copy.py"), "init", str(pkg), "--run-id", "run-20260101-000001", "--work-root", str(work)], case)
    record("负例：副本缺文件时 init 自检失败、删副本、退出码 1",
           proc.returncode == 1 and not copy.exists(),
           f"rc={proc.returncode} 副本仍在={copy.exists()}")

    # 包根 SHA256SUMS 混入历史 run 产物（3.1.2 两个包的真实情况）→ init 必须仍能建立副本并记下排除项
    pkg2 = case / "pkg2"
    (pkg2 / "asset-runs" / "run-old").mkdir(parents=True)
    (pkg2 / "a.txt").write_text("a\n", encoding="utf-8")
    (pkg2 / "asset-runs" / "run-old" / "ev.json").write_text("z", encoding="utf-8")
    sums2 = subprocess.run(["sha256sum", "a.txt", "asset-runs/run-old/ev.json"],
                           cwd=pkg2, capture_output=True, text=True)
    (pkg2 / "SHA256SUMS").write_text(sums2.stdout, encoding="utf-8")
    work2 = case / "work2"
    proc = run([str(SCRIPTS / "work_copy.py"), "init", str(pkg2), "--run-id", "run-20260101-000002",
                "--work-root", str(work2)], case)
    st2 = json.loads((pkg2 / "asset-runs" / "run-20260101-000002" / "work-copy.json").read_text(encoding="utf-8"))
    sums_entry = (st2.get("init_verify") or {}).get("sums") or [{}]
    iv = st2.get("init_verify") or {}
    record("正例：包内有 SHA256SUMS 也不核验（按约定跳过），init 只看文件数/字节数",
           proc.returncode == 0 and iv.get("ok") is True
           and iv.get("sums_checked") is False and not iv.get("sums"),
           f"rc={proc.returncode} sums_checked={iv.get('sums_checked')} sums={iv.get('sums')}")

    proc = run([str(SCRIPTS / "work_copy.py"), "clean", str(pkg), "--run-id", "run-20260101-000001", "--verify"], case)
    state = json.loads((pkg / "asset-runs" / "run-20260101-000001" / "work-copy.json").read_text(encoding="utf-8"))
    vac = state.get("verify_after_clean") or {}
    record("正例：clean 删副本并复核原包（文件数/字节数，不核 SHA256SUMS）",
           proc.returncode == 0 and vac.get("checked") is False
           and vac.get("files") == 3,
           f"rc={proc.returncode} verify={vac}")


# --------------------------------------------------------------------------- T5 证据保留
def t5_prune(tmp: Path) -> None:
    print("\n[5/19] prune_evidence.py — 只留报告类证据")
    run_dir = tmp / "t5" / "run-t5"
    (run_dir / "tool" / "work" / "case1").mkdir(parents=True)
    (run_dir / "refs").mkdir()
    (run_dir / "registration").mkdir()
    (run_dir / "tool" / "work" / "case1" / "big.tif").write_bytes(b"0" * 4096)
    (run_dir / "tool" / "correctness_report.json").write_text('{"cases":[]}', encoding="utf-8")
    (run_dir / "refs" / "frozen.npy").write_bytes(b"1" * 128)
    (run_dir / "registration" / "清单.xlsx").write_bytes(b"2" * 64)
    (run_dir / "validation-report.md").write_text("# r\n\n## 5. 判定\n", encoding="utf-8")

    run([str(SCRIPTS / "prune_evidence.py"), "--run", str(run_dir), "--dry-run"], tmp / "t5")
    dry = json.loads((run_dir.parent / "t5" / "evidence-prune.json").read_text(encoding="utf-8")) if (run_dir.parent / "t5" / "evidence-prune.json").exists() else {}
    record("正例：--dry-run 的 bytes_after 按删完预估，不沿用 before",
           not dry or dry.get("bytes_after", 0) < dry.get("bytes_before", 0),
           f"before={dry.get('bytes_before')} after={dry.get('bytes_after')}")
    record("正例：--dry-run 只预览，不删文件也不落盘",
           (run_dir / "tool" / "work" / "case1" / "big.tif").exists()
           and not (run_dir / "evidence-prune.json").exists(),
           "dry-run 却发生了删除或写了记录")

    (run_dir / "cases.json").write_text(json.dumps({"cases": [
        {"case_id": "c1", "actual_output": ["tool/work/case1/big.tif",
                                            "tool/correctness_report.json"]}]}, ensure_ascii=False),
        encoding="utf-8")
    proc = run([str(SCRIPTS / "prune_evidence.py"), "--run", str(run_dir)], tmp / "t5")
    led = json.loads((run_dir / "cases.json").read_text(encoding="utf-8"))["cases"][0]
    record("正例：actual_output 是列表时台账照常标记（不崩）",
           proc.returncode == 0 and led.get("actual_pruned") is True,
           f"rc={proc.returncode} pruned={led.get('actual_pruned')}")
    rec = json.loads((run_dir / "evidence-prune.json").read_text(encoding="utf-8"))
    kept = all((run_dir / p).exists() for p in ("tool/correctness_report.json", "refs/frozen.npy",
                                                "registration/清单.xlsx", "validation-report.md"))
    record("正例：实跑清掉 */work/、保留报告与冻结参考值",
           not (run_dir / "tool" / "work").exists() and kept and rec.get("pruned_items", 0) >= 1,
           f"pruned={rec.get('pruned_items')} 保留={kept}")


# --------------------------------------------------------------------------- T6 Sage 探测
def t6_sage(tmp: Path) -> None:
    print("\n[6/19] sage_probe.py — 需求判定、配置缺失向用户索取")
    case = tmp / "t6"
    plain = case / "plain"
    (plain / "data").mkdir(parents=True)
    (plain / "catalog.json").write_text(json.dumps([{"asset_id": "sciasset://2026ZD123456/earth/data/x",
                                                     "asset_type": "data"}]), encoding="utf-8")
    out = case / "need_plain.json"
    run([str(SCRIPTS / "sage_probe.py"), "detect", str(plain), "--json-out", str(out)], case)
    need = json.loads(out.read_text(encoding="utf-8"))
    record("正例：纯数据包判定为无需 Sage 测试", need.get("needed") is False, f"needed={need.get('needed')}")

    llm = case / "llm"
    (llm / "skill").mkdir(parents=True)
    (llm / "catalog.json").write_text(json.dumps([{"asset_id": "sciasset://2026ZD123456/earth/skill/x",
                                                   "asset_type": "skill"}]), encoding="utf-8")
    (llm / "skill" / "workflow.env.example").write_text("LLM_BASE_URL=http://x/v1\nLLM_MODEL=m\n",
                                                        encoding="utf-8")
    out = case / "need_llm.json"
    run([str(SCRIPTS / "sage_probe.py"), "detect", str(llm), "--json-out", str(out)], case)
    need = json.loads(out.read_text(encoding="utf-8"))
    record("正例：含大模型 API 证据的包判定为需要测试",
           need.get("needed") is True and need.get("evidence"), f"needed={need.get('needed')}")

    # catalog.json 的 assets 是**包络文件路径数组**（不是对象数组）时不能崩
    patharr = case / "patharr"
    (patharr / "runtime").mkdir(parents=True)
    (patharr / "runtime" / "asset.json").write_text(json.dumps(
        {"asset_id": "sciasset://2026ZD123456/earth/environment/runtime",
         "asset_type": "environment", "name": "runtime"}, ensure_ascii=False), encoding="utf-8")
    (patharr / "sk").mkdir(parents=True)
    (patharr / "sk" / "asset.json").write_text(json.dumps(
        {"asset_id": "sciasset://2026ZD123456/earth/skill/sk", "asset_type": "skill", "name": "sk"},
        ensure_ascii=False), encoding="utf-8")
    (patharr / "catalog.json").write_text(json.dumps(
        {"schema_version": "1.0.0", "assets": ["runtime/asset.json", "sk/asset.json"]}, ensure_ascii=False),
        encoding="utf-8")
    out = case / "need_patharr.json"
    proc = run([str(SCRIPTS / "sage_probe.py"), "detect", str(patharr), "--json-out", str(out)], case)
    need = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {}
    record("正例：catalog.json 的 assets 是路径数组时不崩溃，且能识别资产类型（含 skill → 需测）",
           proc.returncode == 0 and need.get("asset_types") == ["environment", "skill"]
           and need.get("needed") is True,
           f"rc={proc.returncode} types={need.get('asset_types')}")

    # 资产放在子目录（无根级 asset.json）+ 无 LLM 证据 → 类型被识别，但不需要测
    deep = case / "deep"
    (deep / "a" / "b").mkdir(parents=True)
    (deep / "a" / "b" / "asset.json").write_text(json.dumps(
        {"asset_id": "sciasset://2026ZD123456/earth/skill/deep", "asset_type": "skill"}, ensure_ascii=False),
        encoding="utf-8")
    out = case / "need_deep.json"
    run([str(SCRIPTS / "sage_probe.py"), "detect", str(deep), "--json-out", str(out)], case)
    need = json.loads(out.read_text(encoding="utf-8"))
    record("正例：子目录里的 asset.json 也被读到；有 skill 资产即判 Sage 适用",
           need.get("asset_types") == ["skill"] and need.get("needed") is True
           and any("skill" in r for r in need.get("reasons") or []),
           f"types={need.get('asset_types')} needed={need.get('needed')}")

    out = case / "need_forced.json"
    run([str(SCRIPTS / "sage_probe.py"), "detect", str(deep), "--assume-llm", "--json-out", str(out)], case)
    need = json.loads(out.read_text(encoding="utf-8"))
    record("正例：--assume-llm 可人工强制判为需要测", need.get("needed") is True, f"needed={need.get('needed')}")

    # 隔离 HOME → 系统里找不到 Sage 配置 → 必须退出码 3 并打印索取清单
    fake_home = case / "home"
    fake_home.mkdir(exist_ok=True)
    env = {"HOME": str(fake_home)}
    for k in ("SAGE_BASE_URL", "SAGE_MODEL", "SAGE_API_KEY", "SAGE_LLM_BASE_URL",
              "SAGE_LLM_MODEL", "SAGE_LLM_API_KEY"):
        env[k] = ""
    proc = run([str(SCRIPTS / "sage_probe.py"), "config", "--json-out", str(case / "cfg.json")],
               case, env_extra=env)
    cfg = json.loads((case / "cfg.json").read_text(encoding="utf-8")) if (case / "cfg.json").exists() else {}
    record("负例：找不到 Sage 配置 → 退出码 3 且给出索取清单",
           proc.returncode == 3 and "SAGE_BASE_URL" in proc.stdout and cfg.get("status") == "missing_config",
           f"rc={proc.returncode} status={cfg.get('status')}")

    probe_env = dict(env, DUMMY_SAGE_KEY="dummy")
    proc = run([str(SCRIPTS / "sage_probe.py"), "config", "--base-url", "http://127.0.0.1:9/v1",
                "--model", "x", "--api-key-env", "DUMMY_SAGE_KEY", "--probe",
                "--json-out", str(case / "cfg2.json")], case, env_extra=probe_env)
    record("负例：配置齐全但探测不通 → 退出码 4", proc.returncode == 4, f"rc={proc.returncode}")

    run([str(SCRIPTS / "sage_probe.py"), "report", str(case), "--status", "blocked_missing_config",
         "--note", "自测"], case)
    rep = json.loads((case / "sage-a1.5-report.json").read_text(encoding="utf-8"))
    record("正例：结论落盘且显式标注不计入判定",
           rep.get("is_acceptance_criterion") is False and rep.get("status") == "blocked_missing_config",
           f"criterion={rep.get('is_acceptance_criterion')}")


# --------------------------------------------------------------------------- T7 资产ID 生成
def t7_asset_ids(tmp: Path) -> None:
    print("\n[7/19] asset_ids.py — 从包络生成清单资产ID")
    pkg = tmp / "t7" / "pkg"
    (pkg / "data" / "a").mkdir(parents=True)
    (pkg / "catalog.json").write_text(json.dumps([
        {"asset_id": "sciasset://2026ZD123456/earth/data/sample", "asset_type": "data",
         "name": "示例数据资产", "asset_version": "1.0.0"},
        {"asset_id": "sciasset://2026ZD123456/earth/tool/run", "asset_type": "tool",
         "name": "示例轨迹工具", "asset_version": "0.0.0+run.20260825.001"},
    ], ensure_ascii=False), encoding="utf-8")
    out = tmp / "t7" / "ids.json"
    proc = run([str(SCRIPTS / "asset_ids.py"), "--package", str(pkg), "--json-out", str(out)], tmp / "t7")
    got = json.loads(out.read_text(encoding="utf-8"))
    ids = [r["list_asset_id"] for r in got["rows"]]
    record("正例：asset_id 去掉前缀即为清单资产ID（不含版本）",
           proc.returncode == 0
           and "2026ZD123456/earth/data/sample" in ids
           and "2026ZD123456/earth/tool/run" in ids,
           f"rc={proc.returncode} ids={ids}")

    rows = tmp / "t7" / "rows.json"
    rows.write_text(json.dumps({"rows": [
        {"资产名称": "示例数据资产", "版本": "1.0.0"},
        {"资产名称": "示例轨迹工具", "版本": "0.0.0+run.20260825.001"}]}, ensure_ascii=False), encoding="utf-8")
    proc = run([str(SCRIPTS / "asset_ids.py"), "--package", str(pkg), "--rows", str(rows), "--inject"], tmp / "t7")
    back = json.loads(rows.read_text(encoding="utf-8"))["rows"]
    record("正例：--inject 按「资产名称+版本」写回 rows.json",
           proc.returncode == 0 and all(r.get("资产ID") for r in back),
           f"rc={proc.returncode} rows={[r.get('资产ID') for r in back]}")

    bad = tmp / "t7" / "bad"
    (bad / "x").mkdir(parents=True)
    (bad / "catalog.json").write_text(json.dumps([
        {"asset_id": "sciasset://mineralmind/data/sample", "asset_type": "data",
         "name": "旧格式", "asset_version": "1.0.0"}], ensure_ascii=False), encoding="utf-8")
    proc = run([str(SCRIPTS / "asset_ids.py"), "--package", str(bad), "--check"], tmp / "t7")
    record("负例：包络仍是三段格式 → --check 退出码 1", proc.returncode == 1, f"rc={proc.returncode}")


# --------------------------------------------------------------------------- T8 外部资产引用
def t8_external_refs(tmp: Path) -> None:
    print("\n[8/19] external_refs.py — 每个资产目录下的外部资产引用校验与「资产关联」列")
    base = tmp / "t8"
    pkg = base / "pkg"

    def make_asset(name: str, aid: str) -> Path:
        d = pkg / name
        (d / "data").mkdir(parents=True, exist_ok=True)
        (d / "asset.json").write_text(json.dumps(
            {"asset_id": aid, "asset_type": "data", "name": name, "asset_version": "1.0.0"},
            ensure_ascii=False), encoding="utf-8")
        (d / "data" / "x.csv").write_text("x", encoding="utf-8")
        return d

    alpha = make_asset("alpha", "sciasset://2026ZD123456/earth/data/alpha")
    beta = make_asset("beta", "sciasset://2026ZD123456/earth/data/beta")

    rows = base / "rows.json"
    rows.write_text(json.dumps({"rows": [
        {"资产ID": "2026ZD123456/earth/data/alpha", "资产名称": "alpha", "版本": "1.0.0", "资产关联": "旧内容"},
        {"资产ID": "2026ZD123456/earth/data/beta", "资产名称": "beta", "版本": "1.0.0", "资产关联": "旧内容"}]},
        ensure_ascii=False), encoding="utf-8")

    proc = run([str(SCRIPTS / "external_refs.py"), "--package", str(pkg)], base)
    record("正例：各资产目录都没有 external_assets.json 时合规跳过",
           proc.returncode == 0 and "未发现任何" in proc.stdout, f"rc={proc.returncode}")

    (alpha / "external_assets.json").write_text(json.dumps({
        "schema_version": "1.0.0",
        "assets": [{"asset_id": "sciasset://2026ZD123456/earth/data/ext-a", "asset_version": "1.0.0",
                    "target_path": "data/ext-a", "media_type": "inode/directory"}]},
        ensure_ascii=False), encoding="utf-8")
    out = base / "refs.json"
    proc = run([str(SCRIPTS / "external_refs.py"), "--package", str(pkg), "--rows", str(rows),
                "--inject", "--json-out", str(out)], base)
    back = {r["资产名称"]: r.get("资产关联") for r in json.loads(rows.read_text(encoding="utf-8"))["rows"]}
    record("正例：只把该资产自己声明的编号写进它那一行（去前缀）",
           proc.returncode == 0 and back["alpha"] == "2026ZD123456/earth/data/ext-a" and back["beta"] == "",
           f"rows={back}")

    def bad(dirname: str, doc: dict, expect: str) -> None:
        d = make_asset(dirname, f"sciasset://2026ZD123456/earth/data/{dirname}")
        (d / "external_assets.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        pr = run([str(SCRIPTS / "external_refs.py"), "--package", str(pkg), "--check",
                  "--json-out", str(d / "r.json")], base)
        rep = json.loads((d / "r.json").read_text(encoding="utf-8"))
        msgs = " ".join(x["message"] for x in rep.get("problems") or [])
        record(f"负例：{dirname}", pr.returncode == 1 and expect in msgs, f"rc={pr.returncode} msg={msgs[:80]}")

    good_ext = {"asset_id": "sciasset://2026ZD123456/earth/data/ext-a", "asset_version": "1.0.0",
                "target_path": "data/ext-a"}
    bad("bad1", {"schema_version": "1.0.0", "assets": [
        {"asset_id": "sciasset://mineralmind/data/raw", "asset_version": "1.0.0", "target_path": "a"}]},
        "不含版本号")
    bad("bad2", {"schema_version": "1.0.0", "assets": [
        dict(good_ext, target_path="/abs/path.nc")]}, "相对资产目录")
    bad("bad3", {"schema_version": "1.0.0", "assets": [
        {"asset_id": good_ext["asset_id"], "target_path": "a"}]}, "缺少 asset_version")
    bad("bad4", {"schema_version": "1.0.0", "assets": [
        dict(good_ext, target_path="../outside.nc")]}, "不得包含")


# --------------------------------------------------------------------------- T9 版本可追溯
def t9_version_stamp(tmp: Path) -> None:
    print("\n[9/19] _version.py + stamp_report.py — 校验 skill 版本可追溯")
    ver = (SKILL / 'VERSION').read_text(encoding='utf-8').strip()
    out = tmp / "t9" / "v.json"
    (tmp / "t9").mkdir(parents=True, exist_ok=True)
    proc = run([str(SCRIPTS / "_version.py")], tmp / "t9")
    info = json.loads(proc.stdout)
    record("正例：VERSION 文件是版本单一来源，且能取到提交号",
           info.get("version") == ver and info.get("name") == "sci-asset-verify-skill",
           f"version={info.get('version')} VERSION={ver}")

    # 生产者 JSON 自带 skill 版本
    pkg = tmp / "t9" / "pkg"
    (pkg / "data").mkdir(parents=True)
    (pkg / "data" / "a.csv").write_text("x", encoding="utf-8")
    (pkg / "asset.json").write_text(json.dumps({
        "schema_version": "1.0.0", "asset_id": "sciasset://2026ZD123456/earth/data/s",
        "asset_type": "data", "name": "示例数据", "description": "d", "asset_version": "1.0.0",
        "lifecycle": "draft", "domains": [{"id": "domain:earth-science"}],
        "task_classes": [{"id": "operation:data-processing"}],
        "provenance": {"created_time": "2026-01-01T00:00:00Z",
                       "provider": [{"name": "p", "role": "author"}], "source": {"kind": "original"}},
        "governance": {"license": "L", "access": "public", "risk_level": "low",
                       "privacy": "none", "usage_constraints": []},
        "spec": {"metadata": {"format": ["csv"]},
                 "artifacts": [{"uri": "data/a.csv", "media_type": "text/csv"}],
                 "domain_attributes": {}, "asset_protocol": {}}}, ensure_ascii=False), encoding="utf-8")
    env = tmp / "t9" / "envelope.json"
    run([str(SCRIPTS / "validate_envelope.py"), str(pkg), "--json-out", str(env)], tmp / "t9")
    got = json.loads(env.read_text(encoding="utf-8")).get("skill") or {}
    record("正例：包络报告带 skill 版本", got.get("version") == ver,
           f"version={got.get('version')}")

    ids = tmp / "t9" / "ids.json"
    run([str(SCRIPTS / "asset_ids.py"), "--package", str(pkg), "--json-out", str(ids)], tmp / "t9")
    got = json.loads(ids.read_text(encoding="utf-8")).get("skill") or {}
    record("正例：资产ID 映射带 skill 版本", got.get("version") == ver, f"version={got.get('version')}")

    # 报告开头 front matter + 幂等 + 台账补戳
    r = tmp / "t9" / "run"
    r.mkdir()
    (r / "validation-report.md").write_text("# 报告\n\n## 1. 结论\n通过。\n", encoding="utf-8")
    (r / "cases.json").write_text(json.dumps({"cases": [{"case_id": "c1"}]}), encoding="utf-8")
    run([str(SCRIPTS / "stamp_report.py"), "--run", str(r)], tmp / "t9")
    txt = (r / "validation-report.md").read_text(encoding="utf-8")
    head = txt.split("\n---\n", 1)[0]
    record("正例：版本与生成时间写在文件开头的 front matter",
           txt.startswith("---\n") and "skill_version" in head and ver in head
           and "generated_at" in head,
           f"head={head[:80]!r}")
    record("正例：正文里不再出现版本小节",
           "校验工具版本" not in txt, "正文仍有版本小节")
    record("正例：台账补上 skill 字段",
           json.loads((r / "cases.json").read_text(encoding="utf-8")).get("skill", {}).get("version") == ver,
           "台账缺版本")

    run([str(SCRIPTS / "stamp_report.py"), "--run", str(r)], tmp / "t9")
    txt2 = (r / "validation-report.md").read_text(encoding="utf-8")
    record("正例：重复执行不重复写 front matter（幂等）",
           txt2.count("\n---\n") == 1 and txt2.count("skill_version:") == 1,
           f"---\n 出现 {txt2.count(chr(10) + '---' + chr(10))} 次")

    # 旧版文末小节应被迁移删除
    legacy = tmp / "t9" / "legacy"
    legacy.mkdir()
    (legacy / "validation-report.md").write_text(
        "# 报告\n\n## 1. 结论\n通过。\n\n<!-- validation-tool:begin -->\n## 校验工具版本\n\n| 项 | 值 |\n|---|---|\n| 版本 | `old` |\n<!-- validation-tool:end -->\n",
        encoding="utf-8")
    run([str(SCRIPTS / "stamp_report.py"), "--run", str(legacy)], tmp / "t9")
    old_txt = (legacy / "validation-report.md").read_text(encoding="utf-8")
    record("正例：旧的文末版本小节被迁移成 front matter",
           old_txt.startswith("---\n") and "validation-tool:begin" not in old_txt
           and "校验工具版本" not in old_txt, f"head={old_txt[:40]!r}")


# --------------------------------------------------------------------------- T10 报告分级
def t10_report_levels(tmp: Path) -> None:
    print("\n[10/19] check_report.py — 问题清单 P0/P1/P2 分级")
    d = tmp / "t10"
    d.mkdir(parents=True, exist_ok=True)

    def write(name: str, body: str) -> Path:
        f = d / name
        f.write_text(body, encoding="utf-8")
        return f

    ok = write("ok.md", "# R\n\n## 1. 结论\n通过。\n\n## 4. 问题清单\n\n### P0 阻断\n"
                        "| P0-1 | 包络不合规 | 须修 |\n\n### P1 重要\n无\n\n### P2 建议\n"
                        "| P2-1 | 命名建议 | 可选 |\n")
    proc = run([str(SCRIPTS / "check_report.py"), "--report", str(ok)], d)
    record("正例：三级齐全的报告通过", proc.returncode == 0, f"rc={proc.returncode}")

    clean = write("clean.md", "# R\n\n## 4. 问题清单\n\n### P0 阻断\n无\n\n### P1 重要\n无\n\n"
                              "### P2 建议\n无\n\n**未发现问题。**\n")
    proc = run([str(SCRIPTS / "check_report.py"), "--report", str(clean)], d)
    record("正例：无问题时写「未发现问题」也通过", proc.returncode == 0, f"rc={proc.returncode}")

    flat = write("flat.md", "# R\n\n## 4. 问题\n- 缺 verify.py\n- 路径写死\n")
    proc = run([str(SCRIPTS / "check_report.py"), "--report", str(flat)], d)
    record("负例：不分级的流水清单被拒", proc.returncode == 1 and "分级" in proc.stderr,
           f"rc={proc.returncode}")

    none = write("none.md", "# R\n\n## 1. 结论\n通过。\n")
    proc = run([str(SCRIPTS / "check_report.py"), "--report", str(none)], d)
    record("负例：整节缺失被拒", proc.returncode == 1 and "缺少「问题清单」" in proc.stderr,
           f"rc={proc.returncode}")

    empty = write("empty.md", "# R\n\n## 4. 问题清单\n\n### P0 阻断\n\n### P1 重要\n\n### P2 建议\n")
    proc = run([str(SCRIPTS / "check_report.py"), "--report", str(empty)], d)
    record("负例：三级全空且没写「未发现问题」被拒",
           proc.returncode == 1 and "未发现问题" in proc.stderr, f"rc={proc.returncode}")


# --------------------------------------------------------------------------- T11 资产自包含
def t11_selfcontained(tmp: Path) -> None:
    print("\n[11/19] check_selfcontained.py — 资产只能引用自己目录内的文件")
    d = tmp / "t11"
    pkg = d / "pkg"
    good = pkg / "asset-a"
    (good / "data").mkdir(parents=True)
    (good / "data" / "x.csv").write_text("x", encoding="utf-8")
    (good / "run.py").write_text("open('data/x.csv')\n", encoding="utf-8")
    (good / "asset.json").write_text(json.dumps({
        "asset_id": "sciasset://2026ZD123456/earth/data/a", "asset_type": "data",
        "spec": {"artifacts": [{"uri": "data/x.csv", "media_type": "text/csv"}],
                 "asset_protocol": {"repository_uri": "./"}}}, ensure_ascii=False), encoding="utf-8")

    bad = pkg / "asset-b"
    (bad / "sub").mkdir(parents=True)
    (bad / "sub" / "s.py").write_text("p = '../asset-a/data/x.csv'\n", encoding="utf-8")
    (bad / "asset.json").write_text(json.dumps({
        "asset_id": "sciasset://2026ZD123456/earth/data/b", "asset_type": "data",
        "spec": {"artifacts": [{"uri": "../asset-a/data/x.csv", "media_type": "text/csv"}],
                 "asset_protocol": {}}}, ensure_ascii=False), encoding="utf-8")

    out = d / "sc.json"
    proc = run([str(SCRIPTS / "check_selfcontained.py"), "--package", str(pkg),
                "--json-out", str(out)], d)
    rep = json.loads(out.read_text(encoding="utf-8"))
    by_asset = {r["asset_dir"]: r for r in rep["results"]}
    record("正例：目录内自足的资产判为自包含",
           by_asset.get("asset-a", {}).get("self_contained") is True,
           f"asset-a={by_asset.get('asset-a', {}).get('self_contained')}")
    record("负例：引用兄弟资产的文件被判越界，且提示改用 external_assets.json",
           proc.returncode == 1
           and by_asset.get("asset-b", {}).get("self_contained") is False
           and any("external_assets.json" in (v.get("hint") or "")
                   for v in by_asset.get("asset-b", {}).get("violations") or []),
           f"rc={proc.returncode}")

    # 软链：声明的 target_path 属合规；未声明的越界软链算违规
    outside_dir = tmp / "t11" / "elsewhere"
    (outside_dir / "inner").mkdir(parents=True, exist_ok=True)
    (outside_dir / "inner" / "z.csv").write_text("z", encoding="utf-8")
    (good / "ref" / "path").mkdir(parents=True, exist_ok=True)
    try:
        (good / "ref" / "path" / "declared").symlink_to(outside_dir / "inner", target_is_directory=True)
        (good / "undeclared").symlink_to(outside_dir / "inner", target_is_directory=True)
        (good / "external_assets.json").write_text(json.dumps({
            "schema_version": "1.0.0",
            "assets": [{"asset_id": "sciasset://2026ZD123456/earth/data/other", "asset_version": "1.0.0",
                        "target_path": "ref/path/declared"}]}, ensure_ascii=False), encoding="utf-8")
        proc = run([str(SCRIPTS / "check_selfcontained.py"), "--package", str(pkg),
                    "--json-out", str(out)], d)
        rep = json.loads(out.read_text(encoding="utf-8"))
        a = next(r for r in rep["results"] if r["asset_dir"] == "asset-a")
        kinds = [v["kind"] for v in a["violations"]]
        record("正例：声明的挂载软链豁免，未声明的越界软链仍算违规",
               "declared-external-link" in kinds and "symlink-escape" in kinds
               and a["violation_count"] == 1,
               f"kinds={kinds} violations={a['violation_count']}")
        (good / "external_assets.json").unlink()
        (good / "ref" / "path" / "declared").unlink()
        (good / "undeclared").unlink()
    except OSError as exc:
        record("正例：声明的挂载软链豁免，未声明的越界软链仍算违规", False, f"无法建软链：{exc}")

    # 文档里的绝对路径只算提示，不判违规
    (good / "README.md").write_text("示例：结果写到 /home/someone/out.csv\n", encoding="utf-8")
    proc = run([str(SCRIPTS / "check_selfcontained.py"), "--package", str(pkg),
                "--json-out", str(out)], d)
    rep = json.loads(out.read_text(encoding="utf-8"))
    a = next(r for r in rep["results"] if r["asset_dir"] == "asset-a")
    record("正例：文档里的本机绝对路径只提示不判违规",
           a["self_contained"] is True and a["note_count"] >= 1,
           f"contained={a['self_contained']} notes={a['note_count']}")


    # 容器化资产：容器内绝对路径记 note，宿主机路径仍判越界；无容器证据则判越界
    cont = tmp / "t11" / "cont"
    (cont / "skill1").mkdir(parents=True, exist_ok=True)
    (cont / "env1").mkdir(parents=True, exist_ok=True)
    (cont / "skill1" / "asset.json").write_text(json.dumps(
        {"asset_id": "sciasset://2026ZD123456/earth/skill/skill1", "asset_type": "skill",
         "name": "skill1", "asset_version": "1.0.0", "spec": {"artifacts": []}}, ensure_ascii=False),
        encoding="utf-8")
    (cont / "skill1" / "run.sh").write_text("DATA=/opt/ai-mineral/x\nBAD=/home/u/y\n", encoding="utf-8")
    (cont / "env1" / "asset.json").write_text(json.dumps(
        {"asset_id": "sciasset://2026ZD123456/earth/environment/env1", "asset_type": "environment",
         "name": "env1", "asset_version": "1.0.0",
         "spec": {"metadata": {"environment_type": "docker", "image": "img:1"}, "artifacts": []}},
        ensure_ascii=False), encoding="utf-8")
    out_c = d / "cont.json" if (d := tmp / "t11").exists() else tmp / "cont.json"
    run([str(SCRIPTS / "check_selfcontained.py"), "--package", str(cont), "--json-out", str(out_c)], tmp)
    rep_c = json.loads(out_c.read_text(encoding="utf-8"))
    skill_res = next(r for r in rep_c["results"] if r["asset_dir"] == "skill1")
    hard = [v["value"] for v in skill_res["violations"] if v["severity"] != "note"]
    soft = [v["value"] for v in skill_res["violations"] if v["severity"] == "note"]
    record("正例：容器资产里 /opt 路径记 note，宿主机 /home 路径仍判越界",
           skill_res["violation_count"] == 1 and hard == ["/home/u/y"]
           and any("/opt/" in x for x in soft) and skill_res.get("container_context"),
           f"hard={hard} soft={soft}")

    plain2 = tmp / "t11" / "plain2"
    (plain2 / "tool1").mkdir(parents=True, exist_ok=True)
    (plain2 / "tool1" / "asset.json").write_text(json.dumps(
        {"asset_id": "sciasset://2026ZD123456/earth/tool/tool1", "asset_type": "tool",
         "name": "tool1", "asset_version": "1.0.0", "spec": {"artifacts": []}}, ensure_ascii=False),
        encoding="utf-8")
    (plain2 / "tool1" / "run.sh").write_text("DATA=/opt/ai-mineral/x\n", encoding="utf-8")
    out_p = tmp / "plain2.json"
    run([str(SCRIPTS / "check_selfcontained.py"), "--package", str(plain2), "--json-out", str(out_p)], tmp)
    rep_p = json.loads(out_p.read_text(encoding="utf-8"))
    # 文档 token 截断：`.tar.sha256` 不得被截成 `.tar.sh`，`.shp` 不得被截成 `.sh`
    filex = tmp / "t11" / "filex"
    fasset = filex / "assetA"
    (fasset / "img").mkdir(parents=True, exist_ok=True)
    (fasset / "asset.json").write_text(json.dumps(
        {"asset_id": "sciasset://2026ZD123456/earth/environment/filex", "asset_type": "environment",
         "name": "filex", "asset_version": "1.0.0", "spec": {"artifacts": []}}, ensure_ascii=False),
        encoding="utf-8")
    (fasset / "img" / "m-image.tar.sha256").write_text("deadbeef\n", encoding="utf-8")
    (fasset / "img" / "deposit.shp").write_text("x\n", encoding="utf-8")
    (fasset / "readme.md").write_text(
        "镜像摘要见 `img/m-image.tar.sha256`，矢量见 `img/deposit.shp`，脚本见 `img/run.sh`\n", encoding="utf-8")
    out_f = tmp / "filex.json"
    run([str(SCRIPTS / "probe_package.py"), "--package", str(filex), "--evaluate",
         "--json-out", str(out_f)], tmp)
    miss = (json.loads(out_f.read_text(encoding="utf-8"))["checks"]
            ["doc_refs_profile"]["missing"].get("assetA") or [])
    record("负例（正则回归）：.tar.sha256 / .shp 不被截断成 .tar.sh / .sh，只有真的缺的 run.sh 报缺失",
           miss == ["img/run.sh"], f"missing={miss}")

    record("负例：没有容器证据时 /opt 绝对路径仍判越界",
           rep_p["results"][0]["violation_count"] == 1, f"vc={rep_p['results'][0]['violation_count']}")


# --------------------------------------------------------------------------- T12 批次目录规范
def t12_run_id(tmp: Path) -> None:
    print("\n[12/19] new_run.py / work_copy.py — 批次目录 run-[YYYYMMDD]-[hhmmss]")
    d = tmp / "t12"
    pkg = d / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "a.txt").write_text("a\n", encoding="utf-8")

    out = d / "run.json"
    proc = run([str(SCRIPTS / "new_run.py"), "--package", str(pkg), "--json-out", str(out)], d)
    payload = json.loads(out.read_text(encoding="utf-8"))
    rid = payload["run_id"]
    record("正例：new_run 生成的批次目录名符合 run-[YYYYMMDD]-[hhmmss]",
           proc.returncode == 0 and re.fullmatch(r"run-\d{8}-\d{6}", rid) is not None
           and (pkg / "asset-runs" / rid).is_dir(),
           f"run_id={rid}")

    proc = run([str(SCRIPTS / "new_run.py"), "--package", str(pkg),
                "--at", "2026-11-14T10:55:45", "--print"], d)
    record("正例：--at 可指定时间（补录/复现）",
           proc.returncode == 0 and proc.stdout.strip().endswith("run-20261114-105545"),
           f"out={proc.stdout.strip()}")

    proc = run([str(SCRIPTS / "work_copy.py"), "init", str(pkg), "--work-root", str(d / "w")], d)
    newdirs = [p.name for p in (pkg / "asset-runs").iterdir() if re.fullmatch(r"run-\d{8}-\d{6}", p.name)]
    record("正例：work_copy 省略 --run-id 时自动生成规范名",
           proc.returncode == 0 and len(newdirs) >= 1, f"dirs={newdirs[:3]}")

    proc = run([str(SCRIPTS / "work_copy.py"), "init", str(pkg), "--run-id", "run-20261113-verify-b5",
                "--work-root", str(d / "w")], d)
    record("负例：非规范 run-id 被拒（提示加 --allow-nonstandard）",
           proc.returncode != 0 and "run-[YYYYMMDD]-[hhmmss]" in (proc.stderr or ""),
           f"rc={proc.returncode}")

    proc = run([str(SCRIPTS / "work_copy.py"), "init", str(pkg), "--run-id", "run-20261113-verify-b5",
                "--allow-nonstandard", "--work-root", str(d / "w")], d)
    record("正例：历史批次加 --allow-nonstandard 可放行", proc.returncode == 0, f"rc={proc.returncode}")


# --------------------------------------------------------------------------- T13 外部资产挂载
def t13_mount_external(tmp: Path) -> None:
    print("\n[13/19] mount_external.py — 先同包解析，包外复制进副本，再按 target_path 建软链")
    d = tmp / "t13"
    copy_root = d / ".asset-work" / "run-x"

    def put_asset(slug: str, aid: str, ver: str = "1.0.0") -> pathlib.Path:
        a = copy_root / slug
        (a / "payload").mkdir(parents=True, exist_ok=True)
        (a / "asset.json").write_text(json.dumps({"asset_id": aid, "asset_version": ver},
                                                 ensure_ascii=False), encoding="utf-8")
        (a / "payload" / "x.csv").write_text("x", encoding="utf-8")
        return a

    a_asset = put_asset("asset-a", "sciasset://2026ZD123456/earth/skill/asset-a")
    put_asset("asset-b", "sciasset://2026ZD123456/earth/data/asset-b")
    (a_asset / "external_assets.json").write_text(json.dumps({
        "schema_version": "1.0.0",
        "assets": [{"asset_id": "sciasset://2026ZD123456/earth/data/asset-b", "asset_version": "1.0.0",
                    "target_path": "ref/path/b", "required": True}]}, ensure_ascii=False), encoding="utf-8")

    out = d / "m.json"
    proc = run([str(SCRIPTS / "mount_external.py"), "--copy-root", str(copy_root),
                "--json-out", str(out)], d)
    rep = json.loads(out.read_text(encoding="utf-8"))
    link = a_asset / "ref" / "path" / "b"
    record("正例：同包资产直接命中，按 target_path 建软链（父目录自动创建）",
           proc.returncode == 0 and rep["status"] == "complete" and link.is_symlink()
           and link.resolve() == (copy_root / "asset-b").resolve(),
           f"rc={proc.returncode} link={link.is_symlink()}")

    proc = run([str(SCRIPTS / "mount_external.py"), "--copy-root", str(copy_root),
                "--json-out", str(out)], d)
    rep = json.loads(out.read_text(encoding="utf-8"))
    record("正例：重复执行幂等（软链不重复建、不报错）",
           proc.returncode == 0 and any("已存在" in (l.get("note") or "") for l in rep["links"]),
           f"links={[l.get('note') for l in rep['links']]}")

    # 包外资产：按优先级找到后复制进副本，再建软链
    c_asset = put_asset("asset-c", "sciasset://2026ZD123456/earth/skill/asset-c")
    (c_asset / "external_assets.json").write_text(json.dumps({
        "schema_version": "1.0.0",
        "assets": [{"asset_id": "sciasset://2026ZD123456/earth/data/ext-c", "asset_version": "2.0.0",
                    "target_path": "data/ext", "required": True}]}, ensure_ascii=False), encoding="utf-8")
    origin = d / "origin-pkg"
    origin.mkdir(parents=True, exist_ok=True)
    outside = d / "outside" / "ext-c"
    (outside / "data").mkdir(parents=True, exist_ok=True)
    (outside / "asset.json").write_text(json.dumps(
        {"asset_id": "sciasset://2026ZD123456/earth/data/ext-c", "asset_version": "2.0.0"},
        ensure_ascii=False), encoding="utf-8")
    (outside / "data" / "y.csv").write_text("y", encoding="utf-8")
    proc = run([str(SCRIPTS / "mount_external.py"), "--copy-root", str(copy_root),
                "--origin-package", str(origin), "--json-out", str(out)], d)
    rep = json.loads(out.read_text(encoding="utf-8"))
    link_c = c_asset / "data" / "ext"
    record("正例：包外资产按优先级找到后复制进副本，并软链到 target_path",
           proc.returncode == 0 and (copy_root / "ext-c" / "asset.json").is_file()
           and link_c.is_symlink() and link_c.resolve() == (copy_root / "ext-c").resolve(),
           f"rc={proc.returncode} copied={(copy_root / 'ext-c').is_dir()} link={link_c.is_symlink()}")

    # 找不到：只在报告里说明，不中断
    (c_asset / "external_assets.json").write_text(json.dumps({
        "schema_version": "1.0.0",
        "assets": [{"asset_id": "sciasset://2026ZD123456/earth/data/nope", "asset_version": "1.0.0",
                    "target_path": "data/none", "required": True}]}, ensure_ascii=False), encoding="utf-8")
    proc = run([str(SCRIPTS / "mount_external.py"), "--copy-root", str(copy_root), "--json-out", str(out)], d)
    rep = json.loads(out.read_text(encoding="utf-8"))
    record("负例：找不到的外部资产只记 missing（退出码仍 0、status=incomplete）",
           proc.returncode == 0 and rep["status"] == "incomplete"
           and any(m["asset_id"].endswith("/nope") for m in rep["missing"]),
           f"rc={proc.returncode} status={rep.get('status')}")

    empty = d / "empty"
    (empty / "x").mkdir(parents=True)
    proc = run([str(SCRIPTS / "mount_external.py"), "--copy-root", str(empty)], d)
    record("正例：没有任何 external_assets.json 时无需挂载、退出码 0",
           proc.returncode == 0 and "无需挂载" in proc.stdout, f"rc={proc.returncode}")


# --------------------------------------------------------------------------- T14 挂载副本不参与校验
def t14_mounted_not_checked(tmp: Path) -> None:
    print("\n[14/19] 挂载进来的外部资产不能被当成包内资产（_layout 标记）")
    d = tmp / "t14"
    pkg = d / "pkg"
    for slug, aid, typ in (("asset-a", "sciasset://2026ZD123456/earth/tool/asset-a", "tool"),
                           ("asset-b", "sciasset://2026ZD123456/earth/data/asset-b", "data")):
        (pkg / slug).mkdir(parents=True)
        (pkg / slug / "asset.json").write_text(json.dumps(
            {"schema_version": "1.0.0", "asset_id": aid, "asset_type": typ, "name": slug,
             "description": "示例", "asset_version": "1.0.0", "lifecycle": "validated",
             "domains": [{"id": "domain:earth-science"}],
             "task_classes": [{"id": "operation:analysis"}],
             "provenance": {"created_time": "2026-01-01T00:00:00Z",
                            "provider": [{"name": "p", "role": "author"}],
                            "source": {"kind": "original"}},
             "governance": {"license": "MIT", "access": "public", "risk_level": "low",
                            "privacy": "none", "usage_constraints": []},
             "spec": {"metadata": {"interface_type": "function"} if typ == "tool" else {"format": ["csv"]},
                      "artifacts": [{"uri": "readme.md", "media_type": "text/markdown"}],
                      "domain_attributes": {}, "asset_protocol": {}}}, ensure_ascii=False),
            encoding="utf-8")
        (pkg / slug / "readme.md").write_text("# x\n", encoding="utf-8")
    # 模拟 mount_external 复制进来的外部资产
    ext = pkg / "asset-ext"
    ext.mkdir()
    (ext / "asset.json").write_text(json.dumps(
        {"asset_id": "sciasset://2026ZD123456/earth/data/asset-ext", "asset_type": "data",
         "name": "ext", "asset_version": "2.0.0"}, ensure_ascii=False), encoding="utf-8")
    (ext / ".mounted-external.json").write_text(json.dumps(
        {"asset_id": "sciasset://2026ZD123456/earth/data/asset-ext"}, ensure_ascii=False), encoding="utf-8")

    out = d / "env.json"
    run([str(SCRIPTS / "validate_envelope.py"), str(pkg), "--json-out", str(out)], d)
    env = json.loads(out.read_text(encoding="utf-8"))
    record("正例：包络只校验包内 2 个资产（挂载副本被跳过）",
           env["summary"]["assets"] == 2, f"assets={env['summary']['assets']}")

    out2 = d / "sc.json"
    run([str(SCRIPTS / "check_selfcontained.py"), "--package", str(pkg), "--json-out", str(out2)], d)
    sc = json.loads(out2.read_text(encoding="utf-8"))
    record("正例：自包含只校验包内 2 个资产", sc["assets_checked"] == 2,
           f"checked={sc['assets_checked']}")

    out3 = d / "ids.json"
    run([str(SCRIPTS / "asset_ids.py"), "--package", str(pkg), "--json-out", str(out3)], d)
    ids = json.loads(out3.read_text(encoding="utf-8"))
    record("正例：资产ID 映射只含包内资产，挂载副本不进清单",
           ids["assets"] == 2 and all("asset-ext" not in str(r.get("asset_id")) for r in ids["rows"]),
           f"assets={ids['assets']}")

    out4 = d / "sage.json"
    run([str(SCRIPTS / "sage_probe.py"), "detect", str(pkg), "--json-out", str(out4)], d)
    sage = json.loads(out4.read_text(encoding="utf-8"))
    record("正例：Sage 判定也只看包内资产类型", sage.get("asset_types") == ["data", "tool"],
           f"types={sage.get('asset_types')}")


# --------------------------------------------------------------------------- T15 skill 测试说明
def t15_skill_spec(tmp: Path) -> None:
    print("\n[15/19] check_skill_spec.py — skill 必须上 agent：md 里写清 harness/model/任务/门槛")
    d = tmp / "t15"
    pkg = d / "pkg"

    def skill(slug: str, aid: str) -> pathlib.Path:
        a = pkg / slug
        (a / "validate").mkdir(parents=True)
        (a / "asset.json").write_text(json.dumps(
            {"asset_id": aid, "asset_type": "skill", "name": slug, "asset_version": "1.0.0"},
            ensure_ascii=False), encoding="utf-8")
        (a / "validate" / "validate.py").write_text("print('ok')\n", encoding="utf-8")
        return a

    a = skill("skill-a", "sciasset://2026ZD123456/earth/skill/skill-a")
    tool = pkg / "tool-b"
    (tool / "validate").mkdir(parents=True)
    (tool / "asset.json").write_text(json.dumps(
        {"asset_id": "sciasset://2026ZD123456/earth/tool/tool-b", "asset_type": "tool"},
        ensure_ascii=False), encoding="utf-8")

    (a / "validate" / "validate.py").write_text("print('ok')\n", encoding="utf-8")
    (a / "validate" / "cases" / "C01").mkdir(parents=True)
    (a / "validate" / "cases" / "C01" / "points.csv").write_text("x,y,value\n0,0,10\n", encoding="utf-8")
    (a / "validate" / "cases" / "C01" / "targets.csv").write_text("x,y\n0,0\n", encoding="utf-8")
    good = """# 测试说明

## Agent
- 建议 agent harness：DSH（DeepSeek Harness）Web 会话
- 装载方式：把资产目录放进 skills 位置，或给出 SKILL.md 路径
- 建议 agent model：DS-4.1；是否必须特定模型：否

## 测试任务
- 任务：让 agent 使用本 skill 完成插值
- 输入：validate/cases/C01/points.csv 与 targets.csv
- 期望输出：interpolated.csv（x,y,value_est,nearest_dist）

## 通过门槛
- 指标：value_est_exact = 0
- 验证脚本：validate/validate.py --case C01-hits --artifact <agent 产物目录>（直接判产物，不重跑脚本）
"""
    spec = a / "validate" / "readme.md"
    spec.write_text(good, encoding="utf-8")
    out = d / "spec.json"
    proc = run([str(SCRIPTS / "check_skill_spec.py"), "--package", str(pkg), "--check",
                "--json-out", str(out)], d)
    rep = json.loads(out.read_text(encoding="utf-8"))
    out5 = d / "sage.json"
    run([str(SCRIPTS / "sage_probe.py"), "detect", str(pkg), "--json-out", str(out5)], d)
    sage = json.loads(out5.read_text(encoding="utf-8"))
    record("正例：包内有 skill 资产时 Sage A1.5 判为适用（不是 not_applicable）",
           sage["needed"] is True and any("skill" in r for r in sage["reasons"]),
           f"needed={sage.get('needed')}")

    record("正例：md 三节齐全（Agent 含 harness+model）时通过（tool 资产不查）",
           proc.returncode == 0 and rep["skills"] == 1 and rep["failed"] == 0
           and rep["spec_format"] == "markdown",
           f"rc={proc.returncode} skills={rep.get('skills')}")

    spec.unlink()
    proc = run([str(SCRIPTS / "check_skill_spec.py"), "--package", str(pkg), "--check"], d)
    record("负例：缺 validate/readme.md 被拒", proc.returncode == 1 and "缺少验证说明" in proc.stderr,
           f"rc={proc.returncode}")

    (a / "validate" / "test-spec.md").write_text(good, encoding="utf-8")
    proc = run([str(SCRIPTS / "check_skill_spec.py"), "--package", str(pkg), "--check"], d)
    record("负例：用旧的 test-spec.md 文件名被拒（要求改名 readme.md）",
           proc.returncode == 1 and "readme.md" in proc.stderr, f"rc={proc.returncode}")
    (a / "validate" / "test-spec.md").unlink()

    spec.write_text(good.replace("## Agent\n- 建议 agent harness：DSH（DeepSeek Harness）Web 会话\n- 装载方式：把资产目录放进 skills 位置，或给出 SKILL.md 路径\n- 建议 agent model：DS-4.1；是否必须特定模型：否\n",
                                 "## Agent\n- 直接跑 validate/validate.py 即可\n"), encoding="utf-8")
    proc = run([str(SCRIPTS / "check_skill_spec.py"), "--package", str(pkg), "--check"], d)
    record("负例：Agent 一节没写 agent 环境（只说跑脚本）被拒",
           proc.returncode == 1 and "agent harness" in proc.stderr, f"rc={proc.returncode}")

    spec.write_text(good.replace("- 建议 agent model：DS-4.1；是否必须特定模型：否\n", ""), encoding="utf-8")
    proc = run([str(SCRIPTS / "check_skill_spec.py"), "--package", str(pkg), "--check"], d)
    record("负例：Agent 一节没写 agent model 被拒",
           proc.returncode == 1 and "agent model" in proc.stderr, f"rc={proc.returncode}")

    spec.write_text(good.replace("- 输入：validate/cases/C01/points.csv 与 targets.csv\n", "")                         .replace("- 期望输出：interpolated.csv（x,y,value_est,nearest_dist）\n",
                                 "- 跑完就行\n"), encoding="utf-8")
    proc = run([str(SCRIPTS / "check_skill_spec.py"), "--package", str(pkg), "--check"], d)
    record("负例：测试任务没写清输入/输出被拒",
           proc.returncode == 1 and ("输入" in proc.stderr and "输出" in proc.stderr),
           f"rc={proc.returncode}")

    bad_gate = good.split("## 通过门槛")[0] + "## 通过门槛\n- 看着对就行\n"
    spec.write_text(bad_gate, encoding="utf-8")
    proc = run([str(SCRIPTS / "check_skill_spec.py"), "--package", str(pkg), "--check"], d)
    record("负例：通过门槛既没指标也没验证脚本被拒",
           proc.returncode == 1 and "通过门槛" in proc.stderr, f"rc={proc.returncode}")

    spec.write_text(good.replace("- 输入：validate/cases/C01/points.csv 与 targets.csv",
                                 "- 输入：validate/cases/C09/points.csv 与 targets.csv"), encoding="utf-8")
    proc = run([str(SCRIPTS / "check_skill_spec.py"), "--package", str(pkg), "--check"], d)
    record("负例：文档引用的 validate/ 路径在包内不存在被拒（属于没法验证）",
           proc.returncode == 1 and "不存在" in proc.stderr, f"rc={proc.returncode}")

    spec.write_text(good.replace("--artifact <agent 产物目录>（直接判产物，不重跑脚本）",
                                 "--out-dir <dir>"), encoding="utf-8")
    proc = run([str(SCRIPTS / "check_skill_spec.py"), "--package", str(pkg), "--check"], d)
    record("提示：门槛脚本看不出在判 agent 产物时给告警（不阻断）",
           proc.returncode == 0 and "artifact" in proc.stderr, f"rc={proc.returncode}")

    spec.write_text("# 测试说明\n\n## Agent\n- 建议 harness：<如 DSH>\n", encoding="utf-8")
    proc = run([str(SCRIPTS / "check_skill_spec.py"), "--package", str(pkg), "--check"], d)
    record("负例：只有模板占位符被拒",
           proc.returncode == 1 and "缺少" in proc.stderr, f"rc={proc.returncode}")


# --------------------------------------------------------------------------- T16 skill 独立 CLI 验收
def t16_skill_agent_cli(tmp: Path) -> None:
    print("\n[16/19] run_skill_agent.py — skill 验收走独立 CLI 进程（不在会话内跑）")
    d = tmp / "t16"
    skill = d / "pkg" / "skill-a"
    (skill / "validate" / "cases" / "C01").mkdir(parents=True)
    (skill / "asset.json").write_text(json.dumps(
        {"asset_id": "sciasset://2026ZD123456/earth/skill/skill-a", "asset_type": "skill",
         "name": "skill-a", "asset_version": "1.0.0"}, ensure_ascii=False), encoding="utf-8")
    (skill / "validate" / "validate.py").write_text("import sys; sys.exit(0)\n", encoding="utf-8")
    (skill / "validate" / "cases" / "C01" / "points.csv").write_text("x,y,value\n0,0,1\n", encoding="utf-8")
    (skill / "validate" / "readme.md").write_text("""# 验证说明

## Agent
- 建议 agent harness：DSH（DeepSeek Harness）Web 会话
- 建议 agent model：DS-4.1；是否必须特定模型：否

## 测试任务
- 任务：让 agent 使用本 skill 完成插值
- 输入：validate/cases/C01/points.csv
- 期望输出：interpolated.csv
- 提示词模板：

```
用本 skill 对 validate/cases/C01/points.csv 做插值，产物写到指定目录。
```

## 通过门槛
- 指标：value_est_exact = 0
- 验证脚本：validate/validate.py --case C01-hits --artifact <agent 产物目录>
""", encoding="utf-8")

    proc = run([str(SCRIPTS / "run_skill_agent.py"), "--list-harnesses"], d)
    record("正例：--list-harnesses 列出预设与本机安装情况",
           proc.returncode == 0 and "dsh" in proc.stdout and "codex" in proc.stdout
           and "harness-cmd" in proc.stdout, f"rc={proc.returncode}")

    proc = run([str(SCRIPTS / "run_skill_agent.py"), "--skill", str(skill),
                "--out-dir", str(d / "agent"), "--dry-run"], d)
    record("正例：默认按 readme 建议解析 harness（DSH），provider 用 harness 默认、不覆盖模型",
           proc.returncode == 0 and "dsh" in proc.stdout and "harness-default" in proc.stdout
           and "(harness 默认)" in proc.stdout, f"rc={proc.returncode}")

    proc = run([str(SCRIPTS / "run_skill_agent.py"), "--skill", str(skill),
                "--out-dir", str(d / "agent-c"), "--harness", "cursor", "--dry-run"], d)
    rep = json.loads(proc.stdout)
    record("正例：指定的 harness 未安装时退用已安装的，并说明原因",
           proc.returncode == 0 and rep["harness"] != "cursor" and "未安装" in rep["reason"],
           f"harness={rep.get('harness')} reason={rep.get('reason')}")

    env = dict(os.environ)
    env.pop("SAGE_BASE_URL", None)
    proc = run([str(SCRIPTS / "run_skill_agent.py"), "--skill", str(skill),
                "--out-dir", str(d / "agent-s0"), "--backend", "sage"], d, env_extra=env)
    record("负例：Sage 测试缺 SAGE_* 配置时明确失败，不降级用默认后端",
           proc.returncode == 2 and "SAGE_BASE_URL" in proc.stderr, f"rc={proc.returncode}")

    env2 = dict(os.environ)
    env2.update({"SAGE_BASE_URL": "https://sage.example/v1", "SAGE_MODEL": "Sage-A1.5",
                 "SAGE_API_KEY": "k"})
    proc = run([str(SCRIPTS / "run_skill_agent.py"), "--skill", str(skill),
                "--out-dir", str(d / "agent-sage"), "--backend", "sage", "--dry-run"], d, env_extra=env2)
    rep = json.loads(proc.stdout)
    record("正例：Sage 后端从 SAGE_* 取地址/模型，harness 不变",
           proc.returncode == 0 and rep["model"] == "Sage-A1.5"
           and "base_url" in rep["sage_env_used"], f"model={rep.get('model')}")

    proc = run([str(SCRIPTS / "run_skill_agent.py"), "--skill", str(skill),
                "--out-dir", str(d / "agent-x"), "--harness-cmd", "myagent run {prompt}", "--dry-run"], d)
    rep = json.loads(proc.stdout)
    record("正例：预设之外的 harness 用 --harness-cmd 就地固化",
           proc.returncode == 0 and rep["harness"] == "custom" and rep["argv"][:2] == ["myagent", "run"],
           f"argv={rep.get('argv')}")

    bad = d / "pkg" / "no-spec"
    (bad / "validate").mkdir(parents=True)
    (bad / "asset.json").write_text("{}", encoding="utf-8")
    proc = run([str(SCRIPTS / "run_skill_agent.py"), "--skill", str(bad),
                "--out-dir", str(d / "x")], d)
    record("负例：缺 validate/readme.md 时直接报错（退出码 2）",
           proc.returncode == 2, f"rc={proc.returncode}")


# --------------------------------------------------------------------------- T17 可复现性
def t17_reproducible(tmp: Path) -> None:
    print("\n[17/19] probe_package.py — 事实恒定 + P0×4/P1×5 固定判级")
    d = tmp / "t17"

    # ① 结构不合规 + 资产不完整 + 无验证入口
    bad_pkg = d / "bad"
    (bad_pkg / "stray" / "sub").mkdir(parents=True)
    (bad_pkg / "a1" / "payload").mkdir(parents=True)
    (bad_pkg / "a1" / "asset.json").write_text(json.dumps({
        "asset_id": "sciasset://2026ZD123456/earth/data/a1", "asset_type": "data",
        "name": "a1", "asset_version": "1.0.0",
        "spec": {"artifacts": [{"uri": "payload/missing.csv", "media_type": "text/csv"}]}},
        ensure_ascii=False), encoding="utf-8")
    out = d / "bad.json"
    hashes = []
    for _ in range(2):
        proc = run([str(SCRIPTS / "probe_package.py"), "--package", str(bad_pkg),
                    "--evaluate", "--json-out", str(out)], d)
        hashes.append(proc.stdout.split("指纹 ")[-1].strip())
    record("正例：两次探测事实指纹相同（确定性）", len(set(hashes)) == 1 and len(hashes[0]) >= 8, f"hashes={hashes}")

    rep = json.loads(out.read_text(encoding="utf-8"))["verdict"]
    ids = {f["check_id"]: f["level"] for f in rep["findings"]}
    record("正例：非资产目录 → P0-STRUCT；实体缺失 → P0-COMPLETE；无验证入口 → P0-VERIFY",
           ids.get("P0-STRUCT") == "P0" and ids.get("P0-COMPLETE") == "P0"
           and ids.get("P0-VERIFY") == "P0", f"ids={ids}")

    cov = {c["check_id"]: c for c in rep["coverage"]}
    record("正例：覆盖表对不适用项标 na 并写理由（事实类错误需人工）",
           cov["P1-DOC-FACT"]["status"] == "na" and cov["P1-DOC-FACT"]["na_reason"],
           f"status={cov['P1-DOC-FACT'].get('status')}")

    # ② 合规包：结构、实体、入口齐备
    good = d / "good"
    (good / "b1" / "validate").mkdir(parents=True)
    (good / "b1" / "payload").write_text("x,y\n0,0\n", encoding="utf-8") if False else None
    (good / "b1" / "payload.csv").write_text("x,y\n0,0\n", encoding="utf-8")
    (good / "b1" / "validate" / "cases.json").write_text(
        json.dumps({"cases": [{"id": "C01"}]}, ensure_ascii=False), encoding="utf-8")
    env_good = minimal_envelope()
    env_good["asset_id"] = "sciasset://2026ZD123456/earth/data/b1"
    env_good["name"] = "b1"
    env_good["spec"]["artifacts"] = [{"uri": "payload.csv", "media_type": "text/csv"}]
    (good / "b1" / "asset.json").write_text(json.dumps(env_good, ensure_ascii=False), encoding="utf-8")
    out2 = d / "good.json"
    run([str(SCRIPTS / "probe_package.py"), "--package", str(good), "--evaluate", "--json-out", str(out2)], d)
    rep2 = json.loads(out2.read_text(encoding="utf-8"))["verdict"]
    cov_good = {c["check_id"]: c for c in rep2["coverage"]}
    record("正例：资产有 validate/cases.json 时 P1-VERIFY-DOC 判 pass（不因字段名不匹配误判 na）",
           cov_good["P1-VERIFY-DOC"]["status"] == "pass", f"status={cov_good['P1-VERIFY-DOC']}")

    # 凭据/密钥：文件名与内容两种形态都判 P0-SECRET
    sec_pkg = d / "sec"
    (sec_pkg / "c1").mkdir(parents=True)
    (sec_pkg / "c1" / "asset.json").write_text(json.dumps(
        {"asset_id": "sciasset://2026ZD123456/earth/data/c1", "asset_type": "data",
         "name": "c1", "asset_version": "1.0.0", "spec": {"artifacts": []}}, ensure_ascii=False),
        encoding="utf-8")
    (sec_pkg / "c1" / ".credentials.yaml").write_text(
        "INKSTONE_API_KEY: sk-live-abcdefghijklmnop123456\n", encoding="utf-8")
    out_s1 = d / "sec1.json"
    run([str(SCRIPTS / "probe_package.py"), "--package", str(sec_pkg), "--evaluate",
         "--json-out", str(out_s1)], d)
    ids_s1 = {f["check_id"]: f["level"] for f in json.loads(out_s1.read_text(encoding="utf-8"))["verdict"]["findings"]}
    record("负例：包内出现 .credentials.yaml → P0-SECRET（阻断）", ids_s1.get("P0-SECRET") == "P0", f"ids={ids_s1}")

    (sec_pkg / "c1" / ".credentials.yaml").unlink()
    (sec_pkg / "c1" / "run.py").write_text('K = "sk-live-abcdefghijklmnop123456"\n', encoding="utf-8")
    out_s2 = d / "sec2.json"
    run([str(SCRIPTS / "probe_package.py"), "--package", str(sec_pkg), "--evaluate",
         "--json-out", str(out_s2)], d)
    ids_s2 = {f["check_id"]: f["level"] for f in json.loads(out_s2.read_text(encoding="utf-8"))["verdict"]["findings"]}
    record("负例：正文里硬编码 sk- 密钥 → P0-SECRET", ids_s2.get("P0-SECRET") == "P0", f"ids={ids_s2}")

    (sec_pkg / "c1" / "run.py").unlink()
    (sec_pkg / "c1" / "readme.md").write_text("用环境变量 INKSTONE_API_KEY 注入，不落盘\n", encoding="utf-8")
    out_s3 = d / "sec3.json"
    run([str(SCRIPTS / "probe_package.py"), "--package", str(sec_pkg), "--evaluate",
         "--json-out", str(out_s3)], d)
    ids_s3 = {f["check_id"] for f in json.loads(out_s3.read_text(encoding="utf-8"))["verdict"]["findings"]}
    record("正例：只提环境变量名、无密钥值时不误报 P0-SECRET", "P0-SECRET" not in ids_s3, f"ids={ids_s3}")

    # 误报回归（真实包反馈）：
    # ① `task-01-without-knowledge` 里的 `sk-01-without-…` 不是密钥
    (sec_pkg / "c1" / "run.py").write_text(
        'print("--output reports/task-01-without-knowledge.html")\n', encoding="utf-8")
    out_s4 = d / "sec4.json"
    run([str(SCRIPTS / "probe_package.py"), "--package", str(sec_pkg), "--evaluate",
         "--json-out", str(out_s4)], d)
    ids_s4 = {f["check_id"] for f in json.loads(out_s4.read_text(encoding="utf-8"))["verdict"]["findings"]}
    record("负例（误报回归）：文档里的 task-01-without-knowledge 不被当成 sk- 密钥",
           "P0-SECRET" not in ids_s4, f"ids={ids_s4}")

    # ② `LLM_API_KEY=`（空值）跨行不匹配下一行变量名
    (sec_pkg / "c1" / "run.py").unlink()
    (sec_pkg / "c1" / "container_check.sh").write_text(
        'LLM_API_KEY=\nMESSAGE_UPDATE_URL=http://x\n', encoding="utf-8")
    out_s5 = d / "sec5.json"
    run([str(SCRIPTS / "probe_package.py"), "--package", str(sec_pkg), "--evaluate",
         "--json-out", str(out_s5)], d)
    ids_s5 = {f["check_id"] for f in json.loads(out_s5.read_text(encoding="utf-8"))["verdict"]["findings"]}
    record("负例（误报回归）：空值 LLM_API_KEY= 跨行不吃下一行 MESSAGE_UPDATE_URL",
           "P0-SECRET" not in ids_s5, f"ids={ids_s5}")
    (sec_pkg / "c1" / "container_check.sh").unlink()

    # ③ 文档以包内子仓库为基准：payload/source/RUN/x.py 存在即不判缺失；真缺失照报
    dr = d / "docref"
    (dr / "a1" / "payload" / "source" / "RUN").mkdir(parents=True)
    (dr / "a1" / "asset.json").write_text(json.dumps(
        {"asset_id": "sciasset://2026ZD123456/earth/tool/a1", "asset_type": "tool",
         "name": "a1", "asset_version": "1.0.0", "spec": {"artifacts": []}}, ensure_ascii=False),
        encoding="utf-8")
    (dr / "a1" / "payload" / "source" / "RUN" / "run_agent.py").write_text("x\n", encoding="utf-8")
    (dr / "a1" / "readme.md").write_text(
        "入口 `RUN/run_agent.py`；链接 [说明](payload/source/RUN/run_agent.py)；入口 `RUN/ghost.py`\n",
        encoding="utf-8")
    out_d = d / "docref.json"
    run([str(SCRIPTS / "probe_package.py"), "--package", str(dr), "--json-out", str(out_d)], d)
    miss_d = json.loads(out_d.read_text(encoding="utf-8"))["checks"]["doc_refs_profile"]["missing"].get("a1") or []
    record("负例（误报回归）：以子仓库为基准的路径与链接文字不误报，真缺失的 ghost.py 仍报",
           miss_d == ["RUN/ghost.py"], f"missing={miss_d}")

    # Sage 附加测试：配置缺失/未跑通 → P0-SAGE；跑通 → 无
    sage_run = d / "sage_run"
    sage_run.mkdir()
    (sage_run / "sage-need.json").write_text(json.dumps({"needed": True, "reasons": ["skill 资产"]},
                                                        ensure_ascii=False), encoding="utf-8")
    (sage_run / "sage-config.json").write_text(json.dumps(
        {"status": "missing_config", "missing": ["model", "api_key"], "api_key_present": False},
        ensure_ascii=False), encoding="utf-8")
    out_g1 = d / "sage1.json"
    run([str(SCRIPTS / "probe_package.py"), "--package", str(sec_pkg), "--run-dir", str(sage_run),
         "--evaluate", "--json-out", str(out_g1)], d)
    f_g1 = [f for f in json.loads(out_g1.read_text(encoding="utf-8"))["verdict"]["findings"]
            if f["check_id"] == "P0-SAGE"]
    record("负例：Sage 需要测但配置缺失 → P0-SAGE，并带端砚平台指引",
           bool(f_g1) and f_g1[0]["level"] == "P0" and "端砚平台" in f_g1[0]["detail"]["hint"],
           f"detail={f_g1[0]['detail'] if f_g1 else None}")

    (sage_run / "sage-config.json").write_text(json.dumps(
        {"status": "configured", "api_key_present": True, "probe_ok": True}, ensure_ascii=False),
        encoding="utf-8")
    (sage_run / "sage-a1.5-report.json").write_text(json.dumps(
        {"status": "completed", "runs": [{"asset": "c1", "ok": True}]}, ensure_ascii=False),
        encoding="utf-8")
    out_g2 = d / "sage2.json"
    run([str(SCRIPTS / "probe_package.py"), "--package", str(sec_pkg), "--run-dir", str(sage_run),
         "--evaluate", "--json-out", str(out_g2)], d)
    rep_g2 = json.loads(out_g2.read_text(encoding="utf-8"))
    ids_g2 = {f["check_id"] for f in rep_g2["verdict"]["findings"]}
    cov_g2 = {c["check_id"]: c["status"] for c in rep_g2["verdict"]["coverage"]}
    record("正例：Sage 已跑通时不判 P0-SAGE，覆盖表记 pass",
           "P0-SAGE" not in ids_g2 and cov_g2["P0-SAGE"] == "pass", f"ids={ids_g2}")

    (sage_run / "sage-need.json").write_text(json.dumps({"needed": False}, ensure_ascii=False), encoding="utf-8")
    out_g3 = d / "sage3.json"
    run([str(SCRIPTS / "probe_package.py"), "--package", str(sec_pkg), "--run-dir", str(sage_run),
         "--evaluate", "--json-out", str(out_g3)], d)
    cov_g3 = {c["check_id"]: c for c in json.loads(out_g3.read_text(encoding="utf-8"))["verdict"]["coverage"]}
    record("正例：不需要 Sage 的包把 P0-SAGE 标 na 并写理由",
           cov_g3["P0-SAGE"]["status"] == "na" and cov_g3["P0-SAGE"]["na_reason"],
           f"status={cov_g3['P0-SAGE']['status']}")

    # 包络错误必须真的被判出来（防"summary 说有错、errors 为空"的假阴性）
    bad_env = d / "badenv"
    (bad_env / "e1").mkdir(parents=True)
    (bad_env / "e1" / "asset.json").write_text(json.dumps({
        "schema_version": "1.0.0", "asset_id": "sciasset://2026ZD123456/earth/data/e1",
        "asset_type": "data", "name": "e1", "asset_version": "1.0.0",
        "spec": {"artifacts": []}}, ensure_ascii=False), encoding="utf-8")
    out_e = d / "badenv.json"
    run([str(SCRIPTS / "probe_package.py"), "--package", str(bad_env), "--evaluate",
         "--json-out", str(out_e)], d)
    rep_e = json.loads(out_e.read_text(encoding="utf-8"))
    ids_e = {f["check_id"]: f for f in rep_e["verdict"]["findings"]}
    record("负例：包络缺字段（provenance/task_classes）判 P0-ENVELOPE 且带字段路径",
           "P0-ENVELOPE" in ids_e and "P0" == ids_e["P0-ENVELOPE"]["level"]
           and bool(rep_e["checks"]["envelope_profile"]["errors"]),
           f"errors={len(rep_e['checks']['envelope_profile']['errors'])}")

    # 格式声明归一化：ATIF 不能被当成 tif（子串误判），geotiff 仍要识别
    sys.path.insert(0, str(SCRIPTS))
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location("_ve", SCRIPTS / "validate_envelope.py")
    _ve = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_ve)
    fam_atif = _ve._declared_families("ATIF")
    fam_gt = _ve._declared_families("geotiff")
    record("负例（误判回归）：metadata.format=ATIF 不被识别成 raster:tiff，geotiff 仍识别为 raster:tiff",
           fam_atif == set() and fam_gt == {"raster:tiff"}, f"ATIF={fam_atif} geotiff={fam_gt}")

    record("正例：合规包 P0/P1 均为 0",
           rep2["summary"]["P0"] == 0 and rep2["summary"]["P1"] == 0,
           f"summary={rep2['summary']}")

    # ③ 文档引用不存在脚本 → P1-DOC-REF
    (good / "b1" / "readme.md").write_text("run `scripts/run_all.py` first\n", encoding="utf-8")
    out3 = d / "doc.json"
    run([str(SCRIPTS / "probe_package.py"), "--package", str(good), "--evaluate", "--json-out", str(out3)], d)
    rep3 = json.loads(out3.read_text(encoding="utf-8"))["verdict"]
    ids3 = {f["check_id"]: f["level"] for f in rep3["findings"]}
    record("正例：文档指向不存在的脚本 → P1-DOC-REF（P1 不是 P0）",
           ids3.get("P1-DOC-REF") == "P1" and "P0-DOC" not in ids3, f"ids={ids3}")

    # ⑤ 包根出现 asset.json → P0-STRUCT（资产必须以子目录存在）
    root_pkg = d / "root_asset"
    (root_pkg / "sub1").mkdir(parents=True)
    for name, path in (("root", root_pkg / "asset.json"), ("sub1", root_pkg / "sub1" / "asset.json")):
        path.write_text(json.dumps({"asset_id": f"sciasset://2026ZD123456/earth/data/{name}",
                                    "asset_type": "data", "name": name, "asset_version": "1.0.0",
                                    "spec": {"artifacts": []}}, ensure_ascii=False), encoding="utf-8")
    out4 = d / "root_asset.json"
    run([str(SCRIPTS / "probe_package.py"), "--package", str(root_pkg), "--evaluate",
         "--json-out", str(out4)], d)
    rep4 = json.loads(out4.read_text(encoding="utf-8"))
    ids4 = {f["check_id"]: f for f in rep4["verdict"]["findings"]}
    record("正例：包根有 asset.json → P0-STRUCT，且包根不被当成资产",
           "P0-STRUCT" in ids4 and ids4["P0-STRUCT"]["scope"] == "package"
           and rep4["checks"]["package_summary"]["asset_dirs"] == ["sub1"],
           f"ids={list(ids4)} asset_dirs={rep4['checks']['package_summary']['asset_dirs']}")

    # ④ 归属：findings 带 scope/assets；报告 P0/P1 行须写归属
    rep_att = json.loads(out3.read_text(encoding="utf-8"))["verdict"]
    f_att = next(f for f in rep_att["findings"] if f["check_id"] == "P1-DOC-REF")
    record("正例：探测结果带归属（scope=asset 且列出资产目录名）",
           f_att.get("scope") == "asset" and "b1" in (f_att.get("assets") or []),
           f"scope={f_att.get('scope')} assets={f_att.get('assets')}")

    run_dir2 = d / "run2"
    run_dir2.mkdir()
    (run_dir2 / "probe-report.json").write_text(json.dumps({"verdict": {"findings": []}}, ensure_ascii=False),
                                                encoding="utf-8")
    (run_dir2 / "inventory.json").write_text(json.dumps(
        {"assets": [{"dir": "b1", "name": "b1 资产"}]}, ensure_ascii=False), encoding="utf-8")
    (run_dir2 / "validation-report.md").write_text(
        "---\nskill_version: x\ngenerated_at: y\n---\n\n# 报告\n\n## 4. 问题清单\n\n"
        "### P0 阻断\n\n| 编号 | 归属 | 问题 |\n|---|---|---|\n"
        "| P0-1 | 包级 | P0-STRUCT 结构不合规 |\n| P0-2 | 未写归属 | P0-VERIFY 缺入口 |\n\n"
        "### P1 重要\n\n| 编号 | 归属 | 问题 |\n|---|---|---|\n"
        "| P1-1 | b1 | P1-DOC-REF 引用缺失 |\n\n### P2 建议\n\n无\n\n## 5. 证据保留口径\n\nx\n",
        encoding="utf-8")
    proc2 = run([str(SCRIPTS / "check_report.py"), "--report", str(run_dir2 / "validation-report.md")], d)
    blob2 = (proc2.stdout or "") + (proc2.stderr or "")
    record("提示：P0/P1 行没写归属（也不含资产名/包级）时报告警",
           "归属" in blob2 and "未写归属" in blob2 and "P1-1 | b1" not in blob2, f"rc={proc2.returncode}")

    # ⑥ P0/P1 不得拿包根一级文件的内容说事
    root_pkg2 = d / "rootfiles"
    (root_pkg2 / "a2").mkdir(parents=True)
    (root_pkg2 / "readme.md").write_text("# 包说明\n", encoding="utf-8")
    (root_pkg2 / "a2" / "asset.json").write_text("{}", encoding="utf-8")
    run_dir3 = d / "run3"
    run_dir3.mkdir()
    (run_dir3 / "probe-report.json").write_text('{"verdict": {"findings": []}}', encoding="utf-8")
    (run_dir3 / "inventory.json").write_text(json.dumps(
        {"package": str(root_pkg2), "assets": [{"dir": "a2", "name": "a2"}]}, ensure_ascii=False),
        encoding="utf-8")
    (run_dir3 / "validation-report.md").write_text(
        "---\nskill_version: x\ngenerated_at: y\n---\n\n# 报告\n\n## 4. 问题清单\n\n"
        "### P0 阻断\n\n无\n\n### P1 重要\n\n| 编号 | 归属 | 问题 |\n|---|---|---|\n"
        "| P1-1 | 包级（readme.md） | P1-DOC-FACT 包根说明算术错误 |\n"
        "| P1-2 | a2 | P1-DOC-REF 资产内引用缺失 |\n\n### P2 建议\n\n无\n\n## 5. 证据保留口径\n\nx\n",
        encoding="utf-8")
    proc3 = run([str(SCRIPTS / "check_report.py"), "--report", str(run_dir3 / "validation-report.md")], d)
    blob3 = (proc3.stdout or "") + (proc3.stderr or "")
    record("提示：归属为包根一级文件时报告警，且要求「直接移除、不要降 P2」",
           "包根一级文件" in blob3 and "不要降为 P2" in blob3 and "P1-2 | a2" not in blob3,
           f"rc={proc3.returncode}")

    # ⑦ lifecycle=draft 不得作为问题
    (run_dir3 / "validation-report.md").write_text(
        "---\nskill_version: x\ngenerated_at: y\n---\n\n# 报告\n\n## 4. 问题清单\n\n"
        "### P0 阻断\n\n无\n\n### P1 重要\n\n| 编号 | 归属 | 问题 |\n|---|---|---|\n"
        "| P1-1 | a2 | P1-VERSION lifecycle 全部为 draft，未验证就交付 |\n\n"
        "### P2 建议\n\n无\n\n## 5. 证据保留口径\n\nx\n",
        encoding="utf-8")
    proc4 = run([str(SCRIPTS / "check_report.py"), "--report", str(run_dir3 / "validation-report.md")], d)
    blob4 = (proc4.stdout or "") + (proc4.stderr or "")
    # 有 sage-need 却没带 --run-dir → 告警（P0-SAGE 会漏判）
    run_dir4 = d / "run4"
    run_dir4.mkdir()
    (run_dir4 / "sage-need.json").write_text('{"needed": true}', encoding="utf-8")
    (run_dir4 / "probe-report.json").write_text('{"checks": {}, "verdict": {"findings": []}}', encoding="utf-8")
    (run_dir4 / "validation-report.md").write_text(
        "---\nskill_version: x\ngenerated_at: y\n---\n\n# 报告\n\n## 4. 问题清单\n\n"
        "### P0 阻断\n\n无\n\n### P1 重要\n\n无\n\n### P2 建议\n\n无\n\n"
        "## 5. 证据保留口径\n\nx\n", encoding="utf-8")
    proc4b = run([str(SCRIPTS / "check_report.py"), "--report", str(run_dir4 / "validation-report.md")], d)
    blob4b = (proc4b.stdout or "") + (proc4b.stderr or "")
    record("提示：需要 Sage 但探测没带 --run-dir（缺 sage_profile）时报告警",
           "sage_profile" in blob4b and "--run-dir" in blob4b, f"rc={proc4b.returncode}")

    record("提示：问题行提到 lifecycle/draft 时告警（draft 是正常交付状态）",
           "lifecycle/draft" in blob4 and "不要降为 P2" in blob4, f"rc={proc4.returncode}")


# --------------------------------------------------------------------------- T18 skill 自更新
def t18_self_update(tmp: Path) -> None:
    print("\n[18/19] update_skill.py — 开跑前对齐远端最新 tag（失败不阻断）")
    import shutil as _sh
    if not _sh.which("git"):
        record("跳过：无 git", True, "git 不可用")
        return
    d = tmp / "t18"
    origin = d / "origin.git"
    seed = d / "seed"
    local = d / "local"
    d.mkdir(parents=True)
    run(["--version"], d)  # 保活
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=False)
    subprocess.run(["git", "clone", "-q", str(origin), str(seed)], check=False,
                   capture_output=True)
    (seed / "scripts").mkdir(parents=True)
    (seed / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    _sh.copy2(SCRIPTS / "update_skill.py", seed / "scripts" / "update_skill.py")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}

    def git(*args, cwd):
        e = dict(os.environ); e.update(env)
        return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, env=e)

    git("add", "-A", cwd=seed)
    git("commit", "-q", "-m", "v1", cwd=seed)
    git("tag", "-a", "v1.0.0", "-m", "v1", cwd=seed)
    git("branch", "-M", "main", cwd=seed)
    git("push", "-q", "origin", "main", cwd=seed)
    git("push", "-q", "--tags", "origin", cwd=seed)
    subprocess.run(["git", "--git-dir", str(origin), "symbolic-ref", "HEAD", "refs/heads/main"],
                   check=False)
    subprocess.run(["git", "clone", "-q", str(origin), str(local)], check=False, capture_output=True)

    up = local / "scripts" / "update_skill.py"
    proc = run([str(up), "--check"], local)
    record("正例：与远端同版本时判「已是最新」", "已是最新" in (proc.stdout + proc.stderr), proc.stdout.strip()[:60])

    (seed / "VERSION").write_text("1.0.1\n", encoding="utf-8")
    git("add", "-A", cwd=seed); git("commit", "-q", "-m", "v101", cwd=seed)
    git("tag", "-a", "v1.0.1", "-m", "v101", cwd=seed)
    git("push", "-q", "origin", "main", cwd=seed); git("push", "-q", "--tags", "origin", cwd=seed)

    proc = run([str(up), "--check"], local)
    record("正例：远端有新 tag 时判「有新版」", "有新版" in (proc.stdout + proc.stderr), proc.stdout.strip()[:60])

    proc = run([str(up), "--apply"], local)
    after = (local / "VERSION").read_text(encoding="utf-8").strip()
    record("正例：--apply 切到最新 tag 并更新 VERSION",
           proc.returncode == 0 and after == "1.0.1", f"rc={proc.returncode} VERSION={after}")

    (seed / "VERSION").write_text("1.0.2\n", encoding="utf-8")
    git("add", "-A", cwd=seed); git("commit", "-q", "-m", "v102", cwd=seed)
    git("tag", "-a", "v1.0.2", "-m", "v102", cwd=seed)
    git("push", "-q", "origin", "main", cwd=seed); git("push", "-q", "--tags", "origin", cwd=seed)
    (local / "VERSION").write_text("1.0.1\nlocal edit\n", encoding="utf-8")
    proc = run([str(up), "--apply"], local)
    record("负例：工作区有本地改动时拒绝更新（不覆盖）",
           proc.returncode == 1 and "工作区脏" in (proc.stdout + proc.stderr)
           and (local / "VERSION").read_text(encoding="utf-8").endswith("local edit\n"),
           f"rc={proc.returncode}")

    proc = run([str(up), "--no-update"], local)
    record("正例：--no-update 显式跳过且不失败",
           proc.returncode == 0 and "跳过" in (proc.stdout + proc.stderr), f"rc={proc.returncode}")

    # 目录只读时：明确报「无写权限」且不失败（不阻断核验）
    git("checkout", "--quiet", "--", ".", cwd=local)
    subprocess.run(["chmod", "-R", "a-w", str(local)], check=False)
    proc = run([str(up), "--apply"], local)
    record("正例：skill 目录不可写时降级为提示（无写权限、退出码 0）",
           proc.returncode == 0 and "无写权限" in (proc.stdout + proc.stderr), f"rc={proc.returncode}")
    subprocess.run(["chmod", "-R", "u+w", str(local)], check=False)


def _harbor_task(base: Path, name: str = "task-a", *, slug: str | None = None, env: str = "dir",
                 solve: bool = True, tests: bool = True, instr: str | None = "do the thing",
                 toml_body: str | None = None, os_name: str | None = None) -> Path:
    """构造最小 harbor 任务目录（目录名 name，task.toml 里的 [task].name 用 slug）。
    env: dir=environment/ 目录 / image=仅 docker_image 声明。"""
    t = base / name
    short = slug or name
    (t / "solution").mkdir(parents=True, exist_ok=True)
    (t / "tests").mkdir(parents=True, exist_ok=True)
    toml = toml_body if toml_body is not None else (
        f'schema_version = "1.4"\n\n[task]\nname = "acme/{short}"\n\n[environment]\n'
        + (f'os = "{os_name}"\n' if os_name else '')
        + ('docker_image = "python:3.12-slim"\n' if env == "image" else 'cpus = 1\n'))
    (t / "task.toml").write_text(toml, encoding="utf-8")
    if env == "dir" and toml_body is None:
        (t / "environment").mkdir(exist_ok=True)
        (t / "environment" / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
    if instr is not None:
        (t / "instruction.md").write_text(instr, encoding="utf-8")
    if solve:
        (t / "solution" / "solve.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    if tests:
        (t / "tests" / "test.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    return t


def _harbor_asset_json(t: Path, typ: str = "task") -> None:
    (t / "asset.json").write_text(json.dumps({
        "asset_id": f"sciasset://2026ZD123456/acme/{typ}/{t.name}", "asset_type": typ,
        "name": t.name, "asset_version": "1.0.0",
        "spec": {"artifacts": [{"uri": "task.toml", "media_type": "text/prs.toml"}]}},
        ensure_ascii=False), encoding="utf-8")


def _probe(pkg: Path, out: Path, tmp: Path) -> dict:
    run([str(SCRIPTS / "probe_package.py"), "--package", str(pkg), "--evaluate",
         "--json-out", str(out)], tmp)
    return json.loads(out.read_text(encoding="utf-8"))["verdict"]


def t19_harbor(tmp: Path) -> None:
    print("\n[19/19] harbor 任务包 — 形态识别、静态检查、reward 解析、blocked 语义")
    sys.path.insert(0, str(SCRIPTS))
    from _harbor import probe_harbor, static_issues, compute_task_digest
    from harbor_run import parse_job_reward

    d = tmp / "t18"

    # ---- 正例单任务包：必需文件齐 → 静态 P0 全过，P0-HARB-RUN 是执行占位 na
    pkg = d / "ok"
    t = _harbor_task(pkg, "my-task")
    _harbor_asset_json(t)
    v = _probe(pkg, d / "ok.json", d)
    ids = [f["check_id"] for f in v["findings"]]
    cov = {c["check_id"]: c for c in v["coverage"]}
    record("正例：必需文件齐全的 harbor 任务不产生 P0/P1 检查发现",
           "P0-HARB-STRUCT" not in ids and "P1-HARB-DATASET" not in ids and "P1-HARB-ENVELOPE" not in ids,
           f"ids={ids}")
    record("正例：P0-HARB-STRUCT pass；P0-HARB-RUN na 且理由指向 harbor_run.py 执行",
           cov["P0-HARB-STRUCT"]["status"] == "pass"
           and cov["P0-HARB-RUN"]["status"] == "na" and "harbor_run.py" in cov["P0-HARB-RUN"]["na_reason"],
           f"cov={cov['P0-HARB-STRUCT']},{cov['P0-HARB-RUN']}")
    prof = probe_harbor(pkg, [t])
    record("正例：形态识别为 task，schema_version/task_name 解析正确",
           prof["assets"]["my-task"]["form"] == "task"
           and prof["assets"]["my-task"]["task"]["task_name"] == "acme/my-task",
           f"profile={json.dumps(prof, ensure_ascii=False)[:200]}")

    # ---- 负例矩阵：缺文件逐项判 P0 P0-HARB-STRUCT
    def missing_case(name: str, **kw) -> None:
        p = d / name
        t2 = _harbor_task(p, "t", **kw)
        _harbor_asset_json(t2)
        v2 = _probe(p, d / f"{name}.json", d)
        f2 = next((f for f in v2["findings"] if f["check_id"] == "P0-HARB-STRUCT"), None)
        record(f"负例：{name} 判 P0 P0-HARB-STRUCT",
               f2 is not None and f2["level"] == "P0", f"findings={v2['findings']}")

    missing_case("no-tests", tests=False)
    missing_case("no-solution", solve=False)
    missing_case("empty-instruction", instr="")
    missing_case("bad-toml", toml_body='schema_version = "1.4"\n[task\nname = "x"\n')
    p = d / "no-env"
    t3 = _harbor_task(p, "t", env="none")
    _harbor_asset_json(t3)
    v3 = _probe(p, d / "no-env.json", d)
    f3 = next((f for f in v3["findings"] if f["check_id"] == "P0-HARB-STRUCT"), None)
    record("负例：environment/ 与 docker_image 双缺判 P0", f3 is not None and f3["level"] == "P0",
           f"findings={v3['findings']}")

    # ---- 显式 dataset：dataset.toml 与 asset.json 在数据集目录上，任务为其子目录
    p = d / "ds"
    set_dir = p / "set"
    _harbor_task(set_dir, "task1")
    _harbor_task(set_dir, "task2")
    (set_dir / "asset.json").write_text(json.dumps({
        "asset_id": "sciasset://2026ZD123456/acme/task/set", "asset_type": "task",
        "name": "set", "asset_version": "1.0.0",
        "spec": {"artifacts": [{"uri": "dataset.toml", "media_type": "text/prs.toml"}]}},
        ensure_ascii=False), encoding="utf-8")
    (set_dir / "dataset.toml").write_text(
        'schema_version = "1.0"\n\n[dataset]\nname = "acme/set"\n\n'
        '[[tasks]]\nname = "acme/task1"\ndigest = "sha256:' + "0" * 64 + '"\n\n'
        '[[tasks]]\nname = "acme/task3"\ndigest = "sha256:' + "1" * 64 + '"\n', encoding="utf-8")
    v4 = _probe(p, d / "ds.json", d)
    f4 = next((f for f in v4["findings"] if f["check_id"] == "P1-HARB-DATASET"), None)
    record("负例：dataset.toml 指向缺失任务判 P1 P1-HARB-DATASET",
           f4 is not None and f4["level"] == "P1" and "task3" in json.dumps(f4["detail"], ensure_ascii=False),
           f"findings={v4['findings']}")

    # ---- 隐式 dataset：一个资产目录下多个任务目录、无 dataset.toml → P2
    p = d / "implicit"
    set_dir = p / "set"
    _harbor_task(set_dir, "task1")
    _harbor_task(set_dir, "task2")
    (set_dir / "asset.json").write_text(json.dumps({
        "asset_id": "sciasset://2026ZD123456/acme/task/set", "asset_type": "task",
        "name": "set", "asset_version": "1.0.0", "spec": {"artifacts": []}},
        ensure_ascii=False), encoding="utf-8")
    v5 = _probe(p, d / "implicit.json", d)
    ids5 = [f["check_id"] for f in v5["findings"]]
    topics5 = [str(n.get("topic")) for n in (v5.get("notes") or [])]
    record("负例：隐式数据集（无 dataset.toml）只作 P2 建议，不进判定",
           any("隐式数据集" in t for t in topics5) and "P1-HARB-DATASET" not in ids5
           and not any(i.startswith("P0-HARB") for i in ids5), f"notes={topics5} ids={ids5}")

    # ---- 未登记的 harbor 任务 → P1-HARB-ENVELOPE（包络缺口）
    p = d / "unreg"
    _harbor_task(p, "loose-task")
    v6 = _probe(p, d / "unreg.json", d)
    f6 = next((f for f in v6["findings"] if f["check_id"] == "P1-HARB-ENVELOPE"), None)
    record("负例：harbor 任务不在任何 asset.json 条目下判 P1 P1-HARB-ENVELOPE",
           f6 is not None and f6["level"] == "P1", f"findings={v6['findings']}")

    # ---- asset_type 声明不是 task → P1-HARB-ENVELOPE
    p = d / "wrongtype"
    t7 = _harbor_task(p, "tw")
    _harbor_asset_json(t7, typ="tool")
    v7 = _probe(p, d / "wrongtype.json", d)
    f7 = next((f for f in v7["findings"] if f["check_id"] == "P1-HARB-ENVELOPE"), None)
    record("负例：asset.json 声明 asset_type=tool 与 harbor 实体不符判 P1",
           f7 is not None and "declared_asset_type_not_task" in json.dumps(f7["detail"]),
           f"findings={v7['findings']}")

    # ---- digest 复算（只作事实，不稳定判）
    dig = compute_task_digest(t)
    record("正例：任务目录内容摘要可复算且格式为 sha256:<64hex>",
           bool(re.match(r"^sha256:[a-f0-9]{64}$", dig)), f"digest={dig[:20]}…")

    # ---- reward 解析：三种真实输出形态
    job = d / "job1"
    trial = job / "oracle" / "task__abc"
    trial.mkdir(parents=True)
    (trial / "result.json").write_text(json.dumps(
        {"task_name": "acme/t", "verifier_result": {"rewards": {"reward": 1.0}}}), encoding="utf-8")
    r1 = parse_job_reward(job / "oracle")
    record("正例：trial result.json 的 verifier_result.rewards 解析出 reward=1.0",
           r1["reward"] == 1.0 and "result.json" in str(r1["source"]), f"got={r1}")
    job2 = d / "job2" / "nop"
    job2.mkdir(parents=True)
    (job2 / "results.jsonl").write_text('{"verified_reward": 0}\n', encoding="utf-8")
    r2 = parse_job_reward(job2)
    record("正例：results.jsonl 的 verified_reward 解析出 0.0（前向兼容）",
           r2["reward"] == 0.0, f"got={r2}")
    job3 = d / "job3" / "oracle"
    (job3 / "trialx" / "verifier").mkdir(parents=True)
    (job3 / "trialx" / "verifier" / "reward.txt").write_text("0\n", encoding="utf-8")
    r3 = parse_job_reward(job3)
    record("正例：verifier/reward.txt 兜底解析", r3["reward"] == 0.0, f"got={r3}")
    r4 = parse_job_reward(d / "job-none")
    record("负例：job 目录不存在时 reward=None 不抛异常", r4["reward"] is None, f"got={r4}")

    # ---- harbor_run.py：无任务 → na 退出 0；任务存在但环境不备 → blocked 退出 3
    p = d / "notask"
    p.mkdir(parents=True)
    run_dir = p / "asset-runs" / "run-20261117-000000"
    run_dir.mkdir(parents=True)
    proc = run([str(SCRIPTS / "harbor_run.py"), "--package", str(p), "--skip-lint"], d)
    rep = json.loads((run_dir / "harbor-run-report.json").read_text(encoding="utf-8"))
    record("正例：包内无 harbor 任务时 verdict=not_applicable 且退出 0",
           proc.returncode == 0 and rep["verdict"] == "not_applicable_no_harbor_tasks",
           f"rc={proc.returncode} verdict={rep['verdict']}")

    p = d / "blocked"
    t8 = _harbor_task(p, "t")
    _harbor_asset_json(t8)
    run_dir = p / "asset-runs" / "run-20261117-000001"
    run_dir.mkdir(parents=True)
    proc = run([str(SCRIPTS / "harbor_run.py"), "--package", str(p), "--skip-lint"], d,
               env_extra={"PATH": "/usr/bin:/bin", "ACI_ASSET_VERIFY_AUTO_INSTALL": "0"})
    rep = json.loads((run_dir / "harbor-run-report.json").read_text(encoding="utf-8"))
    blocked_reasons = {b["status"] for b in rep.get("blocked_tasks", [])}
    record("正例：docker/harbor 不备时全部任务记 blocked（不是 na、不是 fail）且退出 3",
           proc.returncode == 3 and rep["verdict"] == "blocked" and len(rep["blocked_tasks"]) == 1
           and blocked_reasons <= {"blocked_docker", "blocked_missing_harbor"},
           f"rc={proc.returncode} verdict={rep['verdict']} blocked={blocked_reasons}")

    # ---- case-minimums：task 全量 + 负例要求
    mins = json.loads((SKILL / "assets" / "case-minimums.json").read_text(encoding="utf-8"))
    record("正例：case-minimums 给 task 规定 nop 负例（negative_expected=1）",
           (mins["require_kinds"].get("task") or {}).get("negative_expected") == 1
           and "全量" in mins["by_type"]["task"]["why"], f"task={mins['by_type']['task']}")

    # ---- audit-checks：harbor 检查项已入清单且级别正确
    ac = json.loads((SKILL / "assets" / "audit-checks.json").read_text(encoding="utf-8"))
    lv = {c["id"]: c["level"] for c in ac["checks"] if "HARB" in c["id"]}
    record("正例：清单含 5 条 harbor 检查（P0-HARB-STRUCT/RUN、P1-HARB-DATASET/ENVELOPE/LINT）",
           lv == {"P0-HARB-STRUCT": "P0", "P0-HARB-RUN": "P0",
                  "P1-HARB-DATASET": "P1", "P1-HARB-ENVELOPE": "P1", "P1-HARB-LINT": "P1"},
           f"levels={lv}")


# --------------------------------------------------------------------------- main
def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="saci-selftest-"))
    print(f"skill 自测：{SKILL}")
    print(f"临时目录：{tmp}")
    try:
        for fn in (t1_envelope, t2_list, t3_coverage, t4_work_copy, t5_prune, t6_sage,
               t7_asset_ids, t8_external_refs, t9_version_stamp, t10_report_levels, t11_selfcontained, t12_run_id, t13_mount_external, t14_mounted_not_checked,
               t15_skill_spec, t16_skill_agent_cli, t17_reproducible, t18_self_update, t19_harbor):
            try:
                fn(tmp)
            except Exception as exc:  # noqa: BLE001
                record(f"{fn.__name__} 异常", False, f"{type(exc).__name__}: {exc}")
    finally:
        if VERBOSE:
            print(f"\n（-v：临时目录保留在 {tmp}）")
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n{'=' * 60}\n自测结果：{passed}/{total} 通过" + ("" if passed == total else "  ← 有失败"))
    if passed != total:
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  - {name}：{detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
