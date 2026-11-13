---
name: sci-asset-verify-skill
description: 科学数字资产交付与验收：阅读资产包并提炼使用方法，按科学数字资产清单登记，注册 MCP、部署模型，执行给定 case，与 expected output 对比并生成简洁的结果对比报告。用于资产包接入、复现和整体验收。
---

# 科学数字资产交付与验收

把资产包变成可使用、可登记、可调用、可验证的交付物。支持 data、tool、model、environment、skill、task、trajectory、benchmark；按实际存在的资产执行对应分支。

## 1. 确定范围、阅读并建立使用说明

以用户指定的原始资产目录为资产根目录，按 [references/local-assets.md](references/local-assets.md) 发现资产与入口。所有路径、服务和版本均从当前资产包读取。

先枚举 README、交付说明、asset_protocol、SKILL.md、接口 schema、依赖、权重及校验文件、数据、examples/cases、真值和历史报告。忽略已有 `asset-runs/`、缓存、AppleDouble `._*`；重复包和版本分别标记来源，比较 manifest/权重 hash 与入口后确定目标版本，不混用旧权重、新代码和另一版本基准。已有报告可用于登记其验证结果；执行新验证时以原始输入、代码及响应为依据。

在原始资产根目录下创建 `<资产根目录>/asset-runs/<run_id>/`，中间结果和最终产物统一放入该目录。后文产物路径均相对此 run 目录。每次新运行使用唯一 run_id；恢复时复用原目录。保留原始资产文件，盘点时排除 asset-runs，避免将运行产物再次登记为资产。写入：
- `inventory.json`：资产 ID/类型/版本/来源路径/关键文件 SHA256/关联资产/证据路径/待补信息。
- `envelope-report.json`：**只校验、不构造**。用 `scripts/validate_envelope.py` 校验包内已有资产描述文件（`asset.json`/`catalog.json`）是否符合规范 §4 公共包络（schema 见 `assets/asset-envelope.schema.json`，含 §5 类型专有要求与 §6/§7 约束），逐资产给出 error/warning 与字段路径，并核对**本地实体是否真的在包内**（路径可达、`size_bytes`/`sha256` 一致，≤64MB 的文件实测摘要）；schema 交叉校验有 `jsonschema` 就用、没有就用内置轻量实现，报告中如实标注引擎；包内没有描述文件时记 `no_envelope` 并说明缺失，不由 skill 代写包络。
- `usage.md`：各资产用途、适用边界、输入字段及单位、输出字段及单位、运行环境、实际入口与示例命令；明确本地路径、服务端路径和下载 URL。
- `state.json`：各阶段 pending/running/completed/failed/blocked/not_applicable 的**分型值**（见第 3 步）、产物路径及恢复入口。

**在副本里执行，产物写回原包**：所有会运行包内代码、写中间文件、需要清空输出目录的操作都在副本中进行，用 `scripts/work_copy.py` 管理：

```
python scripts/work_copy.py init  <资产根目录> --run-id <run_id> --manifest   # 打印副本路径
python scripts/work_copy.py clean <资产根目录> --run-id <run_id> --verify     # 删副本 + 复核原包摘要
```

- 副本默认放 `<资产根目录父目录>/.asset-work/<run_id>/`（可用 `--work-root` 或 `ASSET_WORK_ROOT` 覆盖，不可写时回退 `$TMPDIR`）；复制时跳过 `asset-runs/`、`__pycache__`、`*.pyc`。
- `init` 复制完会**自检副本完整性**：文件数与字节数必须与源一致。**不核验、也不生成包根 `SHA256SUMS`**——它常把历史 run 产物一并登记，且不是交付判据；实体级摘要由 `validate_envelope.py` 按 `asset.json` 里声明的 `sha256` 核对。任一项不符就删掉副本、把原因写进 `work-copy.json` 并以非 0 退出——不允许在"可能缺文件"的副本上跑用例。
- **历史批次只增不改**：每批核验一律新建 `asset-runs/<run_id>/`，**不得删除、覆盖或清理已有的 run 目录**；需要重跑就换新 `run_id`，旧批次原样保留作为审计链。确认要删除某个旧批次时必须先问用户。
- **原始包只写一处**：`<资产根目录>/asset-runs/<run_id>/`（最终产物：inventory/usage/state/cases/comparison/validation-report/登记表/部署记录，以及 `work-copy.json` 副本记录）。
- 副本内执行时继续设 `PYTHONDONTWRITEBYTECODE=1`（避免 `.pyc` 污染），shell 里比对文本时统一加 `LC_ALL=C`（中文 locale 下部分命令会输出「成功」而非 `OK`，直接比对会误判）。
- `clean --verify` 会删副本并复核原包文件数/字节数，结果写进 `work-copy.json`；清理失败或复核不通过要记为阻塞，不能静默留副本。副本路径必须位于资产根目录之外，脚本会拒绝删除根目录内的路径。
- **中间产出不随包交付**：case 跑完、报告与覆盖门禁写完后，用 `scripts/prune_evidence.py --run <run>` 清理逐 case 工作目录（`*/work/`，含 `out/` 与日志）、可由包内脚本重建的数据/参考目录（`_prepared/`、`refwork/`、`data-all/`、`data-offline/` 等）、探针 `out/`，以及 `registration/` 之外的大体积实体文件，**只保留报告类证据**（`*.md`/`*.json`/`*.txt`/`*.log`/`*.xlsx`、`registration/`、`scripts/`、`deployment/probes/*/probe.json`）。清理清单与前后体积写 `evidence-prune.json`，台账里被清理的 `actual_output` 标 `actual_pruned: true`，脚本同时把「证据保留口径」小节注入 `validation-report.md`。不进包的只是**可用原命令重跑复现**的原始输出；逐 case 的检查数值与判定已在 `cases.json`/`comparison.json`/执行报告里。要打包交付或长期归档时先跑这一步，避免一个几 MB 的工具包被 GB 级中间产物撑大。

逐项建立“case → 输入 → 执行入口 → 输出 → 真值 → 比较规则”的映射。范围或目标平台缺失时先完成本地分析；仅询问影响执行的缺失信息。用户只要编写 skill/方案时交付文档，不启动业务计算或外部注册。

完成条件：每项范围内资产均有类型、入口或不可执行理由、依赖、输入输出、来源证据；缺失信息显式列出。

**资产自包含校验（必做，盘完立刻做）**

资产入库后是**独立存在的单元**——别人只拿走一个资产目录。因此资产目录内的任何文件都不得引用目录外的文件/目录，
唯一允许的外部引用方式是包根 `external_assets.json` 按资产编号声明（见第 2 步与 `references/registration.md`）。

```
python scripts/check_selfcontained.py --package <资产根目录> --json-out <run>/selfcontained-report.json
```

它逐个资产检查两类东西：① `asset.json` 里的本地路径（`spec.artifacts[].uri`、`spec.asset_protocol` 的 `repository_uri`/`path`）；
② 资产目录内脚本/配置/文档里的 `../`、`/home/…`、`C:\…`、`~/` 等引用。解析后越出资产目录即违规，报告会指出是
「指向包内另一个资产」（应改用 `external_assets.json`）、「指向包内共享位置」（应随资产目录交付）还是「指向包外本机路径」。
文档（`.md`/`.txt`）里的路径只算提示，不算违规。`validate_envelope.py` 对 `asset.json` 里的实体与协议路径已按同一规则判 error。

**越界即 P0 阻断**：不得因为"打包时还在同一个压缩包里"就放行。

## 2. 资产登记填表（对外发版资产清单）

读取 [references/registration.md](references/registration.md)，逐列填法、长度基准和真实样例见 [references/registration-examples.md](references/registration-examples.md)。填的是**对外发版资产清单**（规范 §7，23 列，首列为资产ID），不是内部管理清单；内部管理用的资产编号、校验、资产校验报告、记录人、集成负责人、集成状态不进对外清单。

**表头以 skill 内置 schema 为准**：填表标准是 `assets/external-list-schema.json`（对外发版清单 23 个字段的名称、顺序、类型、枚举与填写口径，取自规范 §7.3）。**不检查、不套用用户自定义模板**，也不为了适配某个模板改写字段集；飞书 CLI 在外发环境不可用，故表头已固化在该文件里。用户显式指定另一份 schema 时，才把该 schema 落成 `registration/schema-snapshot.json` 并记录与内置 schema 的差异，否则一律以内置为准。**资产ID 由包络生成，不手写**：先跑 `python scripts/asset_ids.py --package <资产根目录> --rows rows.json --inject`，它把包络里的 `asset_id` 去掉 `sciasset://` 前缀写进「资产ID」列，即 `{project-id}/{namespace}/{asset-type}/{slug}`——**不含版本号**（规范 §4.1 要求 asset_id 稳定），版本由清单的「版本」列表达。

**外部资产引用是可选文件，存在即校验**：包根目录若有 `external_assets.json`（声明包内未随附、需平台按资产编号解析的资产），先跑 `python scripts/external_refs.py --package <资产根目录> --rows rows.json --inject`；它校验 asset_id 四段格式、asset_version、target_path（相对 POSIX 路径、不得越出包根）、重复引用，通过后把引用明细写进清单最后一列「资产关联」（`required` 可不填，缺省视为 true）。文件不存在则跳过，该列保持原值。校验不通过时必须停下修包，不能带着不合规的引用继续登记。

**用脚本生成表格，生成即自检**：把每行内容整理成 `rows.json`（`{"rows":[{...}],"cases":[{...}]}`，键名与 schema 的 `header` 一致），然后

```
python scripts/build_list.py --rows rows.json --out registration/科学数字资产清单.xlsx
```

脚本按内置 schema 建表头、加枚举下拉、写「用例明细」与「字段口径说明」，生成后**自动调用 `scripts/check_list.py`** 自检（表头逐列一致、枚举合法、必填已填、版本语义化、`资产名称+版本` 唯一、用例明细引用可解析）；自检不通过时以非 0 退出并保留 xlsx 供修表。只有用户显式指定其他 schema 时才加 `--schema`。

**不需要事先安装 openpyxl**：`scripts/_deps.py` 依次尝试「环境已装的 → skill 自带 `vendor/`（openpyxl 3.1.5 + et_xmlfile 2.0.0，纯 Python 随仓库分发）→ 自动安装到 `vendor/`」，三者都不成才报错并给出修复命令（`ACI_ASSET_VERIFY_AUTO_INSTALL=0` 可关闭自动安装，离线环境靠 `vendor/`）。生成日志会打印实际用的 openpyxl 版本与来源，写报告时可据此注明。

输出 `registration/科学数字资产清单.xlsx`（由 build_list.py 生成并通过自检）、`registration/check-list-report.json`（自检结果落盘）、`registration/field-evidence.json`、`registration/asset-row-map.json`。严格执行"一资产一行"：每个独立资产在资产明细恰好一行，23 个字段逐项处理；行标识用「资产ID + 版本」，`asset_id` 记在 asset-row-map.json，内部编号（如有）记在行映射与证据文件。以 asset-row-map.json 核对 inventory 与 Excel 的登记集合；缺 case/真值/执行条件也必须登记。**资产协议不入表**——它只写在 `asset.json` 的 `spec.asset_protocol`；验证报告路径写 field-evidence.json，必要时在「质量说明」末尾一句指路。表内描述简短、口径一致，详细来源与执行状态放证据文件；仅填表时可直接采用匹配当前资产与版本的已有报告结论。

登记表填写和平台实体注册分别记状态；登记表本身不是验收结论，结论以 `validation-report.md` 为准。若用户要求平台注册，按平台当前 schema 生成实体、按引用依赖顺序提交，并保存实体 ID/回执。没有平台接口时完成表格和可生成的实体草稿，标记“平台未注册”，保留所需信息，不能把填完 Excel 称为平台注册成功。

完成条件：表格可重新打开，表头与 `assets/external-list-schema.json` 逐列一致（`scripts/check_list.py` 无 error；若用户显式指定其他 schema，则以 schema-snapshot 为准且差异已在 field-map.json 说明）、每个资产恰好一行、必填项缺口明确、枚举值在词表内、关联可解析，表内结论与所引用报告/`comparison.json` 一致。

## 3. 注册 MCP、部署模型

读取包内配置和启动脚本；记录计划使用的解释器、工作目录、依赖、权重、设备、端口和客户端。复用可用服务；检查现有配置后仅增加或更新目标条目。已获得的部署/注册授权沿用，正常执行；缺目标平台、凭证或所需权限时完成本地配置和检查，并明确具体阻塞，不设置额外的固定审批步骤。

**有 MCP：** 区分 stdio 和 Streamable HTTP，依据目标客户端真实配置格式注册。stdio 使用绝对解释器/脚本路径并确保依赖和资源路径可解析；HTTP 使用文档完整端点。保存脱敏配置、注册结果，执行 initialize、tools/list，核对工具名称及 schema，再做最小业务调用。仅生成 JSON、进程启动或 HTTP 200 都不足以判定注册成功。没有可操作客户端时，区分“直连验证成功”和“客户端未注册”。

**有模型：** 核对权重与预处理/词表/描述符/模型架构版本，按提供环境建立隔离运行环境；优先复用包内推理或服务入口。CLI/库可调用部署与常驻服务部署分别记录；用户要求服务时必须有可访问推理端点、进程管理及启动/停止方法。记录实际加载权重、hash、设备、依赖版本、命令、日志和一次推理响应。仅有远程模型别名时记录远程依赖，不声称本地部署权重。只有权重无入口时，依据架构说明构建最小适配器；架构信息缺失则阻塞该模型分支。

部署与探针在副本中执行（`scripts/work_copy.py init` 之后 cd 进副本），`deployment/` 仍写回原始包的 run 目录。凭证通过环境变量/凭证存储引用，日志不包含密钥。包内没有 MCP 端点或没有可部署组件时，不要笼统写 `not_applicable`，按两种情况区分并记入 state.json：

- `not_applicable_no_such_asset`：包内确实没有该类型资产（例如纯数据/工具包没有模型、没有 MCP 服务）——这是合规跳过；
- `not_supported_by_skill`：包内有该类型资产，但本 skill 没有对应验收步骤（environment 的镜像/硬件/网络策略、trajectory 的 ATIF 结构、benchmark 的 metrics/submission/组合引用等）——这是能力缺口，必须在 state.json 与交付说明里写明"未覆盖"，不得用 not_applicable 掩盖。

没有 MCP 仅有函数描述或 CLI 时，记录"无 MCP 端点，采用 CLI/库直连验证"，并保留最小业务调用（含一次预期失败调用）的请求、响应与退出码。

## 4. 跑给定 case

运行前读取 [references/validation.md](references/validation.md)，按其中的用例规范生成 `cases.json` 并固定比较协议；runner 在副本中跑，`actual/`、`responses/`、日志与报告写回原始包 run 目录。在输出 Excel 的“用例明细”按一 case 一行填写，用“资产名称（版本）”关联资产明细（对外清单没有资产编号列，内部编号如存在则放在 asset-row-map.json）；多个 case 不重复新增资产行。优先使用包内 runner/verifier；先检查其覆盖范围、默认输出目录、环境和是否会覆盖基准，必要时在副本中运行。平台 Agent 用例按给定 prompt 和工具链运行；直接 Python 推理只能证明本地入口，不能替代 MCP/Agent 接入验收。

每个 case 保存原始输入、实际输出、stdout/stderr、退出码、时间、模型/服务版本；每次工具调用立即保存 `sequence/service/tool/arguments/response`，包括失败、重试和轮询。脱敏认证数据，科学响应保留完整。以本次结果填充 `actual/` 和 `responses/`，不得复制历史 expected_output 作为新输出。

异步任务保存真实 task_id 并查询该任务；超时标记未完成，避免重复提交。遵循资产自己的轮询/重试协议；未规定时只对短暂连接失败至多重试两次，参数错误修正后单独记录，持续相同失败停止该 case。独立 case 可继续。中断恢复前核对输入、版本和协议，再复用同一 run 的完成产物。

全部 case 跑完后**清理中间产出**（在写报告与覆盖门禁之后、打包或交付之前）：

```
python scripts/prune_evidence.py --run <资产根目录>/asset-runs/<run_id> [--keep-work] [--dry-run]
```

它按保留规则删掉 `*/work/`、`_prepared/`/`refwork/`/`data-all/`/`data-offline/` 这类可重建目录、`deployment/probes/*/out/` 与 `registration/` 之外的大体积实体文件，把清理清单与前后体积写进 `evidence-prune.json`，给 `cases.json`/`comparison.json` 里被删的 `actual_output` 打 `actual_pruned: true`，并把「证据保留口径」小节注入 `validation-report.md`。不要用 `rm -rf` 手工删——台账标记、报告口径和清理记录要一起落下。需要留原始输出时加 `--keep-work`（只清大体积实体文件）。

**收尾最后一步：写上校验 skill 版本**（必须在 prune 之后）：

```
python scripts/stamp_report.py --run <资产根目录>/asset-runs/<run_id>
```

它把「校验工具版本」小节写到 `validation-report.md` **末尾**（`<!-- validation-tool:begin/end -->` 标记块，幂等），并给 run 内所有 dict 型证据 JSON 补 `skill` 字段（`{name, version, commit, version_source}`）。各脚本产出的 JSON 由脚本自己打戳；`cases.json`/`comparison.json`/`state.json` 这类由执行方整理的台账靠这一步补齐。版本号唯一来源是仓库根 `VERSION` 文件，有 git 时同时记提交号。补录历史批次用 `--version/--commit` 显式指定，正常流程不要用。

完成条件：清单中所有 case 都有执行状态和证据；预期失败用例收到约定错误算通过，运行环境故障不能代替预期错误；`evidence-prune.json` 存在且 `validation-report.md` 含「证据保留口径」与「校验工具版本」两个小节。

## 5. 真值比较、报告和回填

按固定协议输出逐字段 `comparison.json` 以及中文 `validation-report.md`，格式见验证参考。写报告前先跑覆盖度门禁：`python scripts/check_coverage.py --cases cases.json --inventory inventory.json --run-dir . --report validation-report.md`，它按 `assets/case-minimums.json` 的每类型下限核对用例数，把覆盖度小节注入报告并写 `coverage.json`；任何类型低于下限（或包内有该类型资产却没有用例）时报告必须写「覆盖不足」，不得宣称闭环完成。报告只写本次运行结果与 expected output 的对比和判定，保持简短；**问题清单固定分三级：P0 阻断（不可计为有效交付）、P1 重要（规范强制要求不符）、P2 建议（可改进）**，每条带编号（P0-1…）与证据，某一级为空写「无」，三级全空时写「未发现问题」；`asset_id` 类问题的整改要求要照抄完整格式规范（`sciasset://{project-id}/{namespace}/{asset-type}/{slug}`，project-id 为项目编号（见项目任务书首页）、namespace 自拟、asset-type 为资产类型、slug 为资产名称）；表格单元格内的 `|` 必须写成 `\|`，否则切错列；写完后跑 `python scripts/check_report.py --report validation-report.md` 自查，缺分级或表格竖线不一致会直接报错；模型只做推理并与 expected output 比较，不与训练日志比较，不写资产登记、部署、哈希等内容。没有 expected output 或阈值时给出差值与“未判定”，不宣称通过。

根据验证报告中的结果回填 Excel 的“评价指标∣验收条件”和“质量说明”；验收条件先概括本次跑的是哪几类用例、各自检查什么，再写通过数量和阈值（通过率必须注明分母与范围，未执行项不计入分母且显式标注），不能只写“N/N 通过”。对外清单没有“校验”“资产校验报告”列：结论并入“质量说明”，报告路径写入 field-evidence.json；若用户另用内部清单，按该表模板的规定处理。回填后重新打开工作簿检查。只有所有必需分支完成且证据存在时宣称闭环完成。有阻塞仍生成报告，写明哪些 case 未完成对比及原因。最终提供使用说明、登记表、部署信息、原始记录、比较数据和验证报告路径。

## 6. Sage A1.5 附加测试（不计入判定）

涉及大模型 API 调用的资产（skill、workflow/agent、task、trajectory、benchmark 等）必须在 **Sage A1.5** 上再跑一遍。是否需要测由 `sage_probe.py detect` 判定：递归读取包内 `asset.json`，但**类型只作提示**，真正的判据是包内是否出现 `LLM_*`/`OPENAI_*`/`chat/completions`/model-backed 等证据（人工可 `--assume-llm` 强制）。它回答的是"换到核验方的大模型后端还兼不兼容"，**不是资产合格与否的判据**：结果写进报告独立小节与 `sage-a1.5-report.json`，不进入通过率分母、不进覆盖度门禁，也不改变第 5 步的结论。

先判定要不要测：

```
python scripts/sage_probe.py detect <资产根目录> --json-out <run>/sage-need.json
```

它按资产类型 + 包内证据（`LLM_*`/`OPENAI_*` 环境模板、`chat/completions` 调用、model-backed 适配器、Sage 后端开关、asset.json 的模型依赖）判定；`needed=false` 时记 `not_applicable_no_llm_asset` 并跳过。

需要测时先解析配置，优先级 **CLI > 环境变量 `SAGE_BASE_URL` / `SAGE_MODEL` / `SAGE_API_KEY` > 宿主机的 DSH 配置**（`~/.dsh/profiles/*/cordis.patch.yml` 里 models 含 Sage 的 provider，密钥从环境变量或 `~/.dsh/.credentials.yaml` 只读解析）：

```
python scripts/sage_probe.py config --probe --json-out <run>/sage-config.json
```

- **退出码 0**：配置可用。用资产包自带的正确性 harness 把后端换成 Sage 跑一遍（例如包内 `run_correctness.py --backend-name sage-a1.5 --llm-base-url <SAGE_BASE_URL> --llm-model <SAGE_MODEL> --llm-api-key-env SAGE_API_KEY`），输出放 `<run>/validation-run/correctness-sage/`，再把结果写进 `sage-a1.5-report.json` 与报告小节。
- **退出码 3（配置缺失）**：脚本会打印"需要用户提供"的清单。此时**立刻把这段反馈给用户并等其提供**——不要静默跳过，也不要把通用后端或别的模型的结果代填为 Sage 结果。用户暂时给不了，就在 `sage-a1.5-report.json` 记 `blocked_missing_config`、缺项列进 `state.json` 的 `blocked_items`，报告里写明"未测试"。
- **退出码 4**：配置齐全但探测失败（地址/模型名/密钥无效），同样反馈用户。
- 包内没有可切换后端的 harness 时记 `not_supported_by_package`，说明原命令，不自行实现一刀切对接。

密钥只走环境变量，不写进任何文件、不写进交付包；报告与 JSON 里只记地址、模型名与"是否配置"，不记密钥值。

完成条件：`sage-need.json` 存在；`needed=false`，或 `needed=true` 且 `sage-a1.5-report.json` 有明确状态（`completed` / `blocked_missing_config` / `not_supported_by_package`）；报告含「Sage A1.5 兼容性测试」小节并声明该项不计入判定。

报告与覆盖表写完后跑 `scripts/prune_evidence.py`（见第 4 步），把「证据保留口径」注入报告；没有这一步就说明中间产出还留在 run 目录里，交付前必须补上。要重新打包交付资产包时，先 prune 再压缩，`evidence-prune.json` 与 `validation-report.md` 一起进包。
