# M1 INT-I1-pre架构与规则语义设计

版本：`0.1.0-p4-architecture`；状态：**M1-P4 架构冻结候选（文档-only，尚未实现）**。

Baseline：`main@fffbf43070d5dcbb4ba748e9ae57e70509f8bac3`（PR #40 / M1-P3F actual merge SHA）。

上游：M1-P0 / P1 / P2 / P3 = COMPLETE。本文件冻结 I1 决策语义，不实现运行时代码。

## 1. 定位与声明边界

INT-I1-pre 证明的是：

```text
pre-hardware 确定性采集工作流动作
```

它**不**证明：硬件作动、真实探头换位、真实调压、H1 校准、医学有效性、临床决策支持、医疗诊断、M1 完成。

`accept` 只表示当前采集满足工程采集门，可在软件工作流中继续。它不表示正式参数已校准、`heart_rate_bpm` 正式可用、硬件已验证或临床结果正常。pre-H1 下 `objective_parameters` 必须保持 `null`。P4 不得绕过 P3 正式参数门。

INT 不得输出诊断、疾病、证候、治疗、临床正常性或医学风险。人类可读解释必须保持工程解释。

权威 I1 决策路径禁止 LLM。LLM 最多解释已经冻结的结构化决策证据，不得决定动作。

## 2. 冻结 vs 新语义

| 项 | 来源 | 状态 | P4 解释 |
|---|---|---|---|
| `M1Decision` 字段与 `schema_version=1.0.0` | P0 | **FROZEN** | 不改结构，不新增字段 |
| I1 六动作 | P0 `I1_ACTIONS` | **FROZEN** | 运行时仅此六者 |
| `hold` / `adjust_pressure` / `continue_scan` | P0 预留 | **OUT OF I1 SCOPE** | I1 不得发出；不得作为 runtime 动作 |
| 规范 reason_codes 枚举 | `m1-decision.schema.json` | **FROZEN** | 详细 SP/模拟器码必须投影，不得直拷 |
| `QualityLabel` 当前集合 | P2/P3 | **FROZEN INPUT** | 使用 `acceptable` 等现行名，不用旧 `good` |
| P3 Quality Gate / APP 事实 | P3 | **FROZEN INPUT TRUTH** | INT 只消费，不重算 |
| P3 `report.decision_summary.final_action=null` | P3 | **HISTORICAL PRE-P4 TRUTH** | 历史 `app/runs/<run_id>/report.json` 不可改写 |
| P1 `expected_int_action` | 模拟器 metadata | **TEST ORACLE ONLY** | 生产 INT 不得读取 |
| P1C 多 attempt 计划 | 模拟器 | **NO INT EXECUTION** | 计划级 oracle ≠ 实现真理 |
| P0 示例 `max_retry_count=3` / `rule_version=i1-pre-0.0.0` | 合同示例 | **HISTORICAL EXAMPLE** | 非 runtime 策略 |
| 旧文档质量名 `good` / `incomplete_data` / `sensor_saturation` | 旧 P4/P5 表 | **SUPERSEDED** | 映射见第 12 节 |
| 旧决策示例 `INSUFFICIENT_VALID_DURATION` | 《自适应采集决策方案》 | **SUPERSEDED** | 非 `M1Decision.reason_codes` |
| D3 安全状态机 | D3 | **FROZEN OWNER** | INT 不替代物理释放 |

## 3. 契约盘点（只读）

### 3.1 `M1Decision` 字段

`decision_id`，`session_id`，`decided_at_utc`，`milestone`，`int_level`，`device_state`，`quality_reference`，`action`，`reason_codes`，`rule_version`，`input_versions`，`retry_count`，`max_retry_count`，`operator_override`，`outcome`，`parameter_status`，`schema_version`。

`schema_version` 必须为 `1.0.0`。

`input_versions` 必须包含：`signal_processing_version`，`decision_rule_version`，`configuration_digest`。

`decision_rule_version` 必须等于 `rule_version`。禁止 `"unknown"`、`"0"`、全零 digest。

### 3.2 I1 动作

正式 I1：`accept`，`retry_same_position`，`reposition`，`manual_review`，`stop`，`abort_and_release`。

预留且 I1 禁用：`hold`，`adjust_pressure`，`continue_scan`。I1 运行时规则不得发出 `reserved_future_action`。

### 3.3 规范 reason 域

`quality_acceptable`，`weak_signal`，`no_contact`，`saturated`，`unstable_baseline`，`motion_artifact`，`insufficient_duration`，`data_integrity_failure`，`reference_mismatch`，`retry_limit_reached`，`operator_stop`，`operator_override`，`device_fault`，`emergency_stop`，`hard_overload`，`host_timeout`，`watchdog_timeout`，`manual_review_required`。

`reserved_future_action` 不得由 I1 运行时规则发出。

### 3.4 合同许可 ≠ 策略要求

P0 允许 `data_integrity_failure` 作为 abort 安全理由，**不**表示每个完整性失败都必须 abort。

`abort_and_release` 必须带安全/完整性理由，且不得仅由普通质量不足构成。下列码单独存在时**永远不得**直接 abort：

`weak_signal`，`no_contact`，`saturated`，`unstable_baseline`，`motion_artifact`，`insufficient_duration`，`reference_mismatch`，`manual_review_required`，`retry_limit_reached`。

**禁止**机械映射 `QualityLabel.DATA_INTEGRITY_FAILURE → abort_and_release`。

### 3.5 Outcome 域

`null`，`applied`，`superseded`，`rejected_by_safety`，`awaiting_operator`，`completed`。

初始机器建议的 ledger 行：`outcome=null`。后续 outcome 只能通过 append-only 事件变迁，禁止原地改写 JSONL 行。

## 4. 真理所有权

| 层 | 所有者 | INT 用法 |
|---|---|---|
| 安全/设备 | 会话 `device_state`、fault flags、D3 安全状态机、显式 `emergency_stop` / `device_fault` 事实 | 最高优先级；INT 不推断急停于低质量 |
| 完整性 | P2/P3 已提交完整性与 `raw_persistence_status` | 普通采集失败 → 一般 `stop` |
| 质量 | 已提交 `M1QualityResult` / APP Quality Gate | 不重算削顶、逐搏、PPG、分数、稳定窗 |
| APP 门 | P3 `analysis_allowed`、会话完成态 | 不绕过正式参数门 |
| 历史 | INT ledger / RetryScope | 不从场景名推断 retry |
| 操作者 | 显式 operator 输入与 override 事件 | 普通 stop ≠ emergency abort |
| 版本 | 冻结 SP/APP/rule/config SHA | 进入 `input_versions` 与 digest |

INT **只**消费结构化权威工程事实。禁止从原始波形样本直接决策。禁止读取 `scenario.json`、`expected.json`、任何 `expected_int_action` / `expected_quality_label`。

## 5. 架构

```text
Committed P3 Truth
    │
    ▼
DecisionContext Builder   （只读结构化事实；失败则 fail-closed）
    │
    ▼
Frozen I1 Rule Engine     （纯函数；无时钟/随机/文件系统/网络/波形/oracle）
    │
    ▼
DecisionEvaluation        （内部 P4 模型，不改 M1Decision schema）
    │
    ├── M1Decision Projection
    │
    ▼
INT Append-Only Ledger
    │
    ├── Override / Outcome Events
    │
    ├── Replay（只读决策重建）
    │
    └── Decision-Aware Report Projection
            （新 INT 报告；永不改写 P3 app/runs/<run_id>/report.json）
```

P3 不可变输入永不被改写。

## 6. DecisionContext（内部不可变，非新 P0 合同）

`DecisionContext` 只含 INT 所需结构化输入。禁止：原始波形数组、以 scenario ID 选规则、`expected_int_action`、期望质量 oracle、医学解释。

### 6.1 Session facts

最低字段：`session_id`，`source_type`，`completed`，`completion_reason`，`device_state`，`raw_persistence_status`，`parameter_status`。

可用时作为证据/溯源（非医学）：`side`，`site`，`probe_id`。

### 6.2 Safety facts

显式布尔/枚举事实，不得由低质量单独推断：

- `emergency_stop`
- `device_fault`（权威设备故障事实，**不是** `device_state==FAULT` 的别名）
- `hard_overload`
- `host_timeout`
- `watchdog_timeout`
- `operator_stop`（普通操作者终止，与 emergency 分离）

权威来源：会话/事件中的 safety flags、`completion_reason`、设备安全层事实。模拟器当前区分：

| 场景 | 权威区分事实 | I1 动作 |
|---|---|---|
| `abort` | `emergency_stop` 且终端态 `SAFE_HOLD` | `abort_and_release` |
| `device_fault` | 权威 `device_fault=true`（`DeviceFaultKind.DEVICE_FAULT` 或其会话投影），即使 `device_state==FAULT` | `abort_and_release` |
| `sensor_disconnection` | `sensor_connection_failure=true` 且 **无** `emergency_stop`/`device_fault`；即使 `device_state==FAULT` | `stop` |

**禁止** `device_state==FAULT → abort`。那会把普通传感器断线工作流失败误升级为安全 abort。

### 6.3 Integrity facts

使用既有 P2/P3 事实，INT 内不重算 SP 完整性：

`raw_persistence_status`，缺帧计数，时间戳错误计数，CRC 错误计数，传感器连接失败，会话不完整。

普通完整性/采集失败（无独立安全真理）→ `stop`：丢帧、时间戳回退、原始落盘失败、普通语义下的传感器断线。

### 6.4 Quality / APP gate facts

消费：quality label、质量侧 reason/evidence、`quality_reference`、`analysis_allowed`、有效时长等已冻结质量证据。

不重算：削顶、逐搏、PPG 对齐、质量分数、稳定窗。

存在权威 `M1QualityResult` 时，`M1Decision.quality_reference = {session_id, window_id}`。

流水线 `blocked_before_quality` 或无权威质量结果时：`quality_reference=null`。禁止为 abort/device-fault/pre-quality 伪造 `window_id`。

### 6.5 History facts

最低：`prior_decision_count`，`prior_retry_same_position_count`，当前 `retry_count`，`max_retry_count`，`prior_actions`，最近 outcome（若有），`reposition_acknowledged`，当前 retry scope 是否已切换。

禁止从 scenario 名称推断 retry 状态。

### 6.6 Operator / version facts

显式 operator 输入；冻结 `signal_processing_version`、`rule_version`、`configuration_digest`、software SHA。

## 7. 优先级（严格）

1. 安全/紧急（`emergency_stop` / `device_fault` / `hard_overload` / 需释放的 timeout / watchdog）
2. 显式操作者终止（普通 `operator_stop` → `stop`）
3. 原始落盘 + 数据/会话完整性硬门
4. 质量 / 人工复核门
5. 重采历史 / 重采上限
6. 可接受采集（`accept`）
7. 工作流效率

低优先级规则不得覆盖高优先级。无效率规则可单独改变动作；效率只在更高门已通过后减少无效重采，I1-pre 不因效率改写 1–6 的结果。

### 7.1 对抗冲突

| 条件 | 动作 |
|---|---|
| quality acceptable + `emergency_stop` | `abort_and_release` |
| quality acceptable + raw persistence failed | `stop` |
| weak signal + `emergency_stop` | `abort_and_release` |
| weak signal + retry limit reached | `reposition` |
| reference mismatch + retry budget available | `manual_review` |
| `operator_stop` + acceptable quality | `stop` |
| device fault + acceptable quality | `abort_and_release` |
| retry limit + `emergency_stop` | `abort_and_release` |
| manual-review quality + device fault | `abort_and_release` |

## 8. 动作语义

| 动作 | 含义 | 不是 |
|---|---|---|
| `accept` | 满足当前工程采集门，软件工作流可继续 | 正式参数校准、硬件验证、临床正常 |
| `retry_same_position` | 同一 RetryScope 内建议再采一次 | 无限重试；自动换位 |
| `reposition` | 面向操作者的换位建议 / 工作流状态；结束当前同位 retry scope | 自动移动探头 |
| `manual_review` | 自动化暂停，等待操作者评估 | 医学复核；自动 accept/retry/诊断 |
| `stop` | 终止当前自动采集/重采 episode | 硬件紧急释放 |
| `abort_and_release` | 因权威安全真理请求立即安全终止/释放 | 普通质量不足 |

物理释放仍由设备安全层管辖。`reposition` 不自动控制执行器。`adjust_pressure` 属于后续 INT 级别。

削顶（`saturated`）I1 首版 → `stop`。若同时存在权威安全过载 → 更高优先级可产生 `abort_and_release`。

`no_contact` → `reposition`（不消耗同位重采预算，除非未来有独立冻结理由）。

`reference_mismatch` → `manual_review`。不自动 accept，不为“让结果通过”而自动 retry。

`manual_review_required` → `manual_review`。工作流进入 awaiting operator；继续必须显式且可审计。

## 9. 规范 reason 投影

模拟器/SP 详细码（如 `LOW_PULSE_AMPLITUDE`，`FRAME_SEQUENCE_GAP`，`TIMESTAMP_REGRESSION`，`PPG_ALIGNMENT_MISMATCH`，`RAW_PERSISTENCE_FAILURE`）**不得**直接写入 `M1Decision.reason_codes`。

确定性投影（详细证据另存于 DecisionEvaluation / events）：

| 详细/输入事实 | 规范 `reason_codes`（有序列表，去重） |
|---|---|
| Quality `acceptable` 且更高门通过 | `quality_acceptable` |
| Quality `weak_signal` / `LOW_PULSE_AMPLITUDE` | `weak_signal` |
| Quality `no_contact` | `no_contact` |
| Quality `saturated` | `saturated` |
| Quality `unstable_baseline` | `unstable_baseline` |
| Quality `motion_artifact` | `motion_artifact` |
| Quality `insufficient_duration` | `insufficient_duration` |
| Quality `reference_mismatch` / PPG mismatch | `reference_mismatch` |
| Quality `manual_review_required` / unstable_load 类 | `manual_review_required` |
| 原始落盘失败、丢帧、时间戳回退、普通传感器断线 | `data_integrity_failure` |
| 同位重采用尽 | 原质量码 + `retry_limit_reached` |
| 普通操作者停止 | `operator_stop` |
| 操作者覆盖 | 原建议码保留在历史；覆盖事件可含 `operator_override` |
| 权威 `device_fault` | `device_fault` |
| `emergency_stop` | `emergency_stop` |
| 硬过载 / host / watchdog | 对应 `hard_overload` / `host_timeout` / `watchdog_timeout` |

排序：按本节表的稳定优先级顺序输出；禁止集合无序；禁止重复码。

## 10. RetryScope 与 retry_count

内部概念 `RetryScope`（或 `DecisionEpisode`）。不修改 `M1Decision` schema。

同一 scope：同一操作者确认的同位采集序列。`reposition` 被操作者确认后开启新 scope。`stop` / `abort_and_release` 终止 scope。`accept` 结束当前采集 episode。`manual_review` 暂停但不自动重置计数。

**冻结定义：**

```text
retry_count =
  当前同一 RetryScope 内、评估本次 attempt 之前
  已经发出的 retry_same_position 次数
```

它**不是** `attempt_index`，除非实现证明二者在该 scope 内数值等价。

示例（`max_retry_count=2`）：

| 评估时刻 | retry_count | 含义 |
|---|---|---|
| 首次失败 attempt | 0 | 尚未发出 retry |
| 已发出 1 次 retry 后再评估 | 1 | |
| 已发出 2 次 retry 后再评估 | 2 | 达到上限 |

**禁止**仅因另一个 `session_id` 存在就重置 `retry_count`。必须有可证明的 scope 链接或 reposition acknowledgement。

重放必须从 ledger 事件确定性重建 scope，禁止隐藏可变全局计数器。

## 11. max_retry_count 与无限循环硬门

**冻结：`max_retry_count = 2`。**

解释：初始 attempt + 最多 2 次同位重采 = 上限前 3 次 attempt。与 `retry_still_fails` 三个弱信号 attempt 后计划级 `reposition` 对齐。

P0 示例 `max_retry_count=3` **不是** runtime 策略。

比较语义：

```text
若存在可重采条件 AND retry_count < max_retry_count
  → retry_same_position

若 retry_count >= max_retry_count
  → 禁止 retry_same_position
```

这是无限循环硬门。

另外：`reposition` / `manual_review` / `stop` / `abort_and_release` / `accept` 均为终端或需新操作者输入的非自循环动作。禁止自动循环 `retry→retry→…` 或无新输入的 `reposition→retry→reposition→…`。

## 12. 可重采耗尽矩阵

对每个可重采质量类别，动作必须唯一。

| 质量 | 低于上限 | 达到上限 | 规范 reasons（达上限时） | 需操作者确认？ | 之后 scope |
|---|---|---|---|---|---|
| `weak_signal` | `retry_same_position` | `reposition` | `weak_signal`, `retry_limit_reached` | 换位需操作者 | 新 scope 仅在 acknowledgement 后 |
| `unstable_baseline` | `retry_same_position` | `reposition` | `unstable_baseline`, `retry_limit_reached` | 同上 | 同上 |
| `motion_artifact` | `retry_same_position` | `reposition` | `motion_artifact`, `retry_limit_reached` | 同上 | 同上 |
| `insufficient_duration` | `retry_same_position` | `reposition` | `insufficient_duration`, `retry_limit_reached` | 同上 | 同上 |

`no_contact` 不进入同位重采预算：直接 `reposition` + `no_contact`。

`saturated` 不进入同位重采：`stop` + `saturated`（除非更高优先级安全 abort）。

`reference_mismatch` / `manual_review_required` 不因仍有 retry 预算而改 retry。

## 13. 单场景兼容矩阵（由 P3 事实 + 本策略论证，非 oracle 授权）

下列映射必须由权威 P3 质量/完整性/安全事实独立推出。生产路径删除或篡改 `expected.json` 不得改变结果。

| P3 场景 | 权威输入事实 | I1 动作 | 主要 reasons |
|---|---|---|---|
| `normal_high_quality` | quality `acceptable`，完整性 OK，无安全事件 | `accept` | `quality_acceptable` |
| `weak_signal` | quality `weak_signal`，`retry_count < 2` | `retry_same_position` | `weak_signal` |
| `no_contact` | quality `no_contact` | `reposition` | `no_contact` |
| `upper_saturation` / `lower_saturation` | quality `saturated` | `stop` | `saturated` |
| `baseline_drift` | quality `unstable_baseline`，未达上限 | `retry_same_position` | `unstable_baseline` |
| `motion_artifact` | quality `motion_artifact`，未达上限 | `retry_same_position` | `motion_artifact` |
| `unstable_load` | quality `manual_review_required` | `manual_review` | `manual_review_required` |
| `ppg_misalignment` | quality `reference_mismatch` | `manual_review` | `reference_mismatch` |
| `insufficient_duration` | quality `insufficient_duration`，未达上限 | `retry_same_position` | `insufficient_duration` |
| `frame_loss` | 完整性失败，无安全 abort 事实 | `stop` | `data_integrity_failure` |
| `timestamp_regression` | 完整性失败，无安全 abort 事实 | `stop` | `data_integrity_failure` |
| `sensor_disconnection` | 传感器连接失败，无 `device_fault`/`emergency_stop` | `stop` | `data_integrity_failure` |
| `abort` | `emergency_stop` | `abort_and_release` | `emergency_stop` |
| `device_fault` | 权威 `device_fault` | `abort_and_release` | `device_fault` |
| `raw_persistence_failure` | `raw_persistence_status=failed`，会话不完整 | `stop` | `data_integrity_failure` |

旧文档 `good`→现行 `acceptable`；`incomplete_data` 按事实拆成 `insufficient_duration` 或完整性 `stop`；`sensor_saturation`→`saturated`。旧 P5 表“达到重采上限 → stop 或 manual_review”对弱信号 **SUPERSEDED**：I1-pre 弱信号耗尽 → `reposition`。

## 14. 多 attempt 序列

### 14.1 `retry_improves`

不读取计划级 oracle。

1. attempt 1 quality `weak_signal`，`retry_count=0` → `retry_same_position` / `weak_signal`
2. attempt 2 quality `acceptable`，`retry_count=1` → `accept` / `quality_acceptable`

### 14.2 `retry_still_fails`

P1C 每个 AttemptSpec 的 `expected_int_action=retry_same_position` **不是**实现真理。第三次必须由 retry 历史推出 `reposition`。

1. weak，`retry_count=0` → `retry_same_position`
2. weak，`retry_count=1` → `retry_same_position`
3. weak，`retry_count=2`，`max_retry_count=2` → `reposition` + `weak_signal` + `retry_limit_reached`

无无限重试。无自动探头运动。

## 15. 确定性规则引擎

```text
DecisionContext + Frozen I1PolicyConfig → DecisionEvaluation
```

规则求值内部：无文件系统、无随机数、无当前时钟、无网络、无原始波形、无模拟器 oracle。

`decided_at_utc` 由编排/持久化层显式注入，不进入语义身份。

### 15.1 内部 `DecisionEvaluation`

因 `M1Decision` 不含全部审计字段，内部模型可含：

`recommended_action`，`canonical_reason_codes`，`human_readable_explanation`，`evidence_refs`，`matched_rule_id`，`rule_priority`，`semantic_input_digest`，`rule_version`，`configuration_digest`。

解释文本放在内部证据记录 / `decision-events.jsonl` / 派生 API，**不**给 `M1Decision` 增加未批准字段。解释保持工程语言。

`matched_rule_id` 与 `evidence_refs` 使用稳定排序。

### 15.2 `I1PolicyConfig`

不可变策略，仅含规则策略值：`max_retry_count=2`，耗尽策略表版本，`rule_version`，优先级表版本。

**禁止**放入 P2 信号阈值。P2 阈值仍由 SP 拥有。

策略 schema 版本：`i1-policy-v1`。

### 15.3 规则版本

首个 I1-pre runtime 规则版本：**`i1-pre-0.1.0`**。

不继续使用 P0 示例 `i1-pre-0.0.0`。

必须 bump 的语义变化：优先级、耗尽动作、`max_retry_count`、reason 投影、安全/完整性分流、RetryScope 重置规则。

纯文档/注释/性能无关重构不 bump。

### 15.4 configuration_digest

对规范 `I1PolicyConfig` 做 SHA-256：UTF-8，稳定键序，禁止 NaN/Infinity，确定性序列化（建议 `sort_keys=True`，`separators=(",", ":")`，`ensure_ascii=False`，`allow_nan=False`）。

同一策略 → 同一 digest；语义变化 → 不同 digest。

### 15.5 decision_id

```text
decision_id = "m1-decision-" + SHA256(canonical semantic decision input)
```

语义输入必须包括：`session_id`，quality reference 或等价质量真理，device/safety facts，integrity facts，`retry_count`，`max_retry_count`，`rule_version`，`configuration_digest`，相关 prior-decision fingerprint。

**禁止**纳入：`decided_at_utc`，文件系统路径，临时目录，随机 UUID。

同一语义输入 → 同一 `decision_id`。

幂等：同一语义输入 + 规则版本 + 配置 + retry 历史 → 同一决策。重复持久化必须幂等，或显式确定性冲突。禁止因随机 ID/时间戳产生语义不同的重复决策。

## 16. 并行 INT 存储（冻结布局）

P3 已提交 run 不可变。P4 **不得**打开并改写 `app/runs/<run_id>/report.json`，也不得向已提交 P3 run 追加“当时就存在”的文件。

冻结布局：

```text
sessions/<session_id>/
├── manifest.json                 # P1/P3 采集事实（只读对 INT）
├── samples.jsonl
├── events.jsonl
├── app/
│   └── runs/<run_id>/...         # P3 不可变
└── int/
    ├── manifest.json
    ├── decisions.jsonl           # 机器建议 M1Decision，append-only
    ├── decision-events.jsonl     # 覆盖、outcome、应用、解释引用
    └── reports/
        └── <report_id>.json      # 决策感知派生报告
```

权威：

- P3 不可变 session/APP/SP 资产 = **输入真理**
- INT decision ledger = **决策真理**
- 派生报告/API = **投影**

报告或 UI 不是真理源。

### 16.1 `int/manifest.json`

至少：schema/version，`decision_rule_version`，`configuration_digest`，software SHA，ledger checksum/provenance，当前有效决策指针（派生），报告引用，append/commit 语义。

不得复制或覆盖 APP manifest 真理。

### 16.2 `decisions.jsonl`

- 逻辑 append-only
- 每行一个完整 canonical JSON `M1Decision`，schema 校验
- 稳定写入顺序
- 禁止静默改写/删除
- 不完整最后一行不得当作完整真理（fail-closed）
- 写者文件锁；提交后 fsync
- 原子性：完整行 + fsync；崩溃后扫描截断尾
- 重复 `decision_id`：字节等价则幂等成功；否则确定性 conflict，不得混写
- 损坏：拒绝将该 ledger 作为决策真理；不得猜测动作

### 16.3 并发

同一 session 的 INT 写者必须互斥。重叠写：一者提交，另一者冲突/幂等，语义对齐 P3A：无 mixed assets、无部分可见决策、无丢失 manifest。

## 17. 操作者覆盖（append-only）

**禁止**“更新原 JSON 行”。

- 原机器 `M1Decision` 不可变；`operator_override=null`
- 覆盖写入 `decision-events.jsonl`：`operator_id`，`note`，选择动作，时间戳，指向原 `decision_id`
- 有效动作 = 派生视图：原建议 + 覆盖事件 + outcome 事件
- 原建议永远可重放
- 不修改冻结 P0 schema；P0 的 `operator_override` 字段用于派生投影或后续链接记录，不用于原地回填

`manual_review` 通常使工作流 `awaiting_operator`。操作者继续必须显式可审计。覆盖不得擦除机器建议。

## 18. Outcome 所有权

编排/事件层拥有 outcome 变迁；规则引擎初次求值只产生建议，ledger 行 `outcome=null`。

| 值 | 何时 |
|---|---|
| `null` | 已记录建议，尚未应用/覆盖 |
| `awaiting_operator` | `manual_review` / `reposition` 等待确认（事件记录，非改写原行） |
| `applied` | 工作流已按有效动作执行（仍无物理作动） |
| `superseded` | 被后续覆盖或新决策取代 |
| `rejected_by_safety` | 应用前被更高优先级安全事实拒绝 |
| `completed` | episode 正常结束（如 accept 已纳入工作流） |

禁止原地 mutation。

## 19. 决策感知报告

历史 P3 报告：`final_action=null`，`decision_ids=[]`，`decision_rule_version=null`。保持不可变。

P4 使用既有冻结 `M1Report`，不改 schema。新投影放入 `int/reports/`：

- `decision_summary.final_action`
- `decision_summary.decision_ids`
- `decision_summary.reason_codes`
- `version_manifest.decision_rule_version`

同时保留 P3 质量/完整性真理、pre-H1 正式参数边界、`not_for_medical_use`。

即使 `decision.action=accept`，pre-H1 `objective_parameters` 仍为 `null`。INT accept 不是校准权威。

报告身份、决策链接、software/rule 溯源、checksum、不可变、可重放在 P4D 实现；本规划冻结“并行派生、禁止改写 P3”。

## 20. 重放

同一冻结 P3 输入 + 同一决策历史 + 同一策略配置 + 同一规则版本 → 同一语义决策。

不要求：模拟器 oracle、当前墙钟、可变 latest 状态、目录遍历顺序。

**决策重放 ≠ 动作执行。** 重放可重建建议/有效动作，但不得物理移动、重试硬件、释放执行器、改变历史会话状态。除非显式新的持久化操作，重放只读。

## 21. Fail-closed 与未知态

下列情况不得猜测决策，尤其不得默认 `accept`：

缺失 session；损坏/缺失 APP run；缺失 SP 溯源；损坏 quality reference；非法 `retry_count`；`retry_count > max_retry_count`；未知 `device_state`；非法 policy digest；rule-version 不匹配；ledger 损坏；不完整 JSONL 末行。

若真理不足且无显式安全 abort：

- 会话存在但质量/完整性不足以继续自动化 → **`manual_review`**
- 会话明显不完整且属普通采集失败 → **`stop`**

永不因缺省而 `accept`。

## 22. 禁止范围

- 不实现新滤波、新质量阈值、新逐搏、新 PPG 对齐、新削顶计算、新信号置信模型
- 不改 P2 canonical golden `8e0ba895050f3d691d8ab3f8ec5ee8147782306c85a8e7af64bb259cad101b3b`
- 不改/再生 P3 semantic golden（source `2f4f88cc...`，digest `fd76868bb6bd80700ed38d6ef63bf0e0d1e18c6af68e83b1737d41ba7a73997f`）
- 不改 D3 tag `d3-v1.0.0`
- 本规划 PR 不改 `m1_contracts.py` 与全部 `m1-*.schema.json`

## 23. 规则表（实现必须可逐行测试）

| 优先级 | 权威条件 | 需要质量？ | retry 条件 | 动作 | reasons | 终端？ | 需操作者？ | retry 计数 | 证据源 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `emergency_stop` | 否 | 忽略 | `abort_and_release` | `emergency_stop` | 是 | 否（安全） | 终止 scope | safety flags |
| 1 | 权威 `device_fault` | 否 | 忽略 | `abort_and_release` | `device_fault` | 是 | 否 | 终止 | session/device facts |
| 1 | `hard_overload` / 需释放 timeout / watchdog | 否 | 忽略 | `abort_and_release` | 对应码 | 是 | 否 | 终止 | safety facts |
| 2 | 普通 `operator_stop` | 否 | 忽略 | `stop` | `operator_stop` | 是 | 已输入 | 终止 | operator facts |
| 3 | raw persistence failed | 否 | 忽略 | `stop` | `data_integrity_failure` | 是 | 否 | 终止 | P3 persistence |
| 3 | 丢帧/时间戳/普通断线等完整性硬失败 | 否 | 忽略 | `stop` | `data_integrity_failure` | 是 | 否 | 终止 | P2/P3 integrity |
| 4 | `manual_review_required` | 是 | 不消耗 retry | `manual_review` | `manual_review_required` | 暂停 | 是 | 不变 | P3 quality |
| 4 | `reference_mismatch` | 是 | 不消耗 retry | `manual_review` | `reference_mismatch` | 暂停 | 是 | 不变 | P3 quality |
| 4 | `no_contact` | 是 | 不消耗同位预算 | `reposition` | `no_contact` | 需确认后新 scope | 是 | 不自增 | P3 quality |
| 4 | `saturated` 无安全过载 | 是 | 不重采 | `stop` | `saturated` | 是 | 否 | 终止 | P3 quality |
| 5 | 可重采质量且 `retry_count < 2` | 是 | 是 | `retry_same_position` | 质量码 | 否 | 否 | 本次发出后 +1 | quality+history |
| 5 | 可重采质量且 `retry_count >= 2` | 是 | 禁止 retry | `reposition` | 质量码 + `retry_limit_reached` | 需确认 | 是 | 不发 retry | quality+history |
| 6 | `acceptable` 且更高门通过 | 是 | n/a | `accept` | `quality_acceptable` | episode 完成 | 否 | 终止 episode | P3 gate |
| 7 | 仅效率 | — | 不得覆盖 1–6 | 保持 1–6 结果 | — | — | — | — | 无独立动作 |

## 24. 与其他文档

- 《M1-P4 INT-I1-pre规则决策引擎实施方案》：分阶段落地与声明边界
- 《M1-P4测试与验收计划》：测试与未来 acceptance artifact
- 《自适应采集决策方案》：I0–I5 长期路线仍有效；其中 I1 细则与旧示例以本文为准
- 《M1-P真实接入前软件就绪实施方案》：P4 必须实现项仍有效；P5 旧质量名表为历史基线
