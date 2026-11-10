# 登记表规则（对外发版资产清单）

逐列真实样例、长度基准与常见错误见 [registration-examples.md](registration-examples.md)。清单字段集与语义以《科学智能体资产统一表征体系设计 v1.0》§7「对外发版资产清单」为准。

## 两份清单，别弄混

| | 对外发版资产清单（本文件填写的对象） | 内部管理清单（不是本 skill 的产物） |
|---|---|---|
| 用途 | 给外部单位判断"这个资产对我有没有用、能不能用" | 内部台账：编号、校验结论、集成进度 |
| 字段 | 24 列（见下） | 在 24 列基础上另有资产编号、校验、资产校验报告、记录人、集成负责人、目前集成状态 |
| 资产协议 | **不占列**，写在 `asset.json` 的 `spec.asset_protocol` | 同样不占列 |
| 谁产出 | 本 skill | 资产提供方的内部流程 |

对外清单是**人读索引**，机器可读以 §4 的资产描述文件（`asset.json` 包络）为准；两者冲突时以描述文件为准并回填表格。

## 表头以内置 schema 为准

填表标准是 skill 自带的 `assets/external-list-schema.json`：对外发版清单 25 个字段的**名称、顺序、类型、枚举取值与填写口径**都在里面（取自规范 §7.3，字段类型与选项取自线上清单快照）。飞书 CLI 在外发环境不可用，因此表头固化在该文件里。

规则：

1. **不检查、不套用用户自定义模板**。工作区里出现的其他 xlsx（目录型清单、旧版 26 字段表、其他单位模板）只作口径参考，不得作为表头来源，也不得为了适配它改写字段集。
2. **字段顺序**一律按 `fields[].order`；表头文字逐字对齐 `fields[].header`（含 ASCII 竖线 `|`）。
3. **枚举**只用 `fields[].options` 内的值；options 之外的值先确认是不是新注册词表，不要自造。
4. 用户**显式指定**另一份 schema 时才例外：把该 schema 落成 `registration/schema-snapshot.json`，在 `registration/field-map.json` 里逐字段列出与内置 schema 的差异（缺失/多余/改名/枚举不同），否则一律以内置 schema 为准。
5. **用 `scripts/build_list.py` 生成表格，生成即自检**：`python scripts/build_list.py --rows rows.json --out registration/科学数字资产清单.xlsx`。脚本按 schema 建表头、加枚举下拉、写「用例明细」「字段口径说明」，并自动调用 `scripts/check_list.py` 校验表头顺序、枚举、必填、版本格式、`资产名称+版本` 唯一、用例明细引用可解析，结果落盘为 `registration/check-list-report.json`（可用 `--check-json-out` 指定路径）；不通过时非 0 退出并保留 xlsx。已有表格需单独复检时用 `python scripts/check_list.py <xlsx>`。脚本报 error 时先修数据，不要改 schema 迁就。

## 25 个字段

下表是 `assets/external-list-schema.json` 的可读摘要；**权威定义以 JSON 为准**（含每列的 options 与示例）。`|` 为表头里的 ASCII 竖线，写入时保留原表头。

| # | 字段 | 类型 | 必填 | 填写要求 | 主要来源（asset.json） |
|---|---|---|---|---|---|
| 1 | 资产ID | 文本 | 必填 | 格式 `{project-id}/{namespace}/{asset-type}/{slug}`，即包络 `asset_id` 去掉 `sciasset://` 前缀；**不含版本号**，版本由「版本」列表达。用 scripts/asset_ids.py 生成，不手写 | `asset_id` |
| 2 | 资产名称 | 文本 | 必填 | 外部可读；科学对象+功能+必要规格，≤25 字，不用内部代号 | `name` |
| 3 | 资产类型 | 单选 | 必填 | data/tool/model/environment/skill/task/trajectory/benchmark 八选一 | `asset_type` |
| 4 | 领域Profile | 单选/多选 | 必填 | 填清单内的中文领域名；与 `domains[].id`（`domain:*`）一一对应，机器处理以 ID 为准 | `domains` |
| 5 | 资产摘要 | 长文本 | 必填 | 一到两句：解决什么问题、核心能力、适用边界 | `description` |
| 6 | 任务\|操作标签 | 多选 | 必填 | 只取注册词表 `problem:*`/`operation:*`/`stage:*`/`task:*`，不自造；task/skill/trajectory/benchmark 至少一个 | `task_classes[].id` |
| 7 | 任务描述 | 长文本 | 建议必填 | 给定条件、处理目标、预期产出；与标签互补 | `spec.metadata` |
| 8 | 科学对象 | 文本 | 必填 | 实际处理的科学实体，2–6 个名词短语 | `spec.domain_attributes` |
| 9 | 资产提供单位 | 文本 | 必填 | 提供方或责任单位全称 | `provenance.provider` |
| 10 | 联系人 | 文本 | 必填 | 只写姓名，必要时补联系方式；写入前 strip | 交付材料/用户提供 |
| 11 | 项目负责人 | 文本 | 选填 | `姓名 - 单位` 或姓名 | 交付材料/用户提供 |
| 12 | 来源\|产出方式 | 文本 | 必填 | 采集/实验/仿真/模型生成/专家整理/自研封装；派生须写上游 | `provenance.source` |
| 13 | 版本 | 文本 | 必填 | 语义化版本，与 `asset_version` 一致，不保留 `v` 前缀差异 | `asset_version` |
| 14 | 格式\|接口 | 文本/多选 | 条件必填 | 数据格式、服务接口、Schema、镜像；可执行资产写清调用方式与入口 | `spec.metadata`/`spec.artifacts[].media_type` |
| 15 | 规模\|数量 | 文本 | 选填 | 数量+单位，多项用"；"，注明统计口径 | `spec.metadata` |
| 16 | 时间范围 | 文本 | 选填 | 采集/实验/运行/适用时间；无则写"不适用（按输入重算）" | `provenance`/`spec.metadata` |
| 17 | 空间\|坐标\|实验条件 | 长文本 | 选填 | 空间范围、CRS、分辨率、实验与测量条件 | `spec.domain_attributes` |
| 18 | 输入说明 | 长文本 | 可执行资产必填 | 字段/格式/单位/必填性；文件类写清命名与目录约定 | `spec.metadata` 输入合同 |
| 19 | 输出说明 | 长文本 | 可执行资产必填 | 输出实体/格式/单位/数量，以及失败时的返回形式 | `spec.metadata` 输出合同 |
| 20 | 依赖资源 | 文本 | 选填 | 必需的数据、模型、服务、库；跨资产依赖写清资产名称 | `spec.artifacts` + 交叉依赖 |
| 21 | 运行环境\|资源要求 | 长文本 | 可执行资产必填 | 系统、运行时与依赖版本、CPU/GPU、内存、网络与访问策略 | `spec.metadata.runtime` |
| 22 | 评价指标\|验收条件 | 长文本 | 建议必填 | 先写覆盖了哪几类检查与检查点，再写阈值与通过数量；不得只写"N/N 通过" | 本次 `cases.json`/`comparison.json` 或已有报告 |
| 23 | 敏感级别 | 单选 | 必填 | `内部`/`敏感`/`高度敏感`；含个人、医疗或受限第三方资料的资产不得填「内部」 | `governance.access`/`privacy` |
| 24 | 质量说明 | 长文本 | 建议必填 | 抽检与测试结果、已知缺陷、适用范围、不确定性与待确认字段 | 本次报告或已有报告 |
| 25 | 资产关联 | 关联/文本 | 建议必填 | 依赖/派生/被引用关系，写资产名称或 `asset_id` | `provenance`/交叉依赖 |

模板若把某字段限定为"是/空"（如内部清单的"校验"），按模板标记，结论写进「质量说明」「评价指标|验收条件」或证据文件，不写进该字段。

## 一资产一行

- 每个独立资产在「资产明细」恰好一行：权重、配置、文档、数据分片作为组成文件；独立数据集、模型、工具、skill 分别登记。
- 行标识用「资产ID + 版本」（资产ID 不含版本号，靠两列一起保证唯一），不用「资产名称 + 版本」；生成 `registration/asset-row-map.json`，记录 `资产名称`、`asset_id`、`version`、`sheet`、`row`、`case_ids`、登记状态，并核对它与 inventory 的资产集合完全相等。
- 若用户另有内部编号规则，编号写在 `asset-row-map.json` 与 `field-evidence.json`，不进对外清单。
- 多个 case 不增加资产行；case_ids 只在行映射与用例明细中出现。

## 用例明细工作表

在输出副本创建 `用例明细`，每个 case 一行，用「资产名称（版本）」关联资产明细。列定义：

| 列名 | 怎么填 |
|---|---|
| 用例编号 | 原始 case_id；跨包冲突时加包前缀并保留原值 |
| 资产名称 | 主资产的登记名称，必须与资产明细某行一致 |
| 关联资产及检查点 | 其他参与资产的名称/版本/角色与实际检查点；无则"不适用" |
| 资产版本 | 对应登记行版本 |
| 用例名称 | 简述任务与检查目标 |
| 用例来源 | given/supplemental + 原始文件与位置 |
| 用例类型 | success/error/workflow/data-check/environment-check 等 |
| 前置条件 | 依赖、已注册 MCP、设备、数据快照 |
| 输入或Prompt | 简短原文或完整输入文件链接 |
| 执行入口 | CLI/API/MCP 工具/Agent prompt 入口与参数 |
| 预期结果 | 必需字段、状态、单位、产物；给定预期文件链接 |
| 真值类型 | regression/experimental/expert/historical-run/rule-based/simulation/none |
| 真值来源 | 文件、样本键/行号、版本、hash；无真值说明原因 |
| 比较规则 | 对齐键、字段、单位换算、忽略项、错误语义 |
| 验收阈值及依据 | atol/rtol 或任务指标阈值及协议来源；缺失填待确认 |
| 实际输出 | 本次 actual 文件链接；未运行填未执行 |
| 执行状态 | pending/running/completed/failed/blocked/skipped |
| 验证结论 | pass/fail/not_evaluated 及简短原因；completed 不等于 pass |
| 误差或检查结果 | 关键误差及单位、结构或错误语义检查结果 |
| 证据与报告 | 请求、响应、日志、comparison 与报告链接 |
| 备注 | 重试、警告、限制、未覆盖项 |

`cases.json` 是完整机器记录，上表只是可读索引；每例至少含 `case_id, asset_id, asset_version, related_assets, source, kind, preconditions, input/prompt, entrypoint, expected, truth_kind, truth_path, comparison, threshold_source, actual_path, execution_status, validation_status, evidence`。路径统一相对 run 根目录。

## 资产协议与报告链接往哪写

- **资产协议**：只在 `asset.json` 的 `spec.asset_protocol`（`repository_uri`+`revision`+`path`），表格不设列。表中最多在「依赖资源」或「质量说明」提一句协议仓库已随包提供。
- **验证报告路径**：写 `registration/field-evidence.json`；用户要求表内可见时，放「质量说明」末尾一句"核验报告见 <相对路径>"，或按模板提供链接时用单个 `=HYPERLINK(...)`。不要写占位链接，不要写 `==HYPERLINK`。

## 描述口径

- 整行使用一致的名称、功能、输入输出与版本口径；每格一句话或少量短语，必要时补关键数值、单位或版本。
- 详细 case 清单、执行命令、完整依赖、容差推导与逐字段误差放用例明细和证据文件，不塞进单元格。
- 有适用报告时直接概括报告结论；报告没有支持的数值或结论不补写。缺信息写"待补/未验证/不适用"，原因留证据文件。
- 不添加泛化的防御性声明，只写会影响使用或验收结论的实际问题。

## 平台登记与完成检查

平台实体按当前 schema 构建并按引用依赖顺序提交，记录实体 ID 与回执；数据关联真实文件、模型关联权重或服务实体、工具用实际接口 schema、轨迹来自真实运行、benchmark 引用所需任务/数据/环境/协议。平台状态与 Excel 填写状态分别记录；没有平台接口时完成表格与实体草稿并标"平台未注册"，不能把填完 Excel 称作注册成功。

完成条件（逐项核对）：

- [ ] 工作簿可重新打开；`python scripts/check_list.py <xlsx>` 无 error（表头逐列对齐内置 schema、枚举合法、必填已填、版本语义化、行标识唯一、用例明细引用可解析）。
- [ ] 每个登记范围内的资产恰好一行，`asset-row-map.json` 与 inventory 的资产集合相等。
- [ ] 枚举字段取值在词表内；必填字段无空值，或空值原因在 `field-evidence.json`。
- [ ] 「评价指标|验收条件」能看出覆盖了哪几类用例；通过率注明分母（未执行项不计入分母且显式标注）。
- [ ] 用例明细的「资产名称」都能在资产明细中解析；用例编号唯一。
- [ ] 表内数值与所引用报告、`comparison.json` 一致；未验证项标未验证。
- [ ] 同一资产的摘要、任务描述、输入、输出、质量说明口径一致，单位不冲突。
