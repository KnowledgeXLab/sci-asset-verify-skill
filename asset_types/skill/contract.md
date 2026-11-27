# 动态检查合约 · skill 资产：验收说明完整性

你负责**一个**资产：`{asset}`（类型 `skill`）。只判这个资产，不要评价包内其它资产。

- 资产目录：`{asset_dir}`
- 执行根：`{package}`（副本，可在里面跑用例，不要写原包）
- 批次目录：`{run}`（只往这里写结论与日志）
- 产物目录：`{artifacts}`（被测产物放这里）

## 这个类型要做什么

validate/readme.md 是否写清 Agent / 测试任务 / 通过门槛三段

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

## 使用说明（`usage.md`，你负责写）

把**这个资产**怎么用写进 `{run}/agents/{asset}/usage.md`（主进程会合并成批次级 `usage.md`）：

```
# {asset}

- 用途与适用边界：
- 输入：字段/单位/取值域（逐项）
- 输出：字段/单位/取值范围
- 运行环境：解释器/依赖/设备/网络
- 入口与示例命令：`<命令>`（本地路径 / 服务端路径 / 下载 URL 分别标注）
- 已知限制：
```


## 部署核验（本类型必须做）

agent 侧验收即由本合约的用例执行覆盖；若 skill 声明了 MCP 或外部服务，按 tool 类同样做注册与最小调用。

结果写 `{run}/agents/{asset}/deployment.json`：`{"status": "completed|blocked|not_applicable_no_such_asset|not_supported_by_skill", "detail": {...}, "evidence": [...]}`。
- 仅生成配置 / 进程能起 / HTTP 200 都**不算**注册或部署成功；
- 缺目标平台、凭证或硬件 → 记 `blocked` 并写清阻塞点（不要静默跳过、不要用 `not_applicable` 掩盖能力缺口）。


## 输出（唯一交付物）

写 `{run}/agents/{asset}/findings.json`：

```json
{
  "asset": "{asset}",
  "asset_type": "skill",
  "status": "completed",
  "blocked_reason": "",
  "cases": {"planned": 0, "executed": 0, "passed": 0, "failed": 0, "blocked": 0},
  "confirmed": [{"level": "P0", "check_id": "", "summary": "", "evidence": "相对路径:行号", "criterion": ""}],
  "dismissed": [{"level": "P1", "summary": "", "why_false_positive": ""}],
  "usage": "usage.md",
  "deployment": "deployment.json",
  "notes": []
}
```

硬要求：`P0/P1` 只能来自机检事实或本合约判据；**不得自创级别**；不得把「没跑」写成「通过」。
