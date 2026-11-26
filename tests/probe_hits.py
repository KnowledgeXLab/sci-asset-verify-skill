#!/usr/bin/env python3
"""probe_package 的「正向命中」自检：每个检查都要有一位故意违规的样本，证明它真的会报。

为什么需要：行覆盖率只能证明代码被执行过，不能证明判定成立。历史上踩过两次——
`absolute_symlinks` 被一行早退跳过、`sage_profile` 把"还没 detect"当成"不需要"——
两处测试全绿但检查静默失效。本文件按「一个 probe → 一条违规样本 → 断言指定字段命中」组织。

用法：
  python3 tests/probe_hits.py            # 全跑，退出码 0=全部命中
  python3 tests/probe_hits.py -v         # 打印每条样本的命中证据

覆盖不到的部分（如实标注）：probe_governance / probe_lifecycle 只产事实，不产生 P0/P1，
故只断言"字段存在"，不断言命中。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
PROBE = SKILL / "scripts" / "probe_package.py"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def asset_json(name: str, atype: str = "data", version: str = "1.0.0", artifacts=None, **over) -> dict:
    doc = {"schema_version": "1.0.0", "asset_id": f"sciasset://2026ZD000001/ns/{atype}/{name}",
           "asset_type": atype, "name": name, "description": "自检样本", "asset_version": version,
           "lifecycle": "validated", "domains": [{"id": "domain:earth-science"}],
           "task_classes": [{"id": "operation:analysis"}],
           "provenance": {"created_time": "2026-01-01T00:00:00+08:00",
                          "provider": [{"name": "示例单位", "role": "provider"}],
                          "source": {"kind": "original"}},
           "governance": {"license": "CC-BY-4.0", "access": "public", "risk_level": "low",
                          "privacy": "none", "usage_constraints": []},
           "spec": {"metadata": {"format": ["csv"], "data_model": "tabular"},
                    "artifacts": artifacts if artifacts is not None else [],
                    "domain_attributes": {}, "asset_protocol": {}}}
    doc.update(over)
    return doc


def mk_asset(root: Path, name: str = "a1", atype: str = "data", *, payload: str | None = None,
             cases: list | None = None, readme: str | None = None, **over) -> Path:
    """建一个最小合规资产（可按需替换/省略某个部件）。"""
    d = root / name
    (d / "validate").mkdir(parents=True, exist_ok=True)
    arts = []
    if payload is not None:
        (d / "payload").mkdir(exist_ok=True)
        (d / "payload" / "d.csv").write_text(payload, encoding="utf-8")
        arts = [{"id": "d", "artifact_type": "file", "uri": "payload/d.csv",
                 "media_type": "text/csv", "role": "primary-data",
                 "sha256": sha(payload), "size_bytes": len(payload.encode())}]
    (d / "asset.json").write_text(json.dumps(asset_json(name, atype, artifacts=arts, **over),
                                             ensure_ascii=False, indent=2), encoding="utf-8")
    if cases is not None:
        (d / "validate" / "cases.json").write_text(json.dumps({"cases": cases}, ensure_ascii=False),
                                                   encoding="utf-8")
    if readme is not None:
        (d / "validate" / "readme.md").write_text(readme, encoding="utf-8")
    return d


# ---------------------------------------------------------------- 样本
def c_structure_orphan(root: Path) -> None:
    mk_asset(root, "a1", payload="x\n")
    (root / "not-an-asset").mkdir()                       # 子目录但没有 asset.json


def c_structure_root_asset(root: Path) -> None:
    mk_asset(root, "a1", payload="x\n")
    (root / "asset.json").write_text('{"x":1}', encoding="utf-8")


def c_artifact_missing(root: Path) -> None:
    d = mk_asset(root, "a1", payload="x\n")
    doc = json.loads((d / "asset.json").read_text(encoding="utf-8"))
    doc["spec"]["artifacts"][0]["uri"] = "payload/ghost.csv"     # 声明了但没有这个文件
    (d / "asset.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def c_artifact_sha(root: Path) -> None:
    d = mk_asset(root, "a1", payload="x\n")
    doc = json.loads((d / "asset.json").read_text(encoding="utf-8"))
    doc["spec"]["artifacts"][0]["sha256"] = "0" * 64
    (d / "asset.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def c_cases_missing(root: Path) -> None:
    mk_asset(root, "a1", payload="x\n")                   # 有 validate/ 但没 cases.json


def c_readme_incomplete(root: Path) -> None:
    mk_asset(root, "a1", payload="x\n", cases=[{"id": "C01"}],
             readme="# 随便写点什么\n\n没有 Agent / 测试任务 / 通过门槛三节。\n")


def c_tool_contract(root: Path) -> None:
    d = mk_asset(root, "t1", "tool")
    (d / "run.py").write_text('import json, sys\nprint(json.dumps({"error": "undeclared_failure"}))\n',
                              encoding="utf-8")


def c_data_records(root: Path) -> None:
    d = mk_asset(root, "a1", payload="id,value\n1,1\n2,2\n")
    (d / "metadata.json").write_text(json.dumps({"records": 99, "columns": ["id", "value"]}),
                                     encoding="utf-8")


def c_verify_docs(root: Path) -> None:
    mk_asset(root, "a1", payload="x\n", cases=[{"id": "C01"}],
             readme="# 验证说明\n\n## Agent\nDSH\n\n## 测试任务\n跑 5 组用例\n\n## 通过门槛\n全过\n")


def c_verify_entry(root: Path) -> None:
    d = root / "a1"
    d.mkdir(parents=True)
    (d / "asset.json").write_text(json.dumps(asset_json("a1"), ensure_ascii=False), encoding="utf-8")
    # 没有 validate/、没有 acceptance.json、没有脚本 → 无验证入口


def c_secret(root: Path) -> None:
    d = mk_asset(root, "a1", payload="x\n")
    (d / "config.yaml").write_text("api_key: sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2\n", encoding="utf-8")


def c_outside_ref(root: Path) -> None:
    d = mk_asset(root, "a1", payload="x\n")
    doc = json.loads((d / "asset.json").read_text(encoding="utf-8"))
    doc["spec"]["artifacts"][0]["uri"] = "../other/x.csv"
    (d / "asset.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def c_abs_symlink(root: Path) -> None:
    d = mk_asset(root, "a1", payload="x\n")
    os.symlink(str(d / "payload" / "d.csv"), d / "payload" / "link.csv")


def c_escaping_symlink(root: Path) -> None:
    mk_asset(root, "a1", payload="x\n")
    other = mk_asset(root, "a2", payload="y\n")
    os.symlink("../../a2/payload/d.csv", root / "a1" / "payload" / "link.csv")


def c_doc_ref(root: Path) -> None:
    d = mk_asset(root, "a1", payload="x\n")
    (d / "readme.md").write_text("# 用法\n\n先跑 `scripts/ghost_runner.py`。\n", encoding="utf-8")


def c_version(root: Path) -> None:
    d = mk_asset(root, "a1", payload="x\n")
    (d / "validate" / "cases.json").write_text(
        json.dumps({"asset_version": "9.9.9", "cases": [{"id": "C01"}]}), encoding="utf-8")


def c_envelope(root: Path) -> None:
    d = root / "a1"
    d.mkdir(parents=True)
    doc = asset_json("a1")
    doc.pop("governance")
    (d / "asset.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def c_external_refs(root: Path) -> None:
    d = mk_asset(root, "a1", payload="x\n")
    (d / "external_assets.json").write_text(json.dumps({"assets": [
        {"asset_id": "sciasset://p/n/data/ext", "asset_version": "1.0.0",
         "target_path": "../escape"}]}), encoding="utf-8")


def _noop(root: Path) -> None:
    mk_asset(root, "a1", payload="x\n")


CASES = [
    ("结构：子目录不是资产目录", c_structure_orphan, "structure_profile",
     lambda p: bool(p.get("non_asset_dirs"))),
    ("结构：包根出现 asset.json", c_structure_root_asset, "structure_profile",
     lambda p: p.get("root_asset_json") is True),
    ("实体：声明的文件不存在", c_artifact_missing, "artifact_integrity",
     lambda p: bool(p.get("missing"))),
    ("实体：sha256 与磁盘不符", c_artifact_sha, "artifact_integrity",
     lambda p: bool(p.get("sha_mismatch"))),
    ("用例：有 validate/ 但没有 cases.json", c_cases_missing, "cases_profile",
     lambda p: "a1" in (p.get("assets_without_cases") or [])),
    ("验证说明：缺 Agent/任务/门槛三节", c_readme_incomplete, "validate_readme_profile",
     lambda p: "a1" not in (p.get("complete") or [])),
    ("工具契约：实现里出现未声明的错误名", c_tool_contract, "tool_contract_profile",
     lambda p: bool((p.get("assets", {}).get("t1") or {}).get("impl_error_names_not_declared"))),
    ("数据声明：记录数与实体不符", c_data_records, "data_declaration_profile",
     lambda p: bool((p.get("assets", {}).get("a1") or {}).get("records_mismatch"))),
    ("验证说明：声明 5 组但实际 1 组", c_verify_docs, "verify_doc_profile",
     lambda p: bool(p.get("mismatch"))),
    ("验证入口：资产没有可执行入口", c_verify_entry, "verify_entry_profile",
     lambda p: "a1" in (p.get("without_entry") or [])),
    ("凭据：文件里有明文 sk- 密钥", c_secret, "secret_profile",
     lambda p: (p.get("count") or 0) > 0),
    ("越界：artifacts 用 ../ 指向包外", c_outside_ref, "outside_refs_profile",
     lambda p: bool(p.get("outside_refs"))),
    ("越界：绝对路径软链", c_abs_symlink, "outside_refs_profile",
     lambda p: bool(p.get("absolute_symlinks"))),
    ("越界：软链指向兄弟资产", c_escaping_symlink, "outside_refs_profile",
     lambda p: bool(p.get("escaping_symlinks"))),
    ("文档引用：指向不存在的脚本", c_doc_ref, "doc_refs_profile",
     lambda p: bool(p.get("missing"))),
    ("版本：asset_version 与 cases 不一致", c_version, "version_profile",
     lambda p: bool(p.get("mismatch"))),
    ("包络：asset.json 缺 governance", c_envelope, "envelope_profile",
     lambda p: bool(p.get("errors"))),
    ("外部引用：target_path 越界", c_external_refs, "external_refs_profile",
     lambda p: bool((p.get("assets", {}).get("a1") or {}).get("invalid_target_path"))),
]

RUNTIME_CASES = [
    ("Sage：只判静态就应标 needs_detect（不是 needed=false）", "sage_profile",
     lambda p: p.get("needs_detect") is True),
    ("Harbor：没跑过时 report_present 必须为 false", "harbor_run_profile",
     lambda p: p.get("report_present") is False),
]


def run_probe(pkg: Path, run_dir: Path | None = None, static_only: bool = True) -> dict:
    out = pkg.parent / (pkg.name + "-probe.json")
    cmd = [sys.executable, str(PROBE), "--package", str(pkg), "--evaluate", "--json-out", str(out)]
    if static_only:
        cmd.append("--static-only")
    if run_dir is not None:
        cmd = cmd[:4] + ["--run-dir", str(run_dir)] + cmd[4:]
    subprocess.run(cmd, capture_output=True, text=True)
    return json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {}


def main(argv=None) -> int:
    verbose = "-v" in (argv or sys.argv[1:])
    ok = fail = 0
    with tempfile.TemporaryDirectory(prefix="probe-hits-") as td:
        base = Path(td)
        for label, builder, key, predicate in CASES:
            pkg = base / ("case-" + builder.__name__.replace("c_", ""))
            pkg.mkdir(parents=True)
            builder(pkg)
            checks = (run_probe(pkg).get("checks") or {})
            hit = predicate(checks.get(key) or {})
            print(f"  {'PASS' if hit else 'FAIL'}  {label}  → checks.{key}")
            if verbose and not hit:
                print(f"        实际：{json.dumps(checks.get(key), ensure_ascii=False)[:300]}")
            ok, fail = (ok + 1, fail) if hit else (ok, fail + 1)

        # 运行期三项：无产物时必须是"未执行"而不是"不适用"
        pkg = base / "case-runtime"; pkg.mkdir()
        _noop(pkg)
        run_dir = base / "case-runtime-run"; run_dir.mkdir()
        checks = (run_probe(pkg, run_dir, static_only=False).get("checks") or {})
        for label, key, predicate in RUNTIME_CASES:
            hit = predicate(checks.get(key) or {})
            print(f"  {'PASS' if hit else 'FAIL'}  {label}  → checks.{key}")
            if verbose and not hit:
                print(f"        实际：{json.dumps(checks.get(key), ensure_ascii=False)[:300]}")
            ok, fail = (ok + 1, fail) if hit else (ok, fail + 1)

    print(f"\n正向命中自检：{ok}/{ok + fail} 通过")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
