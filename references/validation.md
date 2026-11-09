# 用例协议与验证报告

## 收集用例

先收集交付包的全部给定用例，保留原始输入、预期输出和出处。每个资产建立 case_ids；无给定用例时标记 `no_provided_case`，可补建 smoke case 验证可调用性，明确来源为新增，科学真值缺失时仍为未验证。多个资产共享端到端case时记录各自检查点，不能把工作流通过直接套用到所有依赖资产。

| 资产类型 | 可用 case/检查点 |
|---|---|
| data | 数据可读、字段/单位、记录数、主键、已知记录查询；具体数值依据冻结数据或提供的参考 |
| model | 给定输入推理、各子模型/规格覆盖、边界及错误输入；权重、预处理、输出字段和误差 |
| tool/MCP | initialize与发现后实际业务调用、合法/非法参数、批量部分失败；模型结果还需单独比对 |
| skill/task | 给定自然语言需求到最终产物的完整链路，记录工具轨迹、参数传递与报告完整性 |
| environment | 依赖导入、权重加载及最小调用；只证明环境可运行，不算科学结果通过 |
| trajectory | schema、调用顺序、ID关系、输入输出完整性和真实产物可追溯 |
| benchmark | 评测入口可执行、任务/数据/环境引用存在、比较器对已知相等/不等结果判定正确 |


## 运行前锁定协议

cases.json 每例包含 case_id、asset_id/version、entrypoint、input、expected_kind(success/error)、truth_path、truth_kind、comparison、threshold_source。记录关键输入/权重/真值 hash。仅在有提供 case 时称为给定用例；补建 smoke case 单独标明，不能代替遗漏用例。

真值即资产给定的 expected output（包内 expected_output、gt/标注、固定输出、实测/专家结果）。模型只做推理，用推理结果与 expected output 比较；不与训练日志、checkpoint 内的验证记录或历史报告中的指标比较。历史 Agent 运行作为真值时，需核验其原始响应、参数及版本；报告文本不自动构成真值。

记录字段映射、匹配键、单位转换、忽略字段与容差。结果与真值按 case_id、样本 ID/规范化科学身份对齐，不凭行顺序连接；检查缺失、重复、多余记录。动态 task_id、路径、时间戳可按协议忽略，但结果指向的资产/结构关系仍需验证。跨运行标识不同，以科学对象与实际条件建立映射。

## 比较规则

- JSON：检查结构、必需字段、类型、数组长度、状态；数字与布尔分别处理。CSV/Excel：检查列、样本键、单位、缺失值。字符串/类别按协议精确匹配，必要的规范化保留原值。
- 数值：`abs(actual - expected) <= atol + rtol * abs(expected)`，逐字段记录单位与误差。阈值来自用户、包内协议或有依据的标准；未知时报告差值与“未判定”。零真值不计算普通百分比相对误差。NaN/Inf/缺失不得静默通过。
- 汇总误差：回归按适用性报告 MAE/RMSE、最大误差，可选 R²（单点或零方差时不定义）；分类/排序用任务指定指标。按目标及单位分组，不同单位的指标分别统计。分位预测检查顺序和区间覆盖；报告样本量及数据来源。
- 预期错误：检查错误码/约定消息、success/isError 和必要退出码；不要求动态 stderr 完全一致。超时、缺依赖、连不上服务属于执行失败，不是正确输入拒绝。
- 科学流程：检查物理条件、权重、收敛性、适用范围、单位及关键派生关系。不能仅凭最终数字接近跳过流程错误。

缺真值标 not_evaluated；未执行标 skipped/blocked；预期错误满足协议可 pass。分母保留全部计划 case，分别给出 planned/executed/pass/fail/blocked/skipped/not_evaluated；通过率明确分母，零分母写不适用。

## 报告产物

`comparison.json` 按 case 保存：输入与真值来源、匹配键、字段 actual/expected/unit/absolute_error/tolerance/status、忽略项、运行状态、科学判定、完整性判定及证据相对路径。

`validation-report.md` 是简洁的结果对比报告，只回答“这个工具/skill/模型等跑出来的结果和 expected output 是否一致”。内容：
1. 结论：一两段话给出通过/不通过/未判定，以及最关键的差异数字。
2. 对比对象：被测对象与版本、本次 run 目录、expected output 路径、对比日期，各一行。
3. 结果对比表：按 case 或关键字段列出 expected / actual / 差值 / 判定；case 多时给汇总表（计划/执行/通过/失败/未判定及通过率分母）加关键指标或差异最大的若干条，不逐条罗列全部 case。
4. 问题（仅在存在时写）：不一致、失败、重试、缺失输出，每条说明现象和影响。
5. 最终判定：维度 | 判定 小表 + 一句综合结论。

不写资产登记、编号、文件哈希、部署改动、环境依赖清单、训练日志/基准复现、科学误差与泛化讨论、复跑命令、待补治理信息；这些留在 inventory.json、deployment/、state.json 和登记表中。报告数值只能来自本次输出与 comparison.json。

交付前核对报告中的 case 总数、对比数值和失败清单与 comparison.json 一致。
