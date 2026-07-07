# Loop Engineering Research Demo

这是一个只保留 **research loop** 的 Loop Engineering 教学仓库。

这个案例用于演示：如何在 Codex CLI 之外写一个外部控制器，让 Codex 作为执行 agent 完成联网调研、来源整理和中文报告写作。
当前目标不是生成短摘要，而是生成一份可用于 agent 核心团队技术对齐的长报告。

## 这个 Loop 做什么

默认任务：

```text
调研：Agent 自主进化的技术路线、工程实现方式和评估方法
产出：research_report.md
```

运行后，外部 Python loop 会：

1. 准备隔离工作区。
2. 生成 `brief.md` 和 `rubric.md`。
3. 触发第 `round_N` 轮目标，把上一轮验证反馈交给 Codex writer agent。
4. 用 `codex exec --search` 让 Codex 联网搜索、整理来源、写报告。
5. 保存每轮 prompt、validation、Codex 输出和日志。
6. 每轮 writer 结束后，运行 deterministic validator 检查硬规则。
7. 每轮都调用只读 Codex validator agent 做语义质量评审。
8. 如果没通过，或 `agent_review` 里还有未解决反馈，把合并后的验证结果写入状态，触发下一轮。
9. 只有报告通过混合验证，且 `agent_review` 为空，或轮数用完时停止。

## 运行

先查看会发给 Codex 的命令和 prompt，但不真正调用 Codex：

```bash
python3 run_research_loop.py --dry-run
```

真正运行：

```bash
python3 run_research_loop.py
```

常用参数：

```bash
python3 run_research_loop.py \
  --topic "Agent 自主进化的技术路线、工程实现方式和评估方法" \
  --max-rounds 5 \
  --min-sources 15 \
  --source-year 2026 \
  --min-source-year-ratio 0.8 \
  --min-section-chars 1800 \
  --model gpt-5.5 \
  --validator-model gpt-5.5 \
  --validator-reasoning-effort xhigh
```

`--model` 只控制 writer agent；`--validator-model` 和 `--validator-reasoning-effort` 只控制 validator agent。默认 validator 使用 `gpt-5.5` 和 `model_reasoning_effort="xhigh"`。如果传入 `extra-high` 或 `extra high`，入口会自动转换为 Codex CLI 支持的 `xhigh`。

默认输出目录：

```text
.research_loop_workspaces/agent_evolution
```

## 产物

工作区里会生成：

```text
brief.md                         调研任务
rubric.md                        报告评分标准
sources.json                     Codex 搜索和整理的来源
research_report.md               中文调研报告
round_1_validation.json           外部验证结果
round_1_codex_prompt.md            发给 Codex 的 prompt
round_1_codex.md                   Codex 最终回复
round_1_codex.stdout.log           Codex stdout
round_1_codex.stderr.log           Codex stderr
round_1_rule_validation.json        规则 validator 结果
round_1_agent_validation.json       Codex validator agent 结果，每轮都会生成
round_1_validator_prompt.md         发给 validator agent 的 prompt
```

## Loop Engineering 对应关系

| 要素 | 当前实现 |
|---|---|
| Automations | `run_research_loop.py` 触发和控制循环 |
| Workspaces | `.research_loop_workspaces/agent_evolution` 隔离工作区 |
| Skills | `brief.md`、`rubric.md`、动态生成的 Codex prompt |
| Connectors | Codex CLI 和 Codex CLI 的 `--search` |
| Subagent / Evaluator | Codex writer 负责搜索和写作，默认沿用 Codex CLI 当前模型，可用 `--model` 覆盖；`validator.py` 负责硬规则；Codex validator agent 负责语义质量评审，默认使用 `gpt-5.5` + `model_reasoning_effort="xhigh"` |
| Memory-State | `sources.json`、`research_report.md`、validation、prompt、日志全部落盘 |
| 硬闸门 | 验证通过、Codex 失败、修改受保护文件、轮数用完、来源年份比例不达标、质量门禁不达标都会停止 |

## 代码结构

```text
run_research_loop.py                  CLI 入口
research/codex_runner.py
                                      外部 loop controller
research/validator.py
                                      deterministic validator
research/templates/
                                      brief、rubric、writer prompt、validator prompt 模板
```

Codex 是 loop 里的执行者，不是 loop 本身。真正的 Loop Engineering 发生在 `codex_runner.py` 这个外部控制器里：它负责状态、权限、预算、验证、反馈和停止条件。

当前 validator 是 hybrid validator：

- deterministic validator 检查硬规则。
- Codex validator agent 每轮都在只读 sandbox 中评审语义质量，并输出结构化 JSON。
- validator agent 必须输出 `scorecard`，覆盖 `topic_coverage`、`technical_routes`、`mechanism_depth`、`source_grounding`、`engineering_usability`、`evaluation_design`、`adoption_guidance`、`readability` 8 个维度。
- controller 合并两类结果，只有两者都通过、agent validator 总分达到 96、scorecard 所有维度达标、没有 critical、major 不超过 1 个、没有 `modification_suggestions`，且 `agent_review` 为空，才停止。
- `mechanism_depth`、`source_grounding`、`engineering_usability`、`evaluation_design` 是更严格的核心维度，分数必须至少 92；其他维度至少 90。
- scorecard 任一维度存在 `blocking_notes` 时，本轮不能通过。
- `non_blocking_findings`、`residual_risks`、`source_audit_notes` 和 `next_improvements` 现在表示未解决 review backlog。字段名保留是为了兼容旧 JSON schema；它们只要非空，本轮就不能通过。

deterministic validator 默认检查：

- 至少 80% 的来源 `date` 字段必须来自 2026 年。
- 来源必须字段完整、ID 连续、URL 不重复。
- 每个正文章节必须有足够长度和有效引用。
- 每个来源必须在正文中被引用，不能只出现在参考来源列表。
- 必须包含 `技术全景与发展脉络`、`关键技术路线详解`、`工程实现与代码设计`、`落地选型与使用建议`、`评估方法与实验设计`、`参考来源`。
- `关键技术路线详解` 必须包含 Mermaid 技术路线图。
- `工程实现与代码设计` 必须覆盖 loop、验证器、状态、权限、人工升级，并包含代码片段和 Mermaid 代码流程图。
- 默认不要求固定修订 2 轮；只要 deterministic validator 与 Codex validator agent 都通过，且 agent validator 没有修改建议，就可以停止。

来源年份规则能确定性验证结构化日期比例；如果要进一步证明网页真实发布日期和 `date` 字段一致，需要增加联网抓取或来源元数据校验。

需要改调研任务、rubric 或发给 Codex 的自然语言 prompt 时，优先改：

```text
research/templates/brief.md
research/templates/rubric.md
research/templates/codex_prompt.md
research/templates/validator_prompt.md
```

Python 代码只负责读取模板、填充变量、运行 Codex writer/validator agent、做确定性验证、合并验证结果并控制停止。

这个案例想表达的是：不是让 AI 一次性把调研报告写完，而是设计一个系统，让它持续在“搜索/写作 -> 验证 -> 反馈 -> 修订”之间循环，直到有客观理由停止。
