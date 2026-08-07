# M1-P2 SP-S1-pre信号处理预实现实施方案

版本：0.1.3；状态：M1-P2A/P2B/P2C已实现（`m1_sp`）；P2D未开始。

## 1. 阶段定位

M1-P2位于M1-P1多通道模拟器之后、M1-P3分析软件之前。

目标是建立一套与数据源无关、确定性、可解释、可重放的SP-S1-pre处理链，使同一套处理代码既可以消费M1-P1模拟器/Replay输出，也可以在H1真实硬件到货后消费HardwareDataSource输出。

本阶段只证明“信号处理结构和模拟场景行为成立”，不证明真实传感器阈值、真实滤波参数、人体有效率或医学含义。

## 2. 冻结输入与边界

### 2.1 必须复用

- `M1Sample` / `M1Session` / `M1QualityResult`及其Schema；
- `QualityLabel`冻结枚举；
- `M1-P1` 16个单attempt场景与2个多attempt计划；
- `ReplayDataSource`；
- `d3-v1.0.0`冻结基线；
- M1-P1正式会话目录与完整性摘要。

### 2.2 禁止修改

M1-P2不得为了算法方便修改：

- `m1-sample.schema.json`；
- `m1-session.schema.json`；
- `m1-quality.schema.json`；
- `m1_contracts.py`既有字段语义；
- M1-P1模拟场景定义和golden结果。

如实现确实无法被M1-P0契约表达，应停止并单独提出契约变更评审，不得静默扩展。

## 3. 正式质量枚举对齐

旧《信号处理方案》和早期路线中出现过`good`、`sensor_saturation`、`pressure_unstable`等概念名；M1-P0已经冻结正式输出枚举，因此SP-S1-pre必须以`QualityLabel`为准：

| 概念语义 | 正式M1QualityResult.label |
|---|---|
| good / high quality | `acceptable` |
| weak signal | `weak_signal` |
| no contact | `no_contact` |
| upper/lower saturation | `saturated` |
| baseline drift | `unstable_baseline` |
| motion artifact | `motion_artifact` |
| too short / insufficient beats | `insufficient_duration` |
| frame/timestamp/sensor/persistence integrity | `data_integrity_failure` |
| PPG mismatch | `reference_mismatch` |
| unstable contact load / ambiguous quality | `manual_review_required` |

更细粒度语义进入内部`ProcessingEvidence`；只有M1-P0允许的reason code和metric才能投影到`M1QualityResult`。

不得新增新的正式QualityLabel。

## 4. 关键设计决策

### 4.1 生产算法不得读取模拟器oracle

`SP-S1-pre`只能读取M1Sample/M1Session及算法参数，禁止读取：

- `scenario.json`；
- `expected.json`；
- `ScenarioDefinition.expected_*`；
- 模拟器FaultWindow/FaultPlan。

这些只允许测试和验收层使用。

### 4.2 真实阈值与模拟阈值分离

提供独立参数集：

- `structural_default`：协议/结构事实，不依赖人体和硬件校准；
- `simulation_only`：仅用于M1-P1黄金场景回归；
- `pending_h1_calibration`：真实硬件必须校准后才能赋值；
- `frozen_h1`：M1-P阶段禁止生成。

M1-P2默认模拟验收profile为`simulation_only`。

对真实硬件不存在默认“可信阈值”；H1前不得把simulation-only阈值标成candidate/frozen。

### 4.3 不生成伪置信度

M1-P2首版：

- `score = null`；
- `confidence = null`；
- 分类由确定性规则和证据产生；
- 所有原因代码可追踪到观测指标。

未来真实校准后再评估是否需要score/confidence。

### 4.4 安全终止先于质量生成

以下情况属于会话/设备安全事实，不应为了匹配模拟器oracle强行制造质量标签：

- `abort` / `SAFE_HOLD` / `emergency_stop`；
- 无法由冻结质量reason code正确表达的generic `device_fault`。

此时SP返回：

```text
processing_status = blocked_before_quality
quality_results = []
blocking_codes = emergency_stop | device_fault
```

由后续APP/INT直接使用M1Session和设备状态进行阻断。

以下完整性失败仍生成正式`data_integrity_failure`：

- sequence gap；
- timestamp error；
- sensor disconnected；
- persistence failed；
- CRC error。

## 5. 总体流水线

```text
M1Session + Iterable[M1Sample]
→ InputNormalizer
→ IntegrityAnalyzer
→ SeriesBuilder
→ StableWindowSelector
→ RawQualityMetrics
→ FilterBank
→ BeatDetector / BeatSegmenter
→ PPGReferenceAligner
→ QualityEvaluator
→ M1QualityProjector
→ SPProcessingResult
```

每一层必须：

- 确定性；
- 不修改输入样本；
- 保存输入时间坐标；
- 输出强类型结构；
- 不依赖模拟器内部对象。

## 6. 代码结构

P2A落地包名为`src/digital_pulse/m1_sp/`（设计阶段曾建议`m1_signal_processing`；实现以实际包为准）。

```text
src/digital_pulse/m1_sp/
├── __init__.py
├── errors.py
├── models.py
├── parameters.py
├── normalization.py
├── integrity.py
├── windows.py
└── processor.py
```

后续P2B/P2C可在同包扩展 metrics / filters / beats / quality，或按需拆分。

P2A职责：

| 模块 | 职责 |
|---|---|
| parameters | 参数分层、版本、digest（P2A） |
| models | 内部强类型结果，不扩展M1正式Schema（P2A） |
| normalization | M1Sample→NormalizedSession，保留mask和时间（P2A） |
| integrity | CRC/sequence/timestamp/sensor/session完整性（P2A） |
| windows | 结构性稳定窗口（P2A） |
| processor | `SPPreprocessor`（P2A）+ `SPQualityProcessor`（P2B/P2C；P2D再扩展正式`SPProcessor`） |
| metrics / quality / projection | P2B raw指标 + P2C beat/reference 扩展、`M1QualityResult`投影 |
| filters / beats / reference | P2C 因果/离线滤波、逐搏、PPG 一对一匹配 |
| observations | P2A Final Review：观测序列/时间戳异常 |

## 7. 内部数据模型

建议至少包含：

```text
SPInputBundle
ChannelSeries
IntegrityEvidence
WindowSlice
QualityMetricsInternal
BeatCandidate
BeatSegment
ReferenceMatchSummary
ProcessingEvidence
SPProcessingResult
```

`QualityMetricsInternal`允许保存比M1正式metrics更多的研究指标，例如：

- load median/std/slope；
- high-frequency residual ratio；
- peak prominence统计；
- PPG median lag；
- candidate beat interval dispersion。

但投影到`M1QualityResult.metrics`时只能写冻结字段：

```text
valid_fraction
clipping_fraction
baseline_drift_raw
pulse_std_raw
beat_count
ppg_match_rate
```

## 8. 完整性层

结构性硬门：

- `session_id`一致；
- frame sequence gap；
- `receive_integrity.crc_valid == false`；
- `receive_integrity.sequence_valid == false`；
- `receive_integrity.timestamp_valid == false`；
- device time regression；
- channel disconnected/open/short/read_failed；
- persistence status；
- session completion state。

完整性事实来自实际sample/session，不从scenario配置推断。

## 9. 工程单位接口

M1-P2只建立版本化接口，不伪造真实标定。

现有D2 `CalibrationRecord`属于合成/台架标定基础，可复用其模型思想，但M1-P2必须显式区分：

```text
raw ADC view
synthetic engineering view
future H1 calibrated engineering view
```

默认算法质量判断优先使用raw-domain指标，避免把D2 synthetic AU误写成真实N/kPa。

若无H1真实标定，正式输出不得声称真实力/压力单位。

## 10. 稳定窗口

StableWindowSelector同时考虑：

- sample连续性；
- device state；
- channel status；
- terminal fault；
- load变化；
- motion evidence；
- 最短有效持续时间。

首版输出一个或多个`WindowSlice`，包含：

```text
window_id
start/end sample index
start/end device_time_us
sample_count
valid_duration_s
excluded_reason_codes
```

不得通过删除异常样本后把不连续时间拼接成“连续有效窗口”。

## 11. 滤波设计

### 11.1 质量检测使用raw优先

以下检测不得依赖“修复后”波形掩盖问题：

- clipping；
- no contact；
- weak amplitude；
- baseline drift；
- motion artifact；
- integrity。

### 11.2 实时与离线严格分离

`CausalFilter`：

- 只使用当前及过去样本；
- 保存固定group delay；
- 用于未来在线显示/判断。

`OfflineReviewFilter`：

- 可使用对称窗口/前后文；
- 用于离线形态复核和beat分析；
- 结果明确标记`offline_review`；
- 禁止用其性能宣称实时延迟。

首版优先NumPy-only确定性FIR，避免为P2引入重大DSP依赖；如实现需要SciPy，必须单独评审依赖和边界。

## 12. 逐搏检测

首版只做客观候选检测，不做中医脉象分类。

流程：

```text
filtered pulse
→ robust baseline/noise estimate
→ local peak candidates
→ refractory constraint
→ peak prominence check
→ preceding foot candidate
→ BeatSegment
```

输出至少保存：

- peak/foot device time；
- peak raw/filtered value；
- beat interval；
- segment index范围；
- valid/invalid reason。

真实心率阈值、异常节律解释均不在M1-P2冻结。

## 13. PPG对齐

PPG只作为参考一致性证据，不作为真值标签。

流程：

```text
pulse beat candidates
+ ppg beat candidates
→ monotonic nearest matching
→ match rate
→ lag statistics
→ reference evidence
```

正式M1 metric只输出`ppg_match_rate`；median lag等保留内部。

真实pulse-PPG生理延迟范围标记`pending_h1_calibration`。

## 14. 质量规则优先级

建议冻结为：

```text
blocked_before_quality
→ data_integrity_failure
→ no_contact
→ saturated
→ insufficient_duration
→ motion_artifact
→ unstable_baseline
→ weak_signal
→ reference_mismatch
→ manual_review_required
→ acceptable
```

同一窗口可保留多个evidence，但primary label按上述优先级唯一确定。

## 15. 正式reason code映射

| SP语义 | M1QualityResult.reason_codes |
|---|---|
| duration不足 | `too_short`, 可加`insufficient_beats` |
| 近常量 | `near_constant` |
| no contact | `no_contact` |
| weak | `weak_amplitude` |
| lower/upper clip | `lower_saturation` / `upper_saturation` |
| baseline | `unstable_baseline` |
| motion | `motion_artifact` |
| interval不稳 | `unstable_intervals` |
| CRC | `crc_errors` |
| sequence | `sequence_gaps` |
| timestamp | `timestamp_errors` |
| sensor disconnect | `sensor_disconnected` |
| PPG unavailable | `reference_unavailable` |
| PPG mismatch | `reference_mismatch` |
| persistence | `persistence_failed` |
| unstable load / ambiguity | `manual_review_requested` |

模拟器expected中的大写原因码是测试oracle描述，不直接写入正式quality Schema。

## 16. 分阶段实现

### M1-P2A：输入、完整性、窗口与参数骨架（已完成）

已完成：

- `m1_sp` package；
- `SPParameterSet` / 参数分层与 digest；
- `InputNormalizer` → `NormalizedSession`；
- `IntegrityAnalyzer` → `IntegrityAnalysis`（含 recorded vs observed cross-check）；
- `StableWindowSelector` → 结构性窗口；
- `SPPreprocessor` 入口；
- structural / oracle-isolation tests；
- **不生成**最终质量分类 / `M1QualityResult`。

### M1-P2B：raw质量指标与规则分类（已完成）

已完成：

- `RawQualityMetrics` / `QualityMetricsInternal`（valid/clipping/std/baseline/motion/load）；
- `default_p2b_parameter_set()`：simulation-only 阈值经多 seed 表征冻结；P2A digest 兼容；
- `QualityEvaluator` 优先级与 integrity/safety 分流（`sensor_disconnected`→integrity，abort/device_fault→blocked）；
- `M1QualityProjector` → 正式 `M1QualityResult`（`score/confidence=null`，`parameter_status=synthetic_only`）；
- `SPQualityStageResult` 阶段性结果（非最终 P2D `SPProcessingResult`）；
- characterization fixture：`tests/fixtures/m1_sp/p2b_characterization.json`；
- 滤波 / 逐搏 / PPG / `reference_mismatch` 属于 P2C（已完成）；formal M1-P2 acceptance 仍属 P2D。

### M1-P2C：滤波、逐搏与PPG对齐（已完成）

已完成：

- `FilterBank`：`CausalFIRFilter`（prefix invariant / chunk==whole）+ `OfflineReviewFilter`（reflect pad）；
- `BeatDetector` / `BeatSegmenter`（offline_review 源；不用 BeatTimeline / scenario HR）；
- `PPGDetector` + `ReferenceAligner`（单调一对一；`lag_ms = ppg - pulse`）；
- 正式投影 `beat_count` / `ppg_match_rate`（0 beat 时不伪造 match_rate=0）；
- `insufficient_beats` / `unstable_intervals` / `reference_unavailable` / `reference_mismatch`；
- raw P2B 失败优先，滤波/beat 不得覆盖；
- 无 StableWindow → `window-none-0001` / `insufficient_duration` / `too_short`（禁止静默空 quality）；
- `default_p2c_parameter_set()`（`0.3.0-p2c`）；P2A/P2B digest 不变；
- characterization：`tests/fixtures/m1_sp/p2c_characterization.json`；
- **未实现**正式 `SPProcessor` / 16+2 acceptance / M1-P2 CI gate（P2D）。

### M1-P2D：统一处理入口与综合验收（未开始）

计划完成：

- `SPProcessor.process(...)`；
- provenance/version/digest；
- M1QualityResult Schema验证；
- Replay确定性；
- 16单attempt场景验收；
- 2 multiattempt逐attempt处理（不执行决策）；
- `generate_m1_p2_acceptance.py`；
- CI集成；
- 文档收口。

## 17. M1-P2与P3/P4边界

M1-P2不负责：

- 把quality/beats写入正式会话目录（P3）；
- Web/API展示（P3）；
- report生成（P3）；
- accept/retry/reposition/stop决策（P4）；
- multiattempt自动推进（P4）；
- hardware adapter（H1/M1真实接入）；
- 中医脉象或疾病分类。

## 18. 验收核心

M1-P2最终至少满足：

- 生产SP不读取scenario/expected oracle；
- 同一Replay输入和参数得到完全相同输出；
- 所有正式M1QualityResult通过Schema；
- `confidence=null`；
- simulation-only参数不会伪装成真实阈值；
- integrity failure无法被滤波/质量分数覆盖；
- causal/offline结果类型可区分；
- abort/device_fault不会被伪造为普通quality；
- D3和M1-P1正式验收继续通过；
- 无M1-P0契约变更。

## 19. 停止条件

出现以下任一情况时停止实现并评审：

- 必须修改冻结M1QualityResult Schema才能表达基础结果；
- 生产算法必须读取模拟器oracle才能通过场景；
- 必须把D2 synthetic AU声称为真实工程单位；
- 需要新增重大DSP依赖且无法用当前依赖合理实现；
- 为通过测试需要使用场景ID硬编码分类；
- D3或M1-P1回归失败；
- 需要提前进入APP/INT/H1范围。

## 20. 当前状态

本文件落地表示M1-P2设计阶段完成，可以进入M1-P2A实现；不表示M1-P2已完成。
