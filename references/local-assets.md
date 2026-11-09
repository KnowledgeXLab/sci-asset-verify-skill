# 资产发现与入口确认

以用户指定的原始资产目录为根目录，读取当前包内文档和配置。盘点时排除 `asset-runs/`、缓存及系统辅助文件。

## 阅读顺序

1. 阅读 README、交付说明、资产协议和 manifest，确定资产类型、版本、组成和关联关系。
2. 查找接口定义、依赖声明、启动脚本、权重及数据文件，确认实际入口与运行要求。
3. 收集给定用例、输入、预期输出、验收协议和已有验证报告，记录各自来源。
4. 资产描述文件与登记表：先用 `scripts/validate_envelope.py` 校验包内已有描述文件（`catalog.json`、`assets/*/asset.json`、manifest）是否符合规范 §4 包络，结果写 `envelope-report.json`；包内没有描述文件就记 `no_envelope`，不代写。登记表表头固定取 skill 自带的 `assets/external-list-schema.json`，不查找、不比对用户自定义模板（详见 registration.md）。

## 副本执行与完整性

执行一律在副本中进行：`python scripts/work_copy.py init <资产根目录> --run-id <run_id> --manifest` 生成副本（默认 `<资产根目录父目录>/.asset-work/<run_id>/`），跑完 `clean --verify` 删副本并复核原包摘要。原始包只写 `asset-runs/<run_id>/`。

摘要校验统一用 `LC_ALL=C sha256sum -c SHA256SUMS`：中文 locale 下通过行打印为「成功」，按 `: OK` 匹配会得到 0 行、被误判为全表失败。`__pycache__`/`*.pyc`、缓存等不在 `SHA256SUMS` 内，摘要全绿不能证明包未被运行写入——这正是要在副本里跑的原因。

## 入口与版本

服务地址、传输协议、客户端配置和认证要求以当前包内文档及实际配置为准。模型入口与权重、预处理和架构版本对应；重复副本记录来源，不混用不同版本。

已有验证报告用于填表时，确认资产、版本和结果对应，直接提炼结论，来源记录到 field-evidence.json。执行新验证时，读取原始输入和预期输出，实际响应保存在当前 run 目录。

所有运行产物统一保存到 `<资产根目录>/asset-runs/<run_id>/`，保留原始资产及模板。
