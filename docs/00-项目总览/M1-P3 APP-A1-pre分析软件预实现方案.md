# M1-P3 APP-A1-pre分析软件预实现方案

版本：0.1.0-design；状态：方案冻结候选，尚未开始P3A实现。

Baseline：`main@5a924289e912f60c448a399c01d3bf7dd916d505`；上游M1-P0/P1/P2均已完成并合并。

## 1. 阶段定位

M1-P3位于M1-P1模拟/重放和M1-P2信号处理之后、M1-P4规则决策之前：

```text
M1-P1 Simulator / persisted session / Replay
                         ↓
M1-P2 SPProcessor → SPProcessingResult
                         ↓
M1-P3 APP-A1-pre
                         ↓
M1-P4 INT-I1-pre（本阶段不实现）
```

M1-P3把冻结的会话、原始样本和SP结果组织成可落盘、可重放、可观察、可解释、可追溯的分析视图和M1预验收报告。它不改变采集契约，不重新实现SP算法，也不产生自动决策或医学结论。

核心原则：

```text
Raw First
Replay First
Traceability First
Quality Gate First
No Medical Claim
```

## 2. Goals

- 以`M1Session`、持久化`M1Sample`和`SPProcessingResult`为唯一正式输入；
- 保留并校验原始会话，任何正式分析都可从原始数据重放；
- 提供原始/处理波形、载荷、PPG、窗口、逐搏、参考匹配、质量和完整性视图；
- 以统一且fail-closed的Quality Gate阻断不合格会话的正式参数；
- 生成符合冻结`M1Report`合同的预验收报告；
- 完整记录软件SHA、SP/参数/APP版本、配置digest、SP语义指纹和工程单位状态；
- Direct与Replay在APP语义字段上等价；
- 允许未来将模拟数据源替换为Hardware Source，而无需重写APP核心；
- 为M1-P5和M1-PV提供确定性验收资产。

## 3. Non-goals

- 不新增或调整滤波、逐搏、PPG对齐、质量分类及其阈值；
- 不修改`SPProcessingResult.result_sha256`或`sp-result-fingerprint:v2`；
- 不实现P4的accept/retry/reposition/stop等INT动作；
- 不扩展设备二进制协议、固件协议或Hardware packet；
- 不接真实硬件，不声称H1标定完成；
- 不输出脉象、证候、疾病、治疗、医学风险等诊断结论；
- 不迁移前端框架，不引入大型UI/图表平台；
- 不把M1-P3通过描述为M1-P、H1或M1完成。

## 4. 当前仓库复用与缺口

| 区域 | 当前可直接复用 | P3需要新增 | 不应触碰 |
|---|---|---|---|
| M1合同 | `M1Session`、`M1Sample`、`M1QualityResult`、`M1Report`、`RawPersistenceStatus`、Schema校验、稳定JSON | APP内部模型和映射器 | P0字段、枚举和Schema语义 |
| Simulator | `SimulatorDataSource`、`M1SessionRecorder`、18案例、`samples.jsonl`、`events.jsonl` | APP source adapter | 场景定义和golden |
| Replay | `ReplayDataSource`、`resolve_contained_file`、session-relative路径检查 | `ReplayService`、checksum验证、只读/记录模式 | 读取`expected.json`决定生产结果 |
| SP | `SPProcessor`、`SPProcessingResult`、质量/窗口/滤波/逐搏/reference视图、`result_sha256` | 只读APP projection和持久化adapter | P2算法、参数、golden、semantic hash |
| Backend | FastAPI `create_app()`、现有D3 router模式、`PULSE_DATA_ROOT` | 独立`m1_app` router和services | D0-D3既有endpoint语义 |
| Frontend | React + TypeScript + Vite、现有CSS和简单SVG | `web/src/m1/`组件与P3页面状态 | 框架迁移、浏览器直接读filesystem |
| CI | pytest、unittest、D3/P1/P2 acceptance、Web build | P3 acceptance及artifact上传 | 弱化或删除既有门禁 |

当前`api.py`仍是包含多阶段路由的单文件入口，`web/src/main.tsx`也是单组件。P3只新增模块化router/service和`web/src/m1/`子树，不进行全仓后端或前端重构。

## 5. 冻结输入与事实来源

### 5.1 Acquisition truth

根目录`manifest.json`中的冻结`M1Session`是采集事实来源，包含source、completion、integrity、raw persistence、版本和文件引用。`samples.jsonl`和`events.jsonl`是原始会话资产。

P1模拟器目录中的`scenario.json`和`expected.json`属于模拟配置/测试oracle，不是APP生产输入。删除或篡改它们不得改变APP语义结果。

### 5.2 SP truth

`SPProcessor.process(session, samples, provenance=...)`返回的`SPProcessingResult`是SP业务事实来源。APP不得重新分类、重新选窗、重新检测逐搏或覆盖其字段。

冻结版本：

```text
processing_version = 0.4.0-p2d
parameter_version = 0.3.0-p2c
fingerprint_version = sp-result-fingerprint:v2
```

### 5.3 APP truth

P3新增的`app/manifest.json`是APP资产目录和完整性事实来源，但不是M1-P0交换合同。它不得替代或改写根`manifest.json`。

该双层manifest设计避免为P3擅自扩展冻结的`FileRole`：

- 根`manifest.json`：P0/P1 acquisition truth；
- `app/manifest.json`：P3 immutable analysis runs及其hash/status。

## 6. 总体架构与数据流

```text
SessionDataSource
  ├─ SimulatorSessionSource
  ├─ ReplaySessionSource
  └─ FutureHardwareSessionSource
          │
          ▼
SessionLoader ──► SessionValidator ──► RawAssetVerifier
          │                                  │
          └──────────────────────────────────┘
                                             ▼
                                       SPProcessor
                                             ▼
                                  immutable SPProcessingResult
                                             ▼
                                     AnalysisProjector
                                             ▼
                                        QualityGate
                                  ┌──────────┴──────────┐
                                  ▼                     ▼
                         diagnostic/display       formal parameters
                         projection always        only if allowed
                                  └──────────┬──────────┘
                                             ▼
                                      ReportBuilder
                                             ▼
                                versioned atomic persistence
                                             ▼
                                    FastAPI read model
                                             ▼
                                      React/Vite UI
```

### 6.1 层所有权

| 层 | 输入 | 输出 | 失败 | 所有权 |
|---|---|---|---|---|
| SessionLoader | session root + id | `M1Session`和资产定位 | not found/invalid/path escape | APP |
| SessionValidator | `M1Session` | validated session | schema/completion facts | P0合同 + APP adapter |
| RawAssetVerifier | file refs/checksums | verified raw handles | missing/corrupt/partial | APP |
| SPProcessor | session + samples | `SPProcessingResult` | frozen SP error | P2 |
| AnalysisProjector | session + SP result | display/semantic projection | projection error | APP |
| QualityGate | session + SP result | `AppGateDecision` | fail closed | APP消费P0/P2事实 |
| ReportBuilder | projection + gate | `M1Report` | schema/gate violation | APP映射，P0合同 |
| Persistence | immutable run assets | committed run | atomic write failure | APP |
| API/UI | committed read model | JSON/UI | stable error envelope | APP |

## 7. SessionDataSource接口

P3新增APP层协议，不修改P1的`M1DataSource`：

```python
class SessionDataSource(Protocol):
    @property
    def session(self) -> M1Session: ...

    @property
    def execution_mode(self) -> Literal["direct", "replay", "hardware"]: ...

    def samples(self) -> Iterator[M1Sample]: ...
```

规则：

- `session.source_type`始终表示采集来源；Replay不得把原模拟session伪装成hardware或改写为replay；
- `execution_mode`单独记录本次APP执行是direct/replay/hardware；
- `SimulatorSessionSource`适配Recorder完成后的session和样本；
- `ReplaySessionSource`包装现有`ReplayDataSource`；
- `FutureHardwareSessionSource`必须输出同一`M1Session`/`M1Sample`合同；
- APP pipeline不按scenario ID或source type分叉业务判断。

## 8. Raw-first行为

正式APP运行必须先得到明确的`RawPersistenceStatus`：

| raw status | APP行为 |
|---|---|
| `ok` | 可继续SP和Quality Gate |
| `partial` | session可查看；仅诊断性处理；正式参数/正式完成报告阻断 |
| `failed` | session保留并显示失败；正式分析和正式参数阻断 |
| `not_started` | 不得开始正式分析 |

Direct模式可以在采集时提供临时波形预览，但在原始资产持久化、关闭、hash校验和manifest最终提交前，不得发布正式APP run。

内存中仍有samples不能绕过`partial`/`failed`事实。允许对partial数据进行显式诊断性SP处理时，必须标为`diagnostic_only`，且Quality Gate仍为false。

## 9. Session persistence

### 9.1 目录结构

沿用当前P1目录，不迁移现有原始文件：

```text
sessions/<session_id>/
├── manifest.json                 # frozen M1Session acquisition truth
├── samples.jsonl                 # or samples.partial.jsonl
├── events.jsonl
├── scenario.json                 # simulator-only; production APP ignores
├── expected.json                 # test oracle; production APP forbids
└── app/
    ├── manifest.json             # APP run catalog, update last
    └── runs/
        └── <run_id>/             # immutable
            ├── provenance.json
            ├── sp/
            │   ├── result.json
            │   └── series/
            │       └── <window>-<series>.npy
            ├── analysis.json
            ├── report.json
            └── checksums.json
```

`result.json`保存SP正式摘要、完整semantic fingerprint、版本和数组描述；实际显示序列按little-endian固定dtype写入`.npy`并记录dtype、shape和SHA-256。P3 adapter负责序列化，不把持久化字段反向加入P2 semantic hash。

### 9.2 APP manifest

APP manifest顶层先登记原始source资产：

```text
source_assets[{role, relative_path, size_bytes, sha256, checksum_origin}]
registered_at_utc
raw_integrity_assurance
```

当前P1 `M1Session.files`没有checksum字段，因此P3不得假装它已有采集时加密证明：

- Direct/新Recorder adapter应在seal时复用`SessionRecordResult.sample_stream_sha256`和event hash；
- 既有P1 session首次注册到APP时计算registration snapshot，并记录`checksum_origin=app_registration`；
- registration之前发生的语义篡改无法由P3追溯证明，必须显示`raw_integrity_assurance=from_app_registration`；
- registration之后任何byte变化都必须被检测并fail closed；
- 未来Hardware recorder应在采集seal时直接提供checksum，标记`checksum_origin=acquisition_seal`。

这不会修改P0 manifest或Schema。每个APP run至少记录：

```text
run_id
status = writing | complete | failed
execution_mode
relative_path
producer
app_processing_version
app_schema_version
software_commit_sha
app_configuration_digest
sp_result_sha256
sp_fingerprint_version
files[{role, relative_path, size_bytes, sha256}]
created_at_utc
```

所有路径必须是session-relative、POSIX分隔形式；禁止盘符、绝对路径和`..`。

### 9.3 原子提交

```text
create app/runs/<run_id>.tmp/
→ write each asset
→ close/fsync
→ calculate and verify hashes
→ write checksums.json last inside temp run
→ atomic rename to app/runs/<run_id>/
→ atomically replace app/manifest.json last
```

`app/manifest.json`只有在run目录完整提交后才能标记`complete`。崩溃遗留的`.tmp`目录不进入读取目录，需由显式维护流程处理，读取页面不得自动删除。

## 10. Replay architecture

### 10.1 Direct

```text
SimulatorSessionSource
→ raw persistence verified
→ SPProcessor
→ AnalysisProjector/Gates/Report
→ optional committed APP run
```

### 10.2 Replay

```text
ReplaySessionSource
→ root manifest + raw checksum/parser validation
→ SPProcessor recomputation
→ AnalysisProjector/Gates/Report
→ compare with selected stored run
```

Replay必须从raw/session重新进入SP，不得读取`expected.json`、scenario oracle或预存formal output来决定新结果。

### 10.3 Future Hardware

```text
FutureHardwareSessionSource
→ same M1Session/M1Sample
→ same SPProcessor
→ same APP pipeline
```

只有DAQ/Recorder adapter不同；SP、APP projector、gate、report和UI read model不得按hardware另写一套逻辑。

### 10.4 非破坏性策略

- `POST replay`默认`persist=false`，只在内存中重算并返回comparison；
- `persist=true`时只新增versioned run；
- 历史run不可修改，不能因为当前软件SHA不同而覆盖；
- 相同确定性run id已存在时，仅在所有hash相等时返回幂等成功；否则报`artifact_conflict`；
- `app/manifest.json`可以有`current_run_id`指针，但更新指针不删除历史run。

## 11. APP domain model

P3只建立UI/persistence/view聚合模型：

| 模型 | 责任 |
|---|---|
| `AppSessionRef` | session列表投影，不复制完整`M1Session` |
| `AppRunManifest` / `AppAssetRef` | APP资产目录、hash、版本和状态 |
| `AppAnalysis` | APP语义摘要、gate结果、provenance引用 |
| `AppChannelSeries` | raw/causal/offline/load/PPG的只读显示序列描述 |
| `AppTimeline` | window/quality/integrity事件的统一时间索引 |
| `AppBeatView` | SP beat的UI投影，保留window/beat identity |
| `AppReferenceView` | SP pulse↔PPG match投影 |
| `AppQualityView` | `M1QualityResult`投影，不重分类 |
| `AppIntegrityView` | SP/session integrity投影，与quality分栏 |
| `AppGateDecision` | allowed、阻断码和事实来源 |
| `AppProvenance` | source、执行模式、版本、digest和limitations |
| `AppReplayComparison` | stored vs replayed语义和版本比较 |

禁止创建`AppBeatAlgorithmResult`、`AppQualityAlgorithmResult`等复制SP业务语义的模型。APP view必须保留指向原SP window/beat/reference的稳定ID。

## 12. Quality Gate

P3冻结`AppFormalAnalysisPolicy v0.1.0-p3a`，采用fail-closed规则。`analysis_allowed=true`仅当以下全部满足：

1. `M1Session.validate_schema()`成功；
2. `session.completed is True`且`completion_reason is None`；
3. `raw_persistence_status == ok`；
4. manifest引用的raw资产存在、位于session root内、parser和APP source-asset checksum均通过；
5. `SPProcessingResult.processing_status == quality_evaluated`；
6. `blocking_codes`为空；
7. 至少存在一个`M1QualityResult`；
8. 所有用于正式摘要的quality result均为`QualityLabel.ACCEPTABLE`；
9. formal metrics为有限合法值，且没有缺失必需的逐搏/时间证据；
10. APP policy/config合法且provenance完整。

Gate只组合P0/P2事实，不重新检查波形幅度或阈值。多窗口采用保守策略：任一正式窗口非acceptable即整体阻断；若未来需要“选最佳窗口”，必须先形成独立规则评审，不能由UI选择绕过。

`analysis_allowed=false`时仍允许显示：raw、integrity、已有quality、reason codes、SP已产生的beats/reference、版本和failure explanation。

## 13. Formal Parameter Gate

```text
analysis_allowed = false
→ M1Report.objective_parameters = null
→ API formal_parameters = {status: "unavailable", values: null, reasons: [...]}
```

绝不使用`0`、空字符串或NaN表示不可用。

`analysis_allowed=true`时P3-pre只允许生成客观、可追溯且不依赖新算法的摘要，例如：

- beat count；
- median beat interval和由其直接换算的pulse-rate-like摘要；
- SP正式quality metrics；
- PPG match rate/lag摘要；
- raw/synthetic load摘要；
- integrity统计。

每项必须携带单位/域和status。无H1标定时必须显示`synthetic_only`或`pending_h1_calibration`，不得声称真实力、压力或临床心率。

## 14. APP版本与独立语义哈希

版本策略与SP分离：

```text
P3A 0.1.0-p3a
P3B 0.2.0-p3b
P3C 0.3.0-p3c
P3D 0.4.0-p3d
P3E 0.5.0-p3e
P3F 0.6.0-p3f
```

持久化schema单独使用`m1-p3-app-manifest-v1`、`m1-p3-analysis-v1`等版本，不与processing version混用。

P3可定义：

```text
app-analysis-fingerprint:v1
app_analysis_sha256
report-semantic-fingerprint:v1
report_sha256
```

它们必须明确独立于`SPProcessingResult.result_sha256`。APP颜色、折叠、viewport、display downsampling、绝对路径、生成时间和run容器identity不得进入APP语义哈希。

## 15. Backend/API设计

新增`src/digital_pulse/m1_app/`领域/service包及`m1_app_api.py` router，由现有`create_app()`注册。后端负责文件访问、SP、projection、gate、report和downsampling；浏览器不直接读取session filesystem。

最小API：

| Method/Path | 语义 |
|---|---|
| `GET /api/m1/sessions` | session列表，可按source/status分页过滤 |
| `GET /api/m1/sessions/{id}` | session、当前APP run和状态摘要 |
| `GET /api/m1/sessions/{id}/channels` | raw/processed/load/PPG窗口化序列，支持start/end/max_points |
| `GET /api/m1/sessions/{id}/analysis` | quality/beat/reference/integrity/provenance投影 |
| `GET /api/m1/sessions/{id}/report` | 选定run的冻结`M1Report`投影 |
| `GET /api/m1/sessions/{id}/runs` | 历史APP run列表 |
| `POST /api/m1/sessions/{id}/replay` | 默认只读重放；显式`persist`才新增run |

Replay请求示例：

```json
{
  "persist": false,
  "compare_run_id": "optional-existing-run",
  "allow_diagnostic_incomplete": false
}
```

响应采用统一错误封装：

```json
{
  "error": {
    "code": "raw_asset_corrupted",
    "message": "Raw asset checksum does not match the manifest.",
    "details": {"role": "samples"}
  }
}
```

不得向UI泄露Python exception、绝对路径或堆栈。

## 16. UI范围

保持React/TypeScript/Vite，不新增路由/图表大依赖。P3页面放入`web/src/m1/`，通过最小页面状态或轻量组件切换接入现有入口。

### 16.1 Session List

显示session_id、acquisition source、created time、completion、raw status、quality摘要、analysis allowed、APP/SP版本和limitations。

### 16.2 Session Detail

```text
Header: session/source/completion/raw/quality/version
Timeline: pulse/load/PPG/windows/beats/reference/quality/integrity
Summary: quality/beats/reference/integrity/limitations
```

### 16.3 Replay/Audit

选择历史run，只读Replay，比较software SHA、APP/SP版本、parameter version、SP result SHA、APP/report SHA和差异原因。默认不写盘。

### 16.4 Pre-Acceptance Report

展示`analysis_allowed`、report status、objective parameters availability、failure reasons、versions和limitations，并持续突出`not_for_medical_use`。

## 17. Waveform和Timeline

- raw pulse、causal filtered pulse、offline review pulse必须分轨/分图例；
- load和PPG单独标识单位域；
- selected/candidate window、blocked interval来自SP，只展示不重选；
- beat至少显示foot、peak、segment、interval、validity/reason；
- reference显示pulse beat↔PPG beat、matched count、match rate和lag；
- integrity单独显示CRC、sequence gap、timestamp regression、disconnect、raw persistence failure；
- quality timeline按window显示label、reason codes和formal metrics。

UI downsampling只用于显示，不进入SP/APP分析或语义哈希。API采用viewport范围和`max_points`，首版使用确定性min/max bucket或等价算法保留峰值；返回`display_downsampled=true`和原/显示点数。

## 18. Provenance面板

至少展示：

```text
session_id
acquisition source
execution mode
software_commit_sha
APP processing/schema version
APP configuration digest
SP processing version
SP parameter version/status
SP configuration digest
SP semantic fingerprint version
SP result_sha256
engineering-unit conversion/status
raw/app asset hashes
limitations
```

模拟输入必须显示`synthetic_only`和`synthetic_input`；未真实标定必须显示`pending_h1_calibration`。

## 19. Report Builder与M1Report映射

`M1PreAcceptanceReportBuilder`只负责映射，不替换冻结`M1Report`：

| M1Report字段 | P3来源 |
|---|---|
| `session_id/source_type` | `M1Session` |
| `report_status` | completion/raw/SP/gate的确定性映射 |
| `analysis_allowed` | `AppGateDecision.allowed` |
| `quality_summary` | SP quality只读投影 |
| `integrity_summary` | session + SP integrity |
| `objective_parameters` | Formal Parameter Gate；blocked时必须null |
| `decision_summary` | P3固定为空/`not_started`，不得伪造P4决策 |
| `version_manifest` | APP/SP/session provenance |
| `limitations` | P0冻结限制集合；包含`not_for_medical_use` |
| `failure_summary` | gate阻断码的可解释摘要 |

状态映射：

- abort→`ABORTED`；
- partial/incomplete→`INCOMPLETE`；
- corrupted/persistence failed/SP failure→`FAILED`；
- manual review quality→`MANUAL_REVIEW_REQUIRED`；
- 仅全部gate通过→`COMPLETE`。

冻结合同通过`LimitationCode.NOT_FOR_MEDICAL_USE`表达非医疗用途。API可派生`not_for_medical_use=true`方便UI，但不得从`M1Report.limitations`删除该值。

## 20. Error model

| code | 默认HTTP | 行为 |
|---|---:|---|
| `session_not_found` | 404 | 不泄露root路径 |
| `manifest_invalid` | 422 | session可在列表标为invalid |
| `raw_asset_missing` | 422 | fail closed |
| `raw_asset_corrupted` | 422 | fail closed |
| `path_escape` / `symlink_escape` | 400 | 拒绝访问并审计 |
| `incomplete_session` | 409 | 默认拒绝formal replay |
| `replay_failed` | 422 | 保留历史run |
| `sp_processing_failed` | 422 | 不生成formal report |
| `analysis_blocked` | 409 | 返回阻断原因，可显示诊断视图 |
| `report_generation_failed` | 422 | 不提交run |
| `persistence_failed` | 500 | manifest不得标complete |
| `artifact_conflict` | 409 | 不覆盖已有run |

Incomplete/blocked session必须可以打开并显示事实；不得因为没有正式quality result而崩溃或制造伪quality。

## 21. Security

- configured session root是唯一允许的文件边界；
- session/run ID使用现有identifier约束；
- 所有manifest路径先拒绝absolute/drive/UNC/`..`，再`resolve()`检查包含关系；
- 关键资产拒绝symlink，或验证resolved target仍位于root；
- 禁止浏览器提交任意filesystem路径；
- 对manifest、JSONL行数/行长、文件大小和数组shape设置可配置上限；
- 校验文件role唯一性、size和SHA-256后再解析；
- 原子写入只在session root内使用固定临时后缀；
- 错误响应清除绝对路径、栈和敏感subject/operator字段；
- 列表接口默认不返回原始subject/operator identifier；
- 篡改、path escape和checksum失败必须写入APP审计日志，但不得覆盖采集events。

## 22. Determinism

相同persisted session、SP版本/参数、APP版本/config应产生相同：

- `SP result_sha256`；
- APP semantic projection；
- gate decision和reason ordering；
- `app_analysis_sha256`；
- report semantic content和`report_sha256`。

以下排除：绝对路径、run目录名、`generated_at_utc`、API请求时间、UI状态和display-only downsampling。浮点采用P2已冻结的12位有效数字canonical规则，字典key排序，集合转排序列表，JSON UTF-8/LF、禁止NaN/Infinity。

`M1Report.generated_at_utc`仍保留合法时间，但不进入report semantic hash；`report_id`由semantic hash派生，避免时间污染语义。

## 23. 数据规模与性能预算

当前250 Hz、三原始通道，compact `M1Sample` JSONL按约300–600 bytes/sample估算：

- 8秒：约0.6–1.2 MB；
- 60秒：约4.5–9 MB；
- 30分钟：约135–270 MB。

P3-pre策略：

- raw JSONL流式读取，不一次性构建Python dict列表；
- SP仍按当前接口消费完整会话，P3记录处理时间和peak memory以发现数量级回归；
- channel API按viewport读取/缓存，默认`max_points<=5000`；
- 前端DOM/SVG不直接承载超长完整序列；
- P3不提前引入数据库、对象存储或复杂分布式chunk协议。

## 24. ADR-style关键决策

### ADR-P3-01 Raw-first persistence

正式分析只能在raw状态和完整性确定后发布；内存样本不绕过持久化失败。

### ADR-P3-02 Replay source abstraction

统一`SessionDataSource`，采集来源与执行模式分离；未来hardware只替换adapter。

### ADR-P3-03 SP result is immutable upstream truth

APP只投影和持久化SP结果，不复制或重算P2业务逻辑。

### ADR-P3-04 Quality gate before formal parameters

`analysis_allowed=false`强制正式参数为null/unavailable，诊断显示与正式输出分离。

### ADR-P3-05 Session-relative paths

所有资产引用session-relative并强制containment/symlink检查，目录可跨机器复制重放。

### ADR-P3-06 Versioned non-destructive replay

历史run不可变；Replay默认只读，显式记录只新增版本化run。

### ADR-P3-07 Dual manifest boundary

P0 acquisition manifest保持冻结；P3使用独立APP manifest登记分析资产，不扩展P0 `FileRole`。

## 25. 实施拆分

### P3A — APP contracts / persistence foundation

- Goal：冻结APP内部模型、资产manifest、loader和atomic persistence；
- Scope：`m1_app` models/errors/paths/manifest/persistence；
- Deliverables：可保存、加载、hash校验、拒绝非法路径；
- Tests：schema/model、atomic failure、missing/tamper/path/symlink；
- DoD：不运行复杂SP/UI也能可靠登记和读取APP run。

### P3B — Replay + analysis projection

- Goal：统一source、SP集成、projection、Quality/Formal Parameter Gate；
- Scope：ReplayService、SP adapter、projector、gate、report draft；
- Deliverables：Direct/Replay APP semantic equivalence；
- Tests：18案例、oracle isolation、blocked/partial；
- DoD：gate不可绕过，P2 semantic保持不变。

### P3C — Backend API

- Goal：提供session/read model/replay/report API；
- Scope：FastAPI router、service wiring、error envelope、viewport channels；
- Deliverables：最小endpoint和API tests；
- Tests：404/409/422、path、pagination、read-only replay；
- DoD：浏览器无需filesystem权限即可访问全部P3视图。

### P3D — Frontend visualization

- Goal：最小APP-A1-pre UI；
- Scope：Session List、Detail、Replay/Audit、Report；
- Deliverables：多轨波形、quality/beat/reference/integrity/provenance；
- Tests：TypeScript build、关键状态component/API contract tests；
- DoD：正常、blocked、partial、corrupt状态可解释显示。

### P3E — Report + deterministic acceptance

- Goal：冻结M1Report映射和P3正式验收；
- Scope：report builder、semantic hashes、acceptance generator；
- Deliverables：`m1-p3-acceptance.json`、golden小摘要；
- Tests：determinism、corruption、gate、report schema；
- DoD：所有formal gates自动阻断。

### P3F — Final integration / review

- Goal：完整E2E、CI、文档、独立Final Review；
- Scope：全量矩阵、回归、性能预算和closeout；
- Deliverables：最终验收证据和PR；
- Tests：pytest/unittest/D3/P1/P2/P3/Web；
- DoD：Issue #29仅在完整P3 merge closeout后勾选P3，P4仍未开始。

## 26. Dependencies

- M1-P0合同与`m1-*.schema.json`；
- M1-P1 Recorder/Replay/18案例；
- M1-P2 `SPProcessor`、`SPProcessingResult`和semantic fingerprint；
- FastAPI/Pydantic/Numpy；
- React/TypeScript/Vite；
- D3/P1/P2 acceptance保持绿色。

P3不依赖P4实现；`decision_summary`在P3明确为not started/empty。

## 27. Risks与缓解

| 风险 | 缓解 |
|---|---|
| raw体积增长 | streaming、viewport、显示降采样、尺寸上限 |
| 前端绘图性能 | max_points、min/max bucket、避免全量DOM |
| Replay覆盖历史 | immutable run + 默认只读 + 显式persist |
| 路径穿越/symlink | identifier、relative path、resolve containment、拒绝symlink |
| 浮点/serialization漂移 | 复用P2 canonical float、稳定JSON和固定dtype |
| session schema drift | P0 schema validation；不静默兼容未知版本 |
| 复制SP模型 | projector只读映射，contract-gate测试检查P2字段库存 |
| quality bypass | 单一backend gate，report schema和negative tests双重阻断 |
| partial session崩溃 | diagnostic view与formal output分离 |
| future hardware分叉 | `SessionDataSource`和acquisition/execution语义分离 |
| P0 FileRole不足 | dual manifest，不修改P0合同 |
| SP完整对象不适合JSON | P3 adapter分离metadata与`.npy`序列，SP对象本身不变 |

## 28. Frozen boundaries

P3 acceptance必须验证以下路径/语义未漂移：

```text
src/digital_pulse/m1_contracts.py
protocols/m1-*.schema.json（P0正式Schema）
src/digital_pulse/m1_simulator/
protocols/m1-simulator-*.schema.json
src/digital_pulse/m1_sp/
tests/fixtures/m1_simulator/golden_summaries.json
tests/fixtures/m1_sp/p2d_golden.json
d3-v1.0.0 tag object/target
```

P3 adapter缺口不等于P2 contract defect。只有APP无法在不篡改SP语义的情况下正确实现时，才停止并升级为独立P2 defect评审。

## 29. Stop conditions

出现任一情况停止P3实现：

- 必须修改P0 Schema或P2算法/golden才能继续；
- 必须读取scenario/expected oracle才能通过；
- raw failed/partial仍能产出正式参数；
- Direct/Replay semantic output不一致；
- 历史run会被普通Replay静默覆盖；
- 文件可逃逸session root或checksum篡改未被发现；
- APP把simulation阈值声称为真实H1标定；
- APP生成INT动作或医学结论；
- D3/P1/P2回归失败；
- 需要未经评审的大型依赖或框架迁移。

## 30. M1-P3最终DoD

- session可保存、加载和验证raw完整性；
- Replay可执行，Direct/Replay APP语义一致；
- raw、causal、offline、load和PPG可查看；
- window、beat、reference、quality、integrity可查看；
- blocked/partial/corrupt session可解释显示且fail closed；
- Quality Gate和Formal Parameter Gate不可绕过；
- provenance和limitations完整；
- `M1Report`Schema合法且始终保留`not_for_medical_use`；
- 历史artifact不可变，Replay默认只读；
- D3/P1/P2/P3 acceptance和Web production build全部通过；
- P3F独立Final Review与merge closeout完成；
- P4保持NOT_STARTED。

## 31. 本轮设计结论

本方案已回答APP事实来源、持久化、Replay起点、semantic output、Quality Gate、formal output、前后端接口、历史结果保护和P3A-P3F拆分。

```text
M1-P3 APP-A1-pre design = READY_FOR_IMPLEMENTATION

recommended next stage:
M1-P3A — APP contracts / persistence foundation
```

本结论只授权建议，不代表已开始P3A。等待明确实施授权。

## 32. P3A 实际实现记录

本轮在 `src/digital_pulse/m1_app/` 内完成了 P3A（APP contracts / persistence foundation），没有开始 P3B：

- 新增严格的 APP manifest、asset reference、checksum provenance、run provenance 与 session reference 模型；
- APP manifest schema 版本为 `m1-p3-app-manifest-v1`，P3A processing version 为 `0.1.0-p3a`；
- APP 状态与产物独立存放于 session root 下的 `app/`，不改写 P0 `M1Session` root manifest；
- legacy session 首次注册会对 raw manifest/samples/events 做 APP registration checksum snapshot；已存在 recorder checksum 时复用并验证其 provenance；
- 所有逻辑路径使用 POSIX 相对形式，拒绝绝对路径、drive/UNC、反斜杠、混合分隔符、`..` 与 symlink/junction 逃逸；
- run 先写入 `app/.tmp/<uuid>`，完成 fsync、checksum 和 provenance 后原子 rename 到 `app/runs/<run_id>`，最后原子更新 APP manifest；
- session 级跨平台 OS 文件锁串行化 manifest 的 read-modify-write，避免并发 run 提交相互覆盖；
- 已提交的 run 不允许覆盖；失败残留只报告为 orphan，不被加载器采信或静默删除；
- loader 对 raw 与已登记 APP 资产 fail closed，且不读取 simulator scenario/expected oracle，不调用 SP/Quality Gate/Report。

P3A 定向测试、全量 pytest/unittest、D3/P1/P2 回归和 Web production build 的最终实测结果记录在配套测试计划。Issue #29 的 P3 总复选框仍须保持未勾选；P3B-P3F 均未开始。

## 33. P3A Final Review修复与边界

独立攻击式Final Review识别并修复了仅属于P3A persistence foundation的阻断缺陷：

- registration与run commit现在复用同一session级OS文件锁；两个进程首次注册只产生一个snapshot，registration/commit并发不再丢run；
- 跨进程不同run不会last-writer-wins，同run只允许一个成功，另一个稳定返回`artifact_conflict`；
- model `from_dict()`不再把数字等错误类型静默转换为字符串，APP schema/processing version继续fail closed；
- Windows reserved device name、非法字符及尾随dot/space被拒绝，logical path仍统一为POSIX `/`；
- 普通registration只接受`recorder` supplied checksum，不能自行声称`hardware_seal`；P3A run只能记录`persistence_only`，不能虚构Direct/Replay/Hardware执行；
- raw samples/events在进入P1 parser前增加duplicate-key/NaN/Infinity严格JSONL扫描，权限与checksum I/O错误统一映射为不泄漏路径的`M1AppError`；
- orphan扫描拒绝被symlink/junction替换的`app/.tmp`或`app/runs`，且正式loader仍只采信manifest登记run；
- asset fsync失败不再被吞掉，atomic failure matrix覆盖temp、write、checksum、rename和manifest update关键转换。

完整性边界必须准确表述：APP manifest中的SHA-256是registered-state一致性anchor，不是数字签名。能够同时重写APP manifest与全部资产的恶意主体不在当前单用户本地session storage threat model内；未来需要跨信任域交换时应增加外部签名/可信seal。

已知非阻断限制：

- Windows junction由`os.path.isjunction`和实现审查覆盖；本轮自动化环境未稳定执行junction创建，Linux/Windows symlink及Windows跨进程锁已实际测试；
- containment check与最终open之间仍有极窄TOCTOU窗口；当前单用户、本地session目录模型下接受，未来多租户或不可信可写目录应使用handle-relative/no-follow I/O；
- `%2e%2e`在P3A filesystem loader中只是普通文件名；未来HTTP API必须先URL decode再调用P3A path validator。

本轮仍未实现P3B能力，没有修改root acquisition manifest、P0/P1/P2冻结实现或golden。
