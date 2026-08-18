# M1-P4 INT-I1-pre规则决策引擎实施方案

版本：`0.1.2-p4-plan`；状态：**Final Review 文档修复后的规划冻结（文档-only）**。

Baseline：`main@fffbf43070d5dcbb4ba748e9ae57e70509f8bac3`。

Issue #29 本阶段保持 OPEN，且 **不勾选** M1-P4。

## 1. 阶段定位

M1-P3 APP-A1-pre 已 COMPLETE。下一工程能力是 INT-I1-pre 规则决策引擎。

**本阶段不实现决策引擎。** 本阶段只冻结语义、架构、存储边界与可合并子阶段，并提交 Draft PR。

```text
M1-P0 契约  →  P1 模拟  →  P2 SP  →  P3 APP
                                      ↓
                         M1-P4 INT-I1-pre（本文件：只规划）
                                      ↓
                         M1-P5 / M1-PV（未开始）
```

## 2. Goals / Non-goals

### Goals

冻结并文档化：决策真理所有权、`DecisionContext`、I1 动作语义、优先级、规范 reason 投影、retry_count / `max_retry_count=2`、无限循环硬门、操作者 stop/override、outcome、确定性 `decision_id`、规则/配置版本、`decisions.jsonl`、append-only 历史、并发/幂等、P3 不可变 run 边界、决策感知报告投影、重放、oracle 隔离、P4A–P4E 计划、测试与验收计划。

### Non-goals（本 PR 与本阶段）

- 不创建 `src/digital_pulse/m1_int/`
- 不写规则引擎、ledger writer、新 API、新报告实现
- 不 Ready、不 Merge、不开始 P4A 代码
- 不改 P0 合同 / Schema / P2 / P3 golden / D3 tag
- 不勾选 Issue #29 的 M1-P4
- 不开始 M1-P5
- 不声称硬件作动、H1、临床或 M1 完成

## 3. 物理与安全边界

I1 **不**：自动移动探头、自动调压、直接控制执行器、替代 D3/设备安全状态机。

- `reposition` = 操作者面向建议 / 工作流状态
- `stop` = 终止当前采集工作流/episode，不一定物理释放
- `abort_and_release` = 显式安全释放请求路径；实际物理释放仍由设备安全层管辖

普通质量不足不得 abort。详见《M1 INT-I1-pre架构与规则语义设计》。

## 4. 声明边界

M1-P4 将证明：pre-hardware 确定性采集工作流动作。

它将 **不** 证明：硬件作动、真实探头换位、真实压力推荐、H1 校准、医学有效性、临床决策支持、医疗诊断、M1 完成。

`accept` 不解除 pre-H1 正式参数阻断。

## 5. 子阶段（每个都必须独立走完 Draft → Final Review → Ready → Merge Gate → exact-main CI → Closeout）

本规划 PR **不实现** 任何子阶段代码。

### M1-P4A — Deterministic Rule Core / DecisionContext

只实现：`DecisionContext`，`I1PolicyConfig`，规范 reason 投影，纯确定性 `I1RuleEngine`，`DecisionEvaluation`，`M1Decision` 投影，`decision_id` / semantic fingerprint，规则/配置版本。

无 persistence，无 `decisions.jsonl`，无编排，无报告集成。

### M1-P4B — Decision Ledger / Provenance / Override / Replay

并行 INT 存储层，`decisions.jsonl`，append-only 溯源，checksums，locking，原子性，幂等，损坏处理，操作者覆盖历史，outcome 事件，只读重放。

尚无 retry 编排。

### M1-P4C — Retry Episode / Orchestration / Infinite-Loop Protection

RetryScope / episode，`retry_count` 重建，max retry 强制，reposition 切换，manual-review 暂停，stop/abort 终端，无限循环防护。

无物理运动，无调压。

### M1-P4D — Decision-Aware Report / Query API Integration

决策感知 `M1Report` 投影，decision summary，`decision_rule_version`，读/查询 API，有效决策投影，历史审计。

必须保持不可变 P3 APP run。不要求新增 Web 功能，除非另行授权。

### M1-P4E — Full Scenario Matrix / Aggregate Acceptance / Final P4 Closeout

16 单场景 + 2 多 attempt 计划，完整决策序列，oracle 隔离，安全优先级，retry 上限，确定性，重放，覆盖保留，ledger 损坏，报告链接，P0–P3 回归，聚合 P4 acceptance，独立 Final Review。

**只有 P4E closeout 之后才可勾选 Issue #29 的 M1-P4。**

## 6. 未来验收产物（本阶段不生成）

路径：`artifacts/acceptance/m1-p4-acceptance.json`

版本：`m1-p4-acceptance-v1`

必须最终包含：`software_commit_sha`，`rule_version`，policy/config digest，scenario count，decision sequence count，oracle isolation，safety precedence，retry-limit proof，determinism，ledger integrity，override preservation，report linkage，`failed_gates`，`acceptance`。

决策语义 golden（若采用）名称：`m1-p4-decision-semantic:v1`。它不是 P3 semantic golden、不是 SP fingerprint、不是生产 `decision_id` 方案。必须从已审查冻结的 P4 规则基线生成，禁止任意 HEAD 自我批准。本规划 PR 不提交 golden 文件。

## 7. CI 与回归

本规划为文档-only，不修改 CI，除非文档 lint 有正当需要（本 PR 无此需要）。

现有回归必须对 exact Draft HEAD 保持绿：pytest，unittest，D3，P1，P2，P3B，P3C，P3E，P3 aggregate，Web，P3F whole-workflow conclusion。

尚无 P4 正式 acceptance job。

## 8. 合同与生产冻结

相对 baseline，本 PR 期望：

```text
src/ = ZERO
protocols/ = ZERO
tests/ = ZERO
web/ = ZERO
.github/workflows/ = ZERO
```

仅文档变更。

P2 canonical golden 与 P3 semantic golden、D3 tag 保持不变。

## 9. 文档交付

1. 本文件：实施方案与子阶段
2. `docs/04-分析软件/M1 INT-I1-pre架构与规则语义设计.md`：冻结语义
3. `docs/05-验证与实验/M1-P4测试与验收计划.md`：测试与验收
4. 对旧文档的交叉标注（不改写历史证据正文为“从未存在”）

## 10. 下一闸门

本 Draft PR 完成后必须停止。

Final Review 规范性补全见架构文档 §25（含 §25.19–§25.23 的未分类安全态、manual_review 恢复、投影字段、SP 版本来源）。

在架构计划自身完成 Final Review → Ready → Merge Gate → exact-main closeout 之前，**不得实现 P4A**。

下一闸门需单独授权：

```text
PR #41 — M1-P4 Architecture Plan
Metadata Sync + Ready Transition
```
