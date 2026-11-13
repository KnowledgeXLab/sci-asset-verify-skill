# vendor/

随 skill 分发的第三方依赖，装它就是为了「干净环境 / 离线机器也能直接跑」，不要求使用者自己 pip install。

| 包 | 版本 | 许可 | 用途 |
|---|---|---|---|
| openpyxl | 3.1.5 | MIT | 生成对外资产清单 xlsx |
| et_xmlfile | 2.0.0 | MIT | openpyxl 依赖 |
| jsonschema | 4.26.0 | MIT | 包络 schema 交叉校验（`validate_envelope.py`） |
| jsonschema-specifications | 2025.9.1 | MIT | jsonschema 依赖 |
| referencing | 0.37.0 | MIT | jsonschema 依赖 |
| rpds-py | 2026.9.1 | MIT | jsonschema 依赖（预编译扩展） |
| attrs | 26.1.0 | MIT | jsonschema 依赖 |

各包的许可证原文在同目录的 `*-LICENSE.rst`。

## 解析顺序

- `openpyxl`：`scripts/_deps.py` 负责，顺序是「环境已装 → 本目录 → 自动安装 → 报错」。
- `jsonschema`：`validate_envelope.py` 先试环境，再回退到本目录；两者都没有时用脚本内置的轻量校验器，
  并在报告里如实标注用的是哪个引擎（不会静默跳过）。

`rpds-py` 是预编译扩展，与平台（linux x86_64 / CPython 3.14）绑定；换平台时重新执行
`python -m pip install --target vendor --no-compile jsonschema` 即可。
# vendor/ — 随 skill 分发的第三方依赖

这里的包**随 skill 一起分发**，目的是让清单表生成（`build_list.py` / `check_list.py`）在干净环境里也能跑，
不需要用户先 `pip install`。`scripts/_deps.py` 会把这个目录加入 `sys.path`。

| 包 | 版本 | 许可证 | 用途 |
|---|---|---|---|
| `openpyxl` | 3.1.5 | MIT（见 `openpyxl-LICENSE.rst`） | 生成/读取 `.xlsx` 清单表 |
| `et_xmlfile` | 2.0.0 | MIT（见 `et_xmlfile-LICENSE.rst`） | openpyxl 的 XML 写入依赖 |

两者都是**纯 Python、跨平台、无编译扩展**，可直接随仓库分发。

## 解析顺序（`scripts/_deps.py`）

1. 当前环境已装且能 import 的 `openpyxl`（优先用用户自己的）
2. 本目录（`vendor/`）
3. 自动安装：`python -m pip install --target vendor "openpyxl>=3.1,<4"`（可用 `ACI_ASSET_VERIFY_AUTO_INSTALL=0` 关闭）

三个都不成时抛出带修复命令的明确错误，不会静默失败。

## 升级方法

```bash
python -m pip install --target vendor --upgrade "openpyxl>=3.1,<4"
find vendor -name __pycache__ -type d -prune -exec rm -rf {} +
# 同步更新 vendor/*-LICENSE.rst 与本文件的版本号
```
