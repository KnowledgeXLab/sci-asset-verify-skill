# 动态检查合约 · benchmark 资产：评测口径

你负责**一个**资产：`{asset}`（类型 `benchmark`）。只判这个资产，不要评价包内其它资产。

- 资产目录：`{asset_dir}`
- 执行根：`{package}`（副本，可在里面跑用例，不要写原包）
- 批次目录：`{run}`（只往这里写结论与日志）
- 产物目录：`{artifacts}`（被测产物放这里）

## 这个类型要做什么

metrics / submission / 组合引用（当前无专项静态检查）

## 步骤

1. **读资产自己的说明**：`validate/readme.md`（或资产入口文档）→ 确认入口、依赖、输入输出。
2. **配置独立运行环境**：按资产声明准备依赖（只在资产目录或 `{run}/env/` 内，不污染系统）。
3. **执行用例**：
   - 有 `validate/cases.json`：按 `<资产>/validate/run.json` 声明的 argv 逐条执行
     （占位符 `{case}` `{artifact}` `{work}` `{package}`），或直接
     `python3 {skill_dir}/scripts/run_cases.py --package {package} --run-dir {run} --only {asset}`；
   - 有 `task.toml`（harbor）：`python3 {skill_dir}/scripts/harbor_run.py --package {package} --run-dir {run}`（**串行**）；
   - 需要 Sage：`python3 {skill_dir}/scripts/sage_probe.py detect/config`（模型固定 Sage-A1.5）。
4. **核验结果**：实际输出与 expected 对比，给出差值与判定（通过 / 不通过 / 未判定），写进 `findings.json`。
5. **自查误判（必须）**：对每条拟报问题逐条确认
   - 是否**确实**违反规范（附具体文件与行号级证据）；
   - 是否属于「明确不判」范围：`governance` 取值、包根一级文件、`asset_protocol` 内容、`lifecycle` 取值、
     资产内部实现风格、示例包占位值；
   - 是否因**环境问题**（缺依赖/缺 GPU/缺服务）而非资产缺陷——环境问题记 `blocked`，不要当缺陷报。
   自查后把确认为误报的写进 `dismissed`，每条写一句排除理由。

## 输出（唯一交付物）

写 `{run}/agents/{asset}/findings.json`：

```json
{
  "asset": "{asset}",
  "asset_type": "benchmark",
  "status": "completed",
  "blocked_reason": "",
  "cases": {"planned": 0, "executed": 0, "passed": 0, "failed": 0, "blocked": 0},
  "confirmed": [{"level": "P0", "check_id": "", "summary": "", "evidence": "相对路径:行号", "criterion": ""}],
  "dismissed": [{"level": "P1", "summary": "", "why_false_positive": ""}],
  "notes": []
}
```

硬要求：`P0/P1` 只能来自机检事实或本合约判据；**不得自创级别**；不得把「没跑」写成「通过」。
