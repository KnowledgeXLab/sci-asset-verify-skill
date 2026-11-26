#!/usr/bin/env python3
"""harness 适配器的契约自检：新增/修改适配器时，关键字段不能漏、不能自相矛盾。

检查项（每条对应一个真实踩过的坑或必须遵守的约定）：
  1. `{prompt}` 必须出现在 argv 里 —— 否则 harness 收不到任务
  2. `headless=False` 必须写 `not_headless_reason` —— 否则调用方不知道该报什么错
  3. `headless=True` 的适配器应带 `verified` 或至少可被 auto 选中（不能全是 blocked）
  4. `priority` ∈ 1..4 —— 与 README「优先级」表对齐
  5. `name` 唯一、非空；`bins` 非空、无重复；`argv` 第一项必须是 `{bin}`
  6. 需要外部凭证的适配器 `sage` 必须声明 kind（env/patch/unsupported）
  7. 桌面应用类（headless=False）不得进入 auto 候选

用法：python3 tests/harness_contract.py [-v]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from harnesses import ADAPTERS, ORDER, REGISTRY, available  # noqa: E402


def main(argv=None) -> int:
    verbose = "-v" in (argv or sys.argv[1:])
    checks: list[tuple[str, bool, str]] = []

    def add(label: str, ok: bool, detail: str = "") -> None:
        checks.append((label, ok, detail))

    auto = {a.name for a in available()}

    for a in ADAPTERS:
        add(f"{a.name}: argv 含 {{prompt}}", "{prompt}" in a.argv, " ".join(a.argv))
        add(f"{a.name}: argv 以 {{bin}} 开头", bool(a.argv) and a.argv[0] == "{bin}",
            str(a.argv[:2]))
        add(f"{a.name}: bins 非空且无重复", bool(a.bins) and len(set(a.bins)) == len(a.bins),
            str(list(a.bins)))
        add(f"{a.name}: priority ∈ 1..4", isinstance(a.priority, int) and 1 <= a.priority <= 4,
            str(a.priority))
        add(f"{a.name}: sage 声明 kind",
            isinstance(a.sage, dict) and a.sage.get("kind") in ("env", "patch", "unsupported"),
            str(a.sage)[:60])
        if a.headless:
            add(f"{a.name}: headless=True 不得进 auto 前仍是 blocked",
                a.name not in auto or not str(a.verified).startswith("blocked"),
                f"verified={a.verified or '-'}")
        else:
            add(f"{a.name}: headless=False 必须写原因", bool(a.not_headless_reason.strip()),
                a.not_headless_reason[:60])
            add(f"{a.name}: headless=False 不得进 auto 候选", a.name not in auto, str(a.name in auto))

    add("适配器 name 唯一", len(REGISTRY) == len(ADAPTERS), f"{len(REGISTRY)}/{len(ADAPTERS)}")
    add("ORDER 覆盖全部适配器", set(ORDER) == set(REGISTRY), f"{len(ORDER)}")

    ok = sum(1 for _, good, _ in checks if good)
    for label, good, detail in checks:
        if not good or verbose:
            print(f"  {'PASS' if good else 'FAIL'}  {label}" + (f"  ({detail})" if detail else ""))
    print(f"\nharness 契约自检：{ok}/{len(checks)} 通过")
    return 0 if ok == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
