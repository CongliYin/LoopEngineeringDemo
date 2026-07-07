# 这个 Research Demo 如何体现 Loop Engineering

这篇文章只解释一件事：**当前仓库里的 research demo 为什么是一个 Loop Engineering demo，以及 Loop 的关键组件分别在代码里怎么实现。**

先给结论：

> 当前实现不是“写一个 prompt 让 Codex 一次性生成报告”，而是一个外部 Python controller 持续驱动 Codex writer agent 和 Codex validator agent，在“目标 -> 写作/修订 -> 验证 -> 更新状态 -> 下一轮目标”之间循环，直到外部验证器有足够理由停止。

这正好对应飞书文档里的定义。

定义来源：

```text
https://wcn6cuivzset.feishu.cn/docx/Sjs0daXt1oMYKhxqENHcg3qInlh
```

> Loop Engineering 是从“我来反复 prompt agent”转向“我设计一个系统，让系统反复 prompt、分配、验证、记录并驱动 agent 前进”。

在这个 demo 里：

- Codex 不是 loop 本身。
- Codex 是被 loop 调度的执行单元。
- 真正的 loop 是 `research/codex_runner.py` 里的外部 controller。

## 1. 从文档定义看：什么才算 Loop Engineering

飞书文档把 AI agent 工程演进分成四层：

```text
Prompt  ->  Context  ->  Harness  ->  Loop
```

四层分别解决不同问题：

```text
Prompt:
  怎么把单次输入写清楚。

Context:
  给模型看什么上下文。

Harness:
  让 agent 如何安全、可控地执行一次任务。

Loop:
  让系统如何持续唤起 agent、分配任务、验证结果、记录状态，并决定下一步。
```

当前 demo 不是只停留在 Prompt 或 Context，因为它不只是把一段长 prompt 发给 Codex。

它也不只是 Harness，因为它不是只运行一次 `codex exec`。

它是 Loop，因为外层 controller 做了这些事：

```text
准备工作区
  -> 构造本轮 goal prompt
  -> 调用 Codex writer agent 执行一次 harness run
  -> 保存产物、日志和验证结果
  -> 运行 deterministic validator 和 Codex validator agent
  -> 把验证结果写回 workspace state
  -> 如果失败，构造下一轮 goal prompt
  -> 直到通过或预算耗尽
```

这就是文档里的核心闭环：

```text
Trigger -> Goal -> Agent Harness Run -> Verify -> Update State -> Next Trigger
```

当前实现已经按这个顺序组织：

```text
Trigger -> Goal -> Codex Writer Run -> Hybrid Verify -> Update Workspace State -> Next Trigger
```

其中 `round_N` 就是第 N 次 Trigger。每个 Trigger 都会生成本轮 writer prompt，运行一次 Codex writer，再运行 deterministic validator 和 Codex validator agent，把验证结果写回 workspace，作为下一次 Trigger 的状态输入。

## 2. Demo 的运行目标

当前 demo 的任务是生成一份中文长报告：

```text
Agent 自主进化的技术路线、工程实现方式与评估方法
```

它不是短摘要，而是面向 agent 核心团队技术对齐的基础材料。

最终报告需要回答：

- 近两年 Agent 自主进化有哪些关键技术路线。
- 每类技术路线的机制是什么。
- 代表来源到底做了什么。
- 工程上怎么实现 loop、状态、验证器、权限和人工升级。
- 不同业务场景应该选什么路线。
- 如何设计可复现评估和线上门禁。

最终产物在 workspace 里：

```text
.research_loop_workspaces/agent_evolution/research_report.md
.research_loop_workspaces/agent_evolution/sources.json
```

## 3. 整体架构

核心代码结构：

```text
run_research_loop.py
  CLI 入口，解析参数，创建 loop config。

research/codex_runner.py
  外部 loop controller，负责调度、状态、权限、验证、停止条件。

research/validator.py
  deterministic validator，负责确定性硬规则检查。

research/templates/brief.md
  任务目标。

research/templates/rubric.md
  报告质量要求。

research/templates/codex_prompt.md
  writer agent prompt 模板。

research/templates/validator_prompt.md
  validator agent prompt 模板。
```

运行入口：

```bash
python3 run_research_loop.py
```

默认配置在 `run_research_loop.py`：

```text
workspace              .research_loop_workspaces/agent_evolution
max_rounds             5
min_sources            15
source_year            2026
min_source_year_ratio  0.8
min_revision_rounds    0
min_section_chars      1800
codex_timeout_seconds  1200
model                  writer agent 模型，可选
validator_model        validator agent 模型，默认 gpt-5.5
validator_reasoning_effort
                       validator agent 推理档位，默认 xhigh，即 Codex CLI 支持的 extra high 档位
enable_search          True
enable_agent_validator True
```

## 4. 一轮到底怎么跑

当前代码里的 `round_N` 是：

```text
第 N 次 Trigger
```

真实顺序是：

```text
Round N:
1. 根据初始目标或上一轮 validation 生成 round_N_codex_prompt.md。
2. 调用 Codex writer agent 写作或修订。
3. 运行 deterministic validator 和 Codex validator agent。
4. 把结果写入 round_N_validation.json。
5. 如果 validation 通过，停止。
6. 如果没通过，进入 Round N+1。
```

对应代码在 `CodexResearchLoopRunner.run()`：

```python
validation = initial_goal_context()

for round_number in range(1, self.config.max_rounds + 1):
    prompt = self._build_codex_prompt(round_number, validation)
    completed = self._run_codex_process(command, prompt)

    validation, hard_error = self._run_external_validation(f"round_{round_number}")

    if validation["passed"]:
        return True
```

第一轮就是标准闭环的第一次 Trigger：

```text
初始化 workspace
  -> 写入 brief.md、rubric.md、空 sources.json
  -> Round 1 生成 initial goal prompt
  -> Round 1 writer 写第一版报告
  -> Round 1 validator 评审第一版报告
  -> 写入 round_1_validation.json
```

## 5. 按文档六个组件映射当前实现

飞书文档里说，一个 loop 通常需要：

```text
Automations
Worktrees
Skills
Plugins / Connectors
Sub-agents / Evaluator
State / Memory
```

当前 demo 对应如下。

### 5.1 Automations：循环的触发和目标

文档里的 Automations 负责让 loop 启动、周期性运行，或一直运行到目标完成。

当前实现：

```text
run_research_loop.py
```

它是当前 demo 的 automation trigger。

触发方式是手动命令：

```bash
python3 run_research_loop.py
```

目标来自三部分：

```text
DEFAULT_TOPIC
brief.md
rubric.md
```

代码位置：

```text
run_research_loop.py
research/templates/brief.md
research/templates/rubric.md
```

合理性判断：

- 对 demo 合理。
- 但还不是生产级 automation。
- 生产级版本可以接 cron、GitHub Actions、CI failure、飞书/Linear issue 等触发源。

### 5.2 Worktrees：隔离执行环境

文档里的 Worktrees 重点是并行 agent 隔离，避免多个 agent 互相踩文件。

当前实现没有使用 Git worktree，而是使用独立 workspace：

```text
.research_loop_workspaces/agent_evolution/
```

初始化逻辑在 `CodexResearchLoopRunner._prepare_workspace()`：

```python
if self.workspace.exists() and not self.config.keep_workspace:
    shutil.rmtree(self.workspace)
self.workspace.mkdir(parents=True, exist_ok=True)
write_text(self.workspace / "brief.md", self._render_template("brief.md"))
write_text(self.workspace / "rubric.md", self._render_template("rubric.md"))
if not (self.workspace / "sources.json").exists():
    write_json(self.workspace / "sources.json", [])
```

合理性判断：

- 对单任务 demo 合理。
- 它实现了运行产物隔离。
- 但它不是 Git worktree，也不支持多个 writer agent 并行竞赛。
- 如果要做 coding loop 或多 agent 并发，需要升级为真正的 `git worktree` 或多 workspace merge 策略。

### 5.3 Skills：把工作流沉淀成可复用规则

文档里的 Skills 是项目知识和流程的外部化。

当前 demo 没有使用正式 `SKILL.md`，但把任务流程和评审规则沉淀在模板里：

```text
research/templates/brief.md
research/templates/rubric.md
research/templates/codex_prompt.md
research/templates/validator_prompt.md
```

其中：

```text
codex_prompt.md
  告诉 writer agent 如何搜索、写 sources.json、写 research_report.md，以及如何根据 validation 修订。

validator_prompt.md
  告诉 validator agent 如何评审、输出 JSON、打 scorecard、提出 blocking notes。
```

合理性判断：

- 对 demo 合理。
- 这些模板已经起到了 skill/workflow 的作用。
- 如果要产品化，可以进一步封装成正式 Codex Skill。

### 5.4 Plugins / Connectors：连接外部工具

文档里的 Connectors 让 loop 接触真实工具，例如 GitHub、CI、Linear、Slack、Sentry、飞书等。

当前 demo 的 connector 只有一个：

```text
Codex CLI --search
```

writer agent 和 validator agent 都可以通过 Codex CLI 联网搜索。

命令构造在：

```text
_codex_command()
_validator_command()
```

关键参数：

```text
--search
```

合理性判断：

- 对 research demo 合理，因为调研任务最需要 web search。
- 但它没有接 GitHub、CI、Linear、Slack、Sentry、飞书文档写回等外部系统。
- 所以它不是完整企业级 connector loop。

### 5.5 Sub-agents / Evaluator：Maker 和 Checker 分离

文档强调 maker/checker 分离，避免 agent 自己给自己打分。

当前实现里有两个 Codex 角色：

```text
writer agent
  负责搜索、写 sources.json、写 research_report.md。
  大模型：默认不在 loop 里硬编码，沿用 Codex CLI 的当前默认模型；可以用 --model 覆盖。
  当前本机配置和最近运行中使用的是 gpt-5.5。

validator agent
  负责只读评审，不允许改文件。
  大模型：默认由 loop 显式指定为 gpt-5.5。
  推理档位：默认 model_reasoning_effort="xhigh"，也就是 Codex CLI 支持的 extra high 档位。
```

writer agent 命令：

```text
codex --cd <workspace> --sandbox workspace-write --search exec ...
```

validator agent 命令：

```text
codex --cd <workspace> --sandbox read-only --search exec ...
```

这就是当前 demo 最符合 Loop Engineering 的地方：

- writer 可以写。
- validator 只能读。
- validator 输出结构化 JSON。
- controller 根据 validator 结果决定是否继续。

合理性判断：

- 很合理。
- 当前已经实现 maker/checker 分离。
- 并且 validator agent 不是唯一裁判，controller 还会对它的输出做二次硬校验。

### 5.6 State / Memory：把状态外部化

文档强调：

> The agent forgets. The repo doesn’t.

当前 demo 把所有状态写入 workspace 文件：

```text
brief.md
rubric.md
sources.json
research_report.md
round_N_rule_validation.json
round_N_agent_validation.json
round_N_validation.json
round_N_codex_prompt.md
round_N_codex.md
round_N_codex.stdout.log
round_N_codex.stderr.log
round_N_validator_prompt.md
round_N_validator.md
round_N_validator.stderr.log
```

下一轮 writer 不靠聊天上下文记忆上一轮发生了什么，而是读取：

```text
round_N_validation.json
research_report.md
sources.json
```

合理性判断：

- 很合理。
- 这是当前 demo 的核心工程点。
- 状态外部化让 loop 可恢复、可审计、可复盘。

## 6. Validator 是这个 demo 的核心

如果没有验证器，这个 demo 就只是“自动重复 prompt”。

当前实现有两层验证。

### 6.1 Deterministic Validator

代码：

```text
research/validator.py
```

入口：

```python
validate_report(...)
```

它检查确定性硬规则：

- `research_report.md` 是否存在。
- 必需章节是否齐全。
- `sources.json` 是否合法。
- 来源 ID 是否连续。
- 来源字段是否完整。
- URL 是否重复。
- 来源数量是否达到 `min_sources`。
- 2026 年来源比例是否达到 80%。
- 每个来源是否在正文中被引用。
- 每个来源是否出现在参考来源章节。
- 每个正文章节是否达到最小长度。
- 每个正文章节是否有有效引用。
- 工程章节是否包含 `loop`、`验证器`、`状态`、`权限`、`人工升级`。
- 工程章节是否包含代码块和 Mermaid 图。
- 关键技术路线章节是否包含 Mermaid 图。

输出文件：

```text
round_N_rule_validation.json
```

### 6.2 Codex Validator Agent

模板：

```text
research/templates/validator_prompt.md
```

它负责语义质量评审：

- 主题是否聚焦 Agent 自主进化。
- 技术路线是否完整。
- 机制是否讲清状态、触发器、优化目标、验证器和失败模式。
- 来源是否真的支撑正文。
- 工程方案是否能落地。
- 评估方案是否可复现。
- 选型建议是否能指导团队决策。
- 阅读组织是否适合作为技术分享底稿。

它必须输出 8 维 scorecard：

```text
topic_coverage
technical_routes
mechanism_depth
source_grounding
engineering_usability
evaluation_design
adoption_guidance
readability
```

输出文件：

```text
round_N_agent_validation.json
```

### 6.3 Controller 对 validator agent 再做硬校验

因为 validator agent 也是模型，所以不能只相信它说 `passed: true`。

controller 会运行：

```text
parse_validator_json()
validate_scorecard()
validate_no_review_backlog()
```

硬门槛：

```text
agent validator 总分 >= 96
所有 scorecard 维度 >= 90
mechanism_depth/source_grounding/engineering_usability/evaluation_design >= 92
blocking_notes 必须为空
issues 必须为空或不构成阻塞
required_fixes 必须为空
modification_suggestions 必须为空
non_blocking_findings 必须为空
residual_risks 必须为空
source_audit_notes 必须为空
next_improvements 必须为空
```

这意味着：

```json
{"passed": true}
```

本身不够。只要 scorecard 或 backlog 不干净，controller 会强制失败。

这正好对应文档里的观点：**验证决定 loop 能否无人值守。**

## 7. Feedback 如何进入下一轮

每轮合并结果写入：

```text
round_N_validation.json
```

合并逻辑：

```python
"passed": rule_validation["passed"]
and (agent_validation is None or bool(agent_validation.get("passed")))
```

也就是：

- deterministic validator 失败，整轮失败。
- agent validator 失败，整轮失败。
- 两者都通过，才停止。

如果失败，controller 会把完整 validation JSON 注入下一轮 writer prompt：

```python
prompt = self._build_codex_prompt(round_number, validation)
```

writer prompt 里明确要求：

- 优先修 `agent_validation.issues`。
- 优先修 `required_fixes`。
- 优先处理 scorecard 中最低分维度。
- 清空 `agent_review` 中的 backlog。
- 不要只补几句，要修机制、来源深读、工程设计或可复现实验。

这就形成了真正的反馈闭环：

```text
validator 发现问题
  -> 写入 round_N_validation.json
  -> 注入 round_N_codex_prompt.md
  -> writer 修 report/sources
  -> 下一轮 validator 再检查
```

## 8. 权限边界和受保护文件

writer agent 可以改报告，但不能改题目、rubric 或 validation 历史。

受保护文件逻辑：

```python
def is_protected(path: Path) -> bool:
    return (
        name in {"brief.md", "rubric.md"}
        or name.endswith("_validation.json")
        or name == "final_validation.json"
    )
```

每轮 writer 开始前，controller 会保存受保护文件快照。

writer 结束后，controller 检查这些文件是否被改动。

如果被改动：

```text
恢复原文件
拒绝这一轮
停止
```

这对应文档里的 Harness / Loop 护栏：

- agent 可以执行任务。
- agent 不能篡改目标。
- agent 不能篡改验证结果。

## 9. 预算、超时和失败处理

当前 demo 有两类预算。

最大轮数：

```text
--max-rounds
默认 5
```

单次 Codex 调用超时：

```text
--codex-timeout-seconds
默认 1200
```

统一执行入口：

```python
_run_codex_process(command, prompt)
```

如果 Codex CLI 超时，会返回：

```text
returncode = 124
```

writer agent 有特殊容错：如果 Codex 非零退出或超时，但已经写出候选产物：

```text
research_report.md
sources.json
```

controller 不会直接丢弃结果，而是继续进入下一轮 validation。

代码逻辑：

```python
if completed.returncode != 0:
    if self._has_candidate_outputs():
        continue
```

这是合理的 loop 设计：writer 的进程状态不等于产物质量。产物质量应该交给 validator 判断。

validator agent 不同。如果 validator agent 失败，controller 会认为验证不可信，设置：

```text
controller_error = True
```

然后停止。

## 10. 当前 demo 的停止条件

通过时停止：

```text
deterministic validator passed
and Codex validator agent passed
and score >= 96
and scorecard 全部达标
and blocking_notes 全部为空
and review backlog 全部为空
```

失败时停止：

```text
validator agent 失败，controller 无法信任验证
writer 修改了受保护文件
writer 失败且没有候选产物可验证
最大轮数耗尽
```

这里没有“必须跑 2 轮”这种假严格规则。

是否继续，只看 validation 是否真的满足要求。

## 11. 最近一次运行说明了什么

最近一次从空 workspace 运行时，controller 使用当前默认配置：

```text
min_revision_rounds    0
codex_timeout_seconds  1200
validator_model        gpt-5.5
validator_reasoning    xhigh
```

这意味着：

- 没有“必须跑 2 轮”或“必须跑 3 轮”的固定门槛。
- 是否进入下一轮，只取决于 validation 是否通过。
- writer 和 validator 每次 Codex CLI 调用最多运行 20 分钟。

最近一次真实运行最终在 Round 2 停止。原因不是到了固定轮数，而是 Round 2 的 hybrid validation 已经通过。

```text
Round 1:
  writer 根据 initial goal 生成第一版报告。
  deterministic validator 通过。
  agent validator 打 91 分，没有通过。
  主要问题是：
    - 路线二、路线五、路线七的代表来源深读不够完整。
    - S6、S10、S13 的正文展开缺少任务环境、评估设计和结论边界。
    - 工程章节缺少 TraceEvent / Trajectory schema，不能直接支撑回放、归因和回滚。

Round 2:
  writer 根据 round_1_validation.json 修订。
  更新 sources.json 中 S2、S6、S13 的 summary/relevance。
  在报告中补齐代表来源深读表。
  在工程章节新增 TraceEvent / Trajectory schema。
  把 schema 与 TraceStore、Evaluator、RollbackManager、Candidate、EvalResult、PolicyGate、ValidatorGate 串起来。
  deterministic validator 通过。
  agent validator 打 97 分。
  review backlog 全部为空。
  loop 停止。
```

最终验证摘要：

```text
passed               True
rule_passed          True
agent_passed         True
agent_score          97
source_count         22
source_year_ratio    0.8182
completed_revisions  2
min_revision_rounds  0
issues               0
```

产物命名也很直观：

```text
round_1_codex_prompt.md      Round 1 的 goal prompt
round_1_validation.json      Round 1 writer 产物的验证结果
round_2_codex_prompt.md      Round 2 的修复 goal prompt
round_2_validation.json      Round 2 writer 产物的验证结果
```

每个 `round_N` 都对应：

```text
Trigger -> Goal -> Agent Harness Run -> Verify -> Update State
```

验证通过后，不再生成 `round_N+1`，因为没有新的 Next Trigger。

这体现了 Loop Engineering 的关键点：

- 不是 agent 自称 done。
- 不是人手动挑问题再继续 prompt。
- 是外部系统持续记录、验证、反馈、驱动下一轮。

## 12. 当前实现哪里还不完整

按飞书文档的完整定义看，当前实现是合理的 demo，但不是生产级完整 Loop 系统。

缺口主要有四个。

### 12.1 还不是真正的定时 Automation

当前需要手动运行：

```bash
python3 run_research_loop.py
```

还没有：

- cron
- GitHub Actions
- CI failure trigger
- 飞书/Linear issue trigger
- 定时监控外部变化

### 12.2 还不是真正的 Git Worktree 并行

当前是单 workspace。

没有：

- 多 writer agent 并行生成候选报告。
- 多 worktree 隔离竞争方案。
- merge / compare / select 策略。

### 12.3 Connectors 还很少

当前 connector 主要是：

```text
Codex CLI --search
```

没有接入：

- GitHub
- CI
- Linear
- Slack / 飞书 IM
- Sentry
- 知识库写回
- 数据库或监控系统

### 12.4 没有真实 Human Escalation

当前 demo 在报告里要求“人工升级”，但 controller 本身没有实现：

- 高风险时暂停。
- 发消息给人。
- 等待人工确认。
- 人工审批后继续。

所以它是：

```text
研究型单任务 loop demo
```

不是：

```text
企业级无人值守 production loop
```

## 13. 最终判断

按照飞书文档的定义，当前实现是合理的 Loop Engineering demo。

它完整实现了：

```text
Trigger / Goal
Workspace isolation
External state
Writer agent
Validator agent
Deterministic validator
Feedback loop
Hard gates
Budget
Logs
Stop condition
```

它部分实现了：

```text
Skills
Connectors
Worktrees
Human escalation
```

最准确的描述是：

> 这是一个外部 controller 驱动 Codex writer/validator 的单任务研究型 Loop Engineering demo。它已经体现了 Loop Engineering 的核心：系统替代人来反复 prompt、验证、记录和推进 agent；但它还没有扩展到生产级 automation、并行 worktrees、丰富 connectors 和人工审批流。
