# M1 SP-S1-pre质量规则与参数策略

版本：0.1.0；状态：设计冻结，参数值待P2B基于M1-P1黄金场景表征后确定。

## 1. 目的

本文件冻结“参数从哪里来、哪些参数可以在M1-P2使用、哪些参数必须等H1”的规则。

核心原则：

> 先冻结参数语义和来源，再冻结数值；模拟阈值只用于回归，不冒充真实设备阈值。

## 2. 参数状态

### 2.1 structural_default

不依赖传感器量级或人体统计的结构参数，例如：

- sequence/timestamp显式失败是完整性错误；
- disconnected sample不可用于pulse分析；
- window索引半开区间；
- beat匹配一对一；
- causal filter不得访问未来值；
- `confidence=null`。

这类规则可直接冻结。

### 2.2 simulation_only

从M1-P1固定模拟场景获得，只用于软件回归，例如：

- no-contact raw load边界；
- weak-signal pulse std阈值；
- synthetic baseline drift阈值；
- synthetic motion residual阈值；
- unstable-load阈值；
- PPG synthetic match/lag阈值；
- minimum valid duration用于P1场景区分。

正式`M1QualityResult.parameter_status`在M1-P2模拟验收中使用`synthetic_only`。

### 2.3 pending_h1_calibration

真实硬件接入后必须重新确定：

- ADC→N/kPa等真实工程单位；
- 真实接触力稳定区间；
- 弱信号阈值；
- 运动伪影阈值；
- 真实PPG延迟/匹配范围；
- 真实滤波频带；
- 有效采集时长；
- 真实beat prominence/noise阈值。

### 2.4 frozen_h1

只有H1真实数据校准和独立复验后才能出现。M1-P2禁止生成。

## 3. ParameterProfile

建议强类型结构：

```text
SPParameterProfile
├── profile_id
├── version
├── parameter_status
├── window
├── filter
├── quality
├── beat
├── reference
└── configuration_digest
```

### 3.1 WindowParameters

候选字段：

```text
min_valid_duration_s
max_invalid_gap_samples
load_stability_window_s
load_std_max_raw
load_slope_max_raw_per_s
```

### 3.2 FilterParameters

候选字段：

```text
filter_family = fir
causal_num_taps
offline_num_taps
low_cut_hz | None
high_cut_hz
edge_mode
```

真实cutoff在H1前不声明为人体最优值；P2只冻结synthetic profile。

### 3.3 QualityParameters

候选字段：

```text
no_contact_load_max_raw
near_constant_std_max_raw
weak_signal_std_max_raw
clipping_fraction_max
baseline_drift_max_raw
motion_hf_ratio_max
motion_load_std_max_raw
unstable_load_std_max_raw
```

### 3.4 BeatParameters

候选字段：

```text
min_peak_distance_s
min_prominence_raw
foot_search_s
min_beats_per_window
max_interval_cv
```

### 3.5 ReferenceParameters

候选字段：

```text
max_match_lag_ms
min_ppg_match_rate
max_lag_mad_ms
```

## 4. 参数选择流程

P2B/P2C不得凭感觉填阈值。

对每个candidate metric：

1. 对固定M1-P1场景和多个seed计算分布；
2. 同时计算normal baseline；
3. 检查场景间是否有稳定margin；
4. 选择明确的simulation-only阈值；
5. 把阈值、分布摘要、seed集合、版本写入测试fixture/文档；
6. 加入边界值测试；
7. 标记真实阈值仍`pending_h1_calibration`。

如果某一metric无法稳定区分模拟场景：

- 不允许按scenario_id硬编码；
- 应调整metric或承认`manual_review_required`；
- 不得扩大为未经证据支持的真实规则。

## 5. 质量判定层级

### 5.1 Layer 0：blocked before quality

不是普通QualityLabel：

```text
emergency_stop
SAFE_HOLD
unsupported generic device fault
```

输出`SPProcessingResult.processing_status=blocked_before_quality`。

### 5.2 Layer 1：data integrity

任何结构性完整性失败优先于波形质量：

```text
crc_errors
sequence_gaps
timestamp_errors
sensor_disconnected
persistence_failed
```

primary label：`data_integrity_failure`。

### 5.3 Layer 2：contact / saturation / duration

顺序：

```text
no_contact
→ saturated
→ insufficient_duration
```

理由：无接触和削顶属于基础可用性问题，不应被后续滤波掩盖。

### 5.4 Layer 3：artifact / baseline / amplitude

```text
motion_artifact
→ unstable_baseline
→ weak_signal
```

### 5.5 Layer 4：reference / ambiguity

```text
reference_mismatch
→ manual_review_required
→ acceptable
```

## 6. 正式label与reason映射

### acceptable

条件：所有硬门和simulation-only质量规则通过。

```text
reason_codes = []
score = null
confidence = null
```

### weak_signal

正式reason：

```text
weak_amplitude
```

可附加：

```text
near_constant
```

但no_contact成立时优先no_contact。

### no_contact

正式reason至少：

```text
no_contact
```

可附加`near_constant`。

必须区分：

```text
mechanical no contact: sensors connected
sensor disconnection: data_integrity_failure
```

### saturated

正式reason：

```text
upper_saturation
lower_saturation
```

若两类都发生可同时保留。

### unstable_baseline

正式reason：

```text
unstable_baseline
```

### motion_artifact

正式reason：

```text
motion_artifact
```

### insufficient_duration

正式reason：

```text
too_short
```

若beat不足可加：

```text
insufficient_beats
```

### data_integrity_failure

正式reason按实际观测组合：

```text
crc_errors
sequence_gaps
timestamp_errors
sensor_disconnected
persistence_failed
```

### reference_mismatch

正式reason：

```text
reference_mismatch
```

PPG缺失但pulse本身可分析时，先记录`reference_unavailable`，primary label由profile策略决定；首版建议`manual_review_required`而不是假设reference mismatch。

### manual_review_required

正式reason：

```text
manual_review_requested
```

内部evidence可以进一步说明：

```text
unstable_contact_load
ambiguous_metric_overlap
reference_unavailable
```

但内部code不得写进M1 reason_codes。

## 7. 指标定义冻结要求

实现前必须对每个正式metric冻结唯一公式。

### valid_fraction

```text
valid samples in window / total samples in window
```

“valid”的mask来源必须固定，不允许不同模块各自定义。

### clipping_fraction

```text
pulse clipping!=none AND pulse valid
-------------------------------------
pulse valid samples
```

### baseline_drift_raw

P2B已冻结 `baseline_drift_raw:v1`（segment-median excursion）：

```text
seg_n = max(minimum_segment_samples, round(N * segment_fraction))
medians = median of each full contiguous segment
earlier, later = ordered indices of argmin/argmax(medians)
baseline_drift_raw = median[later] - median[earlier]
```

阈值比较使用 `abs(baseline_drift_raw)`。选择该公式是因为合成 baseline 场景为中部 envelope 漂移，first/last median 差无稳定 margin。一旦 fixture 建立，不得无版本更新地改变公式。

### pulse_std_raw

P2B冻结：`np.std(valid_raw_pulse, ddof=0)`（population std）。

### beat_count

只统计`BeatCandidate.valid=true`且位于当前window的beat。

### ppg_match_rate

建议：

```text
matched pulse beats / valid pulse beats
```

分母为0时输出`null`，不得输出0伪装为“完全不匹配”。

## 8. 指标不应泄漏场景真值

禁止metric直接来自：

- FaultKind；
- fault_schedule；
- ScenarioDefinition；
- expected quality；
- simulator random generator内部truth。

例如motion必须从pulse/load实际样本推导，不能读取`FaultKind.MOTION_ARTIFACT`。

## 9. 多seed稳定性

simulation-only profile冻结前，每个质量类至少使用多个seed复验。

要求：

- normal在多个seed保持acceptable；
- target scenario在多个seed保持目标语义；
- 阈值不能只贴合一个golden seed；
- 不同seed允许metric数值变化，但label应稳定。

## 10. 边界值测试

所有数值阈值必须至少测试：

```text
just below threshold
exact threshold
just above threshold
```

并明确使用`<`、`<=`、`>`或`>=`。

禁止因浮点误差产生不稳定分类；必要时使用明确tolerance并写入config。

## 11. filter参数策略

### causal

P2参数目标是证明：

- 因果；
- chunk一致；
- delay已知；
- 不破坏时间索引。

不证明真实最优频带。

### offline

目标是：

- 确定性；
- 对称处理；
- 边缘策略固定；
- 用于beat/reference研究。

滤波前后的质量分类不得出现“raw saturation被滤波后变acceptable”之类绕过。

## 12. Beat参数策略

Beat detector的simulation-only阈值应以：

- normal多个seed；
- weak signal；
- motion；
- insufficient duration；

联合表征。

目标不是对任意人体心率范围做医学保证，而是形成可替换的算法结构。

## 13. PPG参数策略

PPG alignment在P2只验证：

- 同一共享节律下可匹配；
- synthetic额外延迟能导致reference mismatch；
- missing reference不崩溃；
- 一对一匹配确定性。

真实lag范围必须H1校准。

## 14. 版本策略

建议：

```text
processing_version = sp-s1-pre-0.1.0
parameter_version = m1-p2-sim-0.1.0
parameter_status = synthetic_only
```

参数配置变化导致`configuration_digest`变化。

仅代码重构且数值/语义不变时，processing_version可变化；parameter_version不必变化。

阈值或公式变化必须更新parameter_version和golden。

## 15. 设计退出条件

进入P2B实现前必须确认：

- 所有质量标签都可投影到冻结QualityLabel；
- 所有正式reason/metric都来自M1-P0允许集合；
- 额外研究指标有内部承载结构；
- simulation-only和pending-H1边界清晰；
- 生产代码不需要scenario oracle；
- 不需要修改M1-P0契约。
