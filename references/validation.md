# 用例协议与验证报告

## skill 资产的测试说明文档（validate/test-spec.md）

skill 是给 agent 用的，验收必须在 agent 上做：让 agent 使用该 skill 完成一个任务，再检查产物。
提供方必须在 `validate/` 下给一份 Markdown 说明（模板 `assets/skill-test-spec.template.md`），四节必填：

```markdown
## Agent Harness
- 建议 harness：DSH（DeepSeek Harness）Web 会话；备选：Claude Code / 平台 Agent
- 装载方式：把资产目录放进 skills 位置 / 给出 SKILL.md 路径

## Agent Model
- 建议模型：DS-4.1；是否必须特定模型：否；换模型的影响：…

## 测试任务（怎么测）
- 任务：让 agent 用本 skill 完成「…」
- 输入：…（文件/参数/提示词要点）
- 期望输出：…（产物形态、字段、数量）
- 提示词模板：可直接粘给 agent 的指令

## 通过门槛
- 指标：`value_est_exact = 0`
- 验证脚本：`validate/validate.py --case {case_id} --out-dir {out_dir}`（对 **agent 产物** 做校验）

## Sage A1.5
- 是否沿用同一 harness：是
```

规则：

- **harness 必须是 agent harness**，文档里要能让校验方知道"把 skill 交给谁、怎么交"。
- **测试任务要写清输入与输出**，否则无法判断 agent 是否完成；只写"跑一下脚本"不合规。
- **通过门槛**给指标或验证脚本，验证脚本判的是 agent 的产物，不是重跑一遍脚本。
- **建议的 harness 找不到时**可用系统里已有的 agent harness 替代，报告须注明「建议 X、实际 Y、原因」。
- **Sage A1.5 沿用同一 harness**（只换后端模型）。
- 只接受 Markdown；`validate/test-spec.json` 已废弃。机检：`scripts/check_skill_spec.py --package <包根> --check`。

## 收集用例

先收集交付包的全部给定用例，保留原始输入、预期输出和出处。每个资产建立 case_ids；无给定用例时标记 `no_provided_case`，可补建 smoke case 验证可调用性，明确来源为新增，科学真值缺失时仍为未验证。多个资产共享端到端case时记录各自检查点，不能把工作流通过直接套用到所有依赖资产。

| 资产类型 | 可用 case/检查点 |
|---|---|
| data | 数据可读、字段/单位、记录数、主键、已知记录查询；具体数值依据冻结数据或提供的参考 |
| model | 给定输入推理、各子模型/规格覆盖、边界及错误输入；权重、预处理、输出字段和误差 |
| tool/MCP | initialize与发现后实际业务调用、合法/非法参数、批量部分失败；模型结果还需单独比对 |
| skill/task | 给定自然语言需求到最终产物的完整链路，记录工具轨迹、参数传递与报告完整性 |
| environment | 依赖导入、权重加载及最小调用；另核对镜像/锁文件/硬件要求/网络与访问策略四项是否声明；只证明环境可运行，不算科学结果通过 |
| trajectory | 格式与结构（ATIF 类格式需 `schema_version`、agent 名称版本、`steps`≥1、步骤与工具调用引用一致）、调用顺序、ID关系、输入输出完整性和真实产物可追溯 |
| benchmark | 评测入口可执行；`metrics[]{id,direction,aggregation}`、`submission`、`datasets[].split/role`、`environment_id` 齐备；任务/数据/环境引用存在；比较器对已知相等/不等结果判定正确 |

没有对应检查点的资产类型不要记成 `not_applicable`：包内确实没有该类型记 `not_applicable_no_such_asset`，有但没有步骤记 `not_supported_by_skill` 并在交付说明写明未覆盖。


## 运行前锁定协议

cases.json 每例包含 case_id、asset_id/version、entrypoint、input、expected_kind(success/error)、truth_path、truth_kind、comparison、threshold_source。记录关键输入/权重/真值 hash。仅在有提供 case 时称为给定用例；补建 smoke case 单独标明，不能代替遗漏用例。

真值即资产给定的 expected output（包内 expected_output、gt/标注、固定输出、实测/专家结果）；判定依据写在 case.json 的检查项里（解析解/独立复算/参考库直算/模拟真值/回归基线/非法输入），阈值与容差逐例记录。模型只做推理，用推理结果与 expected output 比较；不与训练日志、checkpoint 内的验证记录或历史报告中的指标比较。历史 Agent 运行作为真值时，需核验其原始响应、参数及版本；报告文本不自动构成真值。

记录字段映射、匹配键、单位转换、忽略字段与容差。结果与真值按 case_id、样本 ID/规范化科学身份对齐，不凭行顺序连接；检查缺失、重复、多余记录。动态 task_id、路径、时间戳可按协议忽略，但结果指向的资产/结构关系仍需验证。跨运行标识不同，以科学对象与实际条件建立映射。

## 比较规则

- JSON：检查结构、必需字段、类型、数组长度、状态；数字与布尔分别处理。CSV/Excel：检查列、样本键、单位、缺失值。字符串/类别按协议精确匹配，必要的规范化保留原值。
- 数值：`abs(actual - expected) <= atol + rtol * abs(expected)`，逐字段记录单位与误差。阈值来自用户、包内协议或有依据的标准；未知时报告差值与“未判定”。零真值不计算普通百分比相对误差。NaN/Inf/缺失不得静默通过。
- 汇总误差：回归按适用性报告 MAE/RMSE、最大误差，可选 R²（单点或零方差时不定义）；分类/排序用任务指定指标。按目标及单位分组，不同单位的指标分别统计。分位预测检查顺序和区间覆盖；报告样本量及数据来源。
- 预期错误：检查错误码/约定消息、success/isError 和必要退出码；不要求动态 stderr 完全一致。超时、缺依赖、连不上服务属于执行失败，不是正确输入拒绝。
- 科学流程：检查物理条件、权重、收敛性、适用范围、单位及关键派生关系。不能仅凭最终数字接近跳过流程错误。

## 覆盖下限（写报告前必须过）

`assets/case-minimums.json` 规定每类资产的用例下限与必需种类（如 tool ≥3 组且至少 1 组预期错误；data ≥3 组；model ≥2 组且至少 1 组边界/错误输入）。写报告前运行：

```
python scripts/check_coverage.py --cases <run>/cases.json --inventory <run>/inventory.json \
    --run-dir <run> --report <run>/validation-report.md
```

外部资产引用（若资产目录下有 `external_assets.json`）在登记阶段由 `scripts/external_refs.py` 校验，其结果写入清单「资产关联」列；用例执行阶段据此确认被引用资产是否真的可达，不可达时按 `required` 决定是否阻断。

脚本按 `asset_id` 的类型段统计 planned/executed/passed/skipped，与下限比较，把覆盖度小节注入报告（`<!-- coverage:begin -->` 标记块，重复运行原地替换），并写 `coverage.json`。任何类型低于下限或包内有该类型资产却没有用例时：报告必须写「覆盖不足」，**不得宣称闭环完成**；脚本以非 0 退出（`--no-gate` 可只出报告不拦截）。

缺真值标 not_evaluated；未执行标 skipped/blocked；预期错误满足协议可 pass。分母保留全部计划 case，分别给出 planned/executed/pass/fail/blocked/skipped/not_evaluated；通过率明确分母，零分母写不适用。

## 报告与登记表的关系

`validation-report.md` 是唯一验收结论来源；登记表的「评价指标|验收条件」「质量说明」只能转述报告与 `comparison.json` 的内容，并注明通过率分母与未执行范围。表格不构成验收结论。

## 报告产物

`comparison.json` 按 case 保存：输入与真值来源、匹配键、字段 actual/expected/unit/absolute_error/tolerance/status、忽略项、运行状态、科学判定、完整性判定及证据相对路径。

`validation-report.md` 是简洁的结果对比报告。**文件开头必须是 front matter**，记录校验 skill 版本与生成时间，正文里不再重复：

```
---
skill: sci-asset-verify-skill
skill_version: 1.2-beta.3
skill_commit: 20d728a
skill_version_source: VERSION
generated_at: 2026-11-14T11:00:23+08:00
---
```

由 `scripts/stamp_report.py --run <run>` 生成与刷新（幂等）。它只回答“这个工具/skill/模型等跑出来的结果和 expected output 是否一致”。内容：
1. 结论：一两段话给出通过/不通过/未判定，以及最关键的差异数字。
2. 对比对象：被测对象与版本、本次 run 目录、expected output 路径、对比日期，各一行。
3. 结果对比表：按 case 或关键字段列出 expected / actual / 差值 / 判定；case 多时给汇总表（计划/执行/通过/失败/未判定及通过率分母）加关键指标或差异最大的若干条，不逐条罗列全部 case。
4. 问题清单：**无论有没有问题都必须有这一节**，按三个级别罗列，每条带编号、问题与证据、影响或整改要求。

   | 级别 | 含义 | 典型内容 |
   |---|---|---|
   | **P0 阻断** | 不可计为有效交付，必须修完才能交 | 包络不合规、用例无法执行、结果与 expected 不一致、必需输入/实体缺失 |
   | **P1 重要** | 规范强制要求不符，影响复现或验收 | 声明了但没交的实体、入口路径写死、版本号与清单不一致、覆盖不足 |
   | **P2 建议** | 可改进，不构成违规 | 命名/口径不统一、历史产物与本次产物混放、文档表述待澄清 |

   写法：

   ```
   ## 4. 问题清单

   ### P0 阻断
   | 编号 | 问题与证据 | 影响 / 整改要求 |
   |---|---|---|
   | P0-1 | `asset_id` 为三段，缺 project-id（证据：`envelope-report.json`） | 包络不合规，须改为四段后重交 |

   ### P1 重要
   无

   ### P2 建议
   | P2-1 | `artifacts[0].uri` 自指 | 建议改成本目录相对路径 |
   ```

   **写作硬规则两条**：

   - **`asset_id` 类问题的「影响 / 整改要求」列必须写全格式规范**，不能只写"改成四段"。原文照抄：
     > asset_id 格式为 `sciasset://{project-id}/{namespace}/{asset-type}/{slug}`，其中 project-id 为项目编号（见项目任务书首页）、namespace 为自拟命名空间、asset-type 为资产类型、slug 为资产名称
   - **写 markdown 时，表格单元格内的竖线必须转义成 `\|`**，反引号里的也要转义（例如 `--vcf <file.vcf\|file.vcf.gz>`、
     `任务\|操作标签`、`运行环境\|资源要求`、`评价指标\|验收条件`）。裸 `|` 会被渲染器切成额外列，整张表错位——
     这是交付出去的 md 给人看的东西，错位直接影响可信度。只在**写报告/说明时**遵守，不需要额外的机检脚本。

   某一级为空显式写「无」；三级全空时，本节写 **未发现问题**（一句话即可，不要留空、不要省略本节）。
   收尾跑 `python scripts/check_report.py --report <run>/validation-report.md` 自查，缺分级直接报错。
4.5 用例覆盖度：由 `scripts/check_coverage.py` 注入的按类型覆盖表；有类型未达下限时必须在结论里点明「覆盖不足」。
4.6 证据保留口径：由 `scripts/prune_evidence.py` 注入；说明清理了哪些中间产出、保留多少报告类证据，并点明逐 case 的检查数值仍在 `cases.json`/`comparison.json`/执行报告里。
5. 最终判定：维度 | 判定 小表 + 一句综合结论。

不写资产登记、编号、文件哈希、部署改动、环境依赖清单、训练日志/基准复现、科学误差与泛化讨论、复跑命令、待补治理信息；这些留在 inventory.json、deployment/、state.json 和登记表中。报告数值只能来自本次输出与 comparison.json。

交付前核对报告中的 case 总数、对比数值和失败清单与 comparison.json 一致。

## 证据保留（跑完必须清理）

case 跑完后 `*/work/` 这类中间目录会长到 GB 级（例如区域预测类用例的现制真值 `_prepared/`、逐 case 的栅格/表格输出），而验收真正需要交付的是**报告类证据**。在写完报告与覆盖表之后、打包或交付之前执行：

```
python scripts/prune_evidence.py --run <asset-runs>/<run_id>
```

- **保留**：run 目录下的报告与台账（`inventory.json`、`envelope-report.json`、`usage.md`、`state.json`、`cases.json`、`comparison.json`、`coverage.json`、`validation-report.md`、`work-copy.json`、`manifest-before.json`）、`registration/`、`scripts/`、`deployment/*.json` 与 `deployment/probes/*/probe.json`（probe.json 已内嵌 result/error 内容与退出码）。
- **清理**：任意 `*/work/` 目录、可由包内脚本重建的数据/参考目录（`_prepared/`、`refwork/`、`data-all/`、`data-offline/`、`mmwf-data/`）、`deployment/probes/*/out/`、`registration/` 之外的大体积实体文件（`.tif/.csv/.npz/.npy/.shp/.gpkg/.zip/.png` 等）。
- **连带动作**：`cases.json`/`comparison.json` 里指向被删路径的 `actual_output` 打 `actual_pruned: true`（保留原路径便于追溯），`evidence` 只留仍存在的路径；写 `evidence-prune.json`（逐次 pass、前/后体积）；给 `validation-report.md` 注入「证据保留口径」。
- 需要保留原始输出时用 `--keep-work`；只想看会删什么用 `--dry-run`。
- 不要手工 `rm -rf`：台账标记、报告口径与清理记录必须一起落下，否则报告会指向不存在的证据。

## Sage A1.5 附加测试（涉及大模型 API 的资产必做，不计入判定）

有模型 API 调用的资产（skill / workflow / agent / task / trajectory / benchmark）要在 Sage A1.5 上再跑一遍，
用来回答"换后端还兼不兼容"。**它是附加信息，不是合格判据**：不参与通过率与覆盖度分母，也不改变报告第 0 节的结论。

判定与配置用 `scripts/sage_probe.py`：

- `detect` 写 `sage-need.json`：`needed` + 证据（哪一行、哪类线索）；`needed=false` 记 `not_applicable_no_llm_asset`。
- `config` 写 `sage-config.json`：按 CLI > 环境变量 > 宿主机 DSH profile/凭据 的顺序解析 `SAGE_BASE_URL` / `SAGE_MODEL` / `SAGE_API_KEY`，并可选探测（列模型、确认 Sage 在列）。**退出码 3 表示缺配置，
  必须立刻把脚本打印的"需要用户提供"清单反馈给用户**，等其提供后再跑；不得静默跳过或代填其他后端结果。
- `report` 写 `sage-a1.5-report.json`：`status` ∈ {`completed`, `blocked_missing_config`, `not_supported_by_package`}。
  这些文件里只记地址/模型名/是否配置，不记密钥值。

报告里的固定小节（放在覆盖度与「证据保留口径」之后、最终判定之前）：

```markdown
### Sage A1.5 兼容性测试（附加项，不计入判定）

| 项目 | 值 |
|---|---|
| 后端 | `<SAGE_MODEL>`（`<SAGE_BASE_URL>`，密钥经环境变量注入，不落盘） |
| 状态 | completed / blocked_missing_config / not_supported_by_package |
| 结果 | N/M 用例通过，K 项检查；与通用后端结果并列，只作对比 |
| 用途 | 仅反映换后端后的兼容性，**不参与通过率与覆盖度，也不改变第 0 节判定** |

缺失配置时写清缺哪几项、已向用户索取（日期），并保留原始探测记录路径。
```

对照写法：通用后端与 Sage 的用例数、通过数、失败用例并列一行即可；**不要**把两边结果混算成一个通过率。
