# M1-P3F 最终集成与验收计划

## 定位

M1-P3F 不是新的功能阶段。它证明完整 APP-A1-pre 栈：

P3A 持久化 + P3B 重放/投影/门控 + P3C API + P3D Web + P3E 冻结 M1Report

作为确定性、失败关闭、可追溯、可重放、非医学、pre-H1 软件包一起工作。

## 版本边界

| 名称 | 值 | 含义 |
| --- | --- | --- |
| P3F stage | `0.6.0-p3f` | 验收/发布阶段元数据 |
| AppAnalysis processing | `0.2.0-p3b` | 生产分析处理版本，P3F 不得改写 |
| APP fingerprint | `app-analysis-fingerprint:v1` | 生产分析指纹 |
| Report projection | `m1-p3e-report-projection-v1` | 生产报告投影 |
| SP processing | `0.4.0-p2d` | 生产 SP |
| SP parameter | `0.3.0-p2c` | 生产参数集 |
| SP fingerprint | `sp-result-fingerprint:v2` | 生产 SP 指纹 |
| 验收摘要 digest | `p3-acceptance-semantic-summary:v1` | **仅验收**，不是生产 fingerprint |

## 数据流与真值所有权

1. 模拟器/未来硬件只提供冻结 `M1Session` + raw `M1Sample`
2. SP 从 raw 重跑，APP 不得改写 SP
3. Quality Gate 消费冻结事实；pre-H1 正式参数保持 `null`
4. 报告由已提交 Session + AppAnalysis + run provenance 投影
5. HTTP GET 零突变；显式 `run_id` 或 `current_run_id`，禁止猜测
6. Web 只展示工程状态，不实现 SP，不新增报告 UI

## 场景矩阵

冻结 P1 registry：16 单场景 + 2 多尝试计划 = 18 cases，21 processing attempts。

`normal_high_quality`：`analysis_allowed=true`，但 `formal_parameters_allowed=false`，`objective_parameters=null`。旧设计行“formal summary available”已被 H1 未校准事实取代。

多尝试只暴露独立 attempt 事实，不发出 INT `accept` / `retry` / `reposition`。

## 语义 golden

路径：`tests/fixtures/m1_app/p3_golden.json`

- `golden_source_sha` = P3E 合并 SHA `2f4f88cc69fbdfb1e129d347025695334542eb9e`
- 在基线生产管线上生成，禁止用 P3F HEAD 自批准
- 不含波形、软件 SHA、时间戳、路径

## 性能

记录 8s@250Hz 与 60s@250Hz 的 raw/SP/APP/report/E2E/内存，以及 `max_points<=5000` 的显示下采样。

这是软件表征，不是实时硬件性能或临床时延。

## 主张边界

M1-P3 PASS 表示 APP-A1-pre 软件实现已就绪，可进入下一软件阶段。

它不表示：M1 完成、H1 完成、硬件验证、临床验证、医学诊断验证。

P4 / Issue #29 的 M1-P3 勾选必须等到 P3F Merge Closeout。
