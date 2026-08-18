# M1-P4A 确定性规则核心实现与验收

## 基线

- 架构合并 SHA：`b9bdc598b0c464f1dd199505e6e99de1095b0ab4`（PR #41）
- 规范文档：`docs/04-分析软件/M1 INT-I1-pre架构与规则语义设计.md`（`0.1.2-p4-architecture`，Final Review 冻结）
- 本文件记录 P4A **实现与验收**，不改写冻结架构语义。

## 范围

P4A 实现纯确定性 I1 规则核心：

- 不可变 `DecisionContext` 与结构化事实组
- 冻结 `I1PolicyConfig` / `configuration_digest`
- 规范 reason 投影
- 纯函数 `I1RuleEngine` → `DecisionEvaluation`
- 历史指纹（显式 HistoryFacts，不读 ledger）
- 方案 B 决策身份
- 冻结 `M1Decision` 投影与 schema 校验
- `m1-p4a-acceptance-v1` 子阶段验收

## 非范围

不实现：`decisions.jsonl`、`decision-events.jsonl`、INT manifest、文件锁、fsync、覆盖/outcome 事件、RetryScope 重建、编排、API、决策感知报告、Web、硬件作动。

## 规则与策略

- `rule_version` = `i1-pre-0.1.0`
- `policy_schema_version` = `i1-policy-v1`
- `max_retry_count` = `2`
- `configuration_digest` = SHA-256(canonical `I1PolicyConfig` JSON)
- 摘要不含 `rule_version`

## 实现入口

生产包：`src/digital_pulse/m1_int/`

| 模块 | 职责 |
|---|---|
| `errors.py` | `M1IntError` |
| `models.py` | 事实组、`DecisionContext`、`DecisionEvaluation`、历史指纹 |
| `policy.py` | `I1PolicyConfig` 与 digest |
| `rules.py` | 纯 `I1RuleEngine` |
| `projection.py` | 方案 B `decision_id` 与 `project_m1_decision` |

公开 API 见 `digital_pulse.m1_int`。

验收：

- `src/digital_pulse/m1_p4a_acceptance.py`
- `scripts/generate_m1_p4a_acceptance.py`
- 产物：`artifacts/acceptance/m1-p4a-acceptance.json`

## 安全鉴别器（实现遵循冻结顺序）

1. `emergency_stop` 或 `completion_reason=abort_and_release` → `abort_and_release` / `emergency_stop`
2. `hard_overload` / `host_timeout` / `watchdog_timeout` → `abort_and_release`
3. `sensor_connection_failure` 且无 1/2 → `stop` / `data_integrity_failure`
4. 权威 `device_fault` 或 `buffer_overflow`（FAULT，无断线）→ `abort_and_release` / `device_fault`
5. 未分类 `FAULT` / `SAFE_HOLD` → `unsupported_device_state`，不发射决策

## 重试

写入 `M1Decision.retry_count` 的是评估前计数：0/1 → `retry_same_position`，2 → `reposition` + `retry_limit_reached`。

## `raw_persistence_status` 边界

- `failed` → `stop` / `data_integrity_failure`（可无 APP run）
- `partial` / `not_started`：冻结架构未给出 I1 动作映射 → `invalid_input` 失败关闭（不把 FAILED→stop 扩大到任意状态）

## 测试矩阵

- 六动作、十个 QualityLabel
- retry 0/1/2 与四类耗尽
- 断线假 abort、buffer_overflow、未分类 FAULT/SAFE_HOLD
- 安全优先级对抗
- 早失败无 APP run、正常路径缺 provenance
- SP 冲突、quality_reference 会话不一致、`analysis_allowed` 矛盾
- 确定性、时钟无关、digest、方案 B
- oracle 隔离 / 无持久化 / 无医学语义静态扫描
- `M1Decision.validate()` 与 `validate_schema()`

## 冻结边界

- `M1Decision` / `m1-decision.schema.json` 未改
- P2 canonical golden `8e0ba895050f3d691d8ab3f8ec5ee8147782306c85a8e7af64bb259cad101b3b`
- P3 semantic golden source `2f4f88cc69fbdfb1e129d347025695334542eb9e` / digest `fd76868bb6bd80700ed38d6ef63bf0e0d1e18c6af68e83b1737d41ba7a73997f`
- D3 tag object `da85aee746453e92b0029ae6ec4f51fefc769e4e` / target `d0251b3741d99bab955fa288c57424abd301b0b1`

P4A `acceptance=true` 不等于 M1-P4 完成，也不勾选 Issue #29 M1-P4。
