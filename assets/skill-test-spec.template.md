# 测试说明文档模板（放在 skill 资产的 `validate/test-spec.md`）

> skill 是给 agent 用的，所以测试必须**上 agent**跑：让 agent 使用这个 skill 完成一个任务，再看产物与门槛。
> 只跑 `validate/` 里的脚本不能算 skill 的验收。下面三节缺一不可。

## Agent

- 建议 agent harness：<如 DSH（DeepSeek Harness）Web 会话 / Claude Code / 平台 Agent>
- 装载方式：<如何把本 skill 交给 agent：放进 skills 目录 / 提供 SKILL.md 路径 / 注册为工具>
- 建议 agent model：<如 DS-4.1 / Sage-A1.5 / gpt-5>
- 是否必须特定模型：<是 / 否>；换模型的差异：<会导致什么差异>
- 备选 harness：<没有建议 harness 时可用的同类 agent 环境>

## 测试任务（怎么测）

- 任务：让 agent 使用本 skill 完成「<一句话任务>」
- 输入：<文件路径 / 参数 / 数据规模 / 提示词要点>
- 期望输出：<产物形态：文件、字段、结构、数量>
- 提示词模板：

```
<可直接粘给 agent 的指令，写清要它用哪个 skill、读什么、产出什么、输出到哪>
```

## 通过门槛

- 门槛类型：<指标 / 脚本 / 两者>
- 指标：<id、方向、阈值，例如 value_est_exact = 0（逐位相等）>
- 验证脚本：<如 validate/validate.py --case {case_id} --out-dir {out_dir}，对 agent 产物做校验>
- 判定口径：<满足哪些条件算通过；未执行怎么记>
