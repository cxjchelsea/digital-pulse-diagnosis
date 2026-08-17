# M1-P3E — 预验收报告投影与正式验收

## 状态

- Stage: M1-P3E
- Baseline / P3E base: `a1a6183bcc2e6b53db8721416513c70a7163543b`
- Acceptance version: `m1-p3e-acceptance-v1`
- Projection version: `m1-p3e-report-projection-v1`
- 目标：冻结 `M1Report` 投影、遗留/新 run 只读 API、确定性 `report_id` 与 fail-closed 完整性
- **明确不等于**：H1 成功、M1 完成、医学有效性、P4 INT 决策可用

## 边界（必须遵守）

| 规则 | 说明 |
|------|------|
| `M1Report` 为 P0 冻结合同 | 不改 `m1_contracts.py` / `protocols/m1-report.schema.json` 语义 |
| 无 SP 重算 | 报告只消费已提交 Session / AppAnalysis / run provenance |
| 无 INT 决策 | `decision_summary.final_action` 恒为 null；`decision_rule_version` 恒为 null（pre-P4） |
| 无医学结论 | 始终含 `not_for_medical_use`；禁止诊断/证型/风险评分等用语 |
| `analysis_allowed` ≠ `formal_parameters_allowed` | 可分析 ≠ 可输出正式客观参数 |
| pre-H1 `objective_parameters` | 在 `formal_parameters_allowed=false` 时必须为 null |
| 遗留 GET 零突变 | 无 `report.json` 的旧 run 仅内存投影，不写盘 |
| 新 run 不可变 `report.json` | 持久化后 GET 只读校验；篡改 fail-closed |
| P3E 成功 ≠ H1/M1 成功 | formal acceptance 只证明软件预验收报告链路 |

## 架构

```
已提交 Session + AppAnalysis + run provenance
  → M1PreAcceptanceReportBuilder（纯投影）
  → report.json（新 persist run）
  → GET /api/m1/sessions/{id}/report（零写入）
  → scripts/generate_m1_p3e_acceptance.py
  → artifacts/acceptance/m1-p3e-acceptance.json
```

## 主要门禁类别

1. **合同冻结**：`frozen_m1_report_contract_unchanged`、`frozen_m1_report_schema_unchanged`
2. **投影语义**：schema/契约、确定性、`analysis_allowed` 映射、限制码枚举、状态机映射
3. **持久化完整性**：新 run 含 report、checksum、语义联动、篡改 fail-closed
4. **只读 API**：显式/当前 run 选择、无 current 禁止猜 `runs[0]`、稳定错误码、零突变
5. **回归**：P3D web 源码不变、web test/build、P3C/P3B/P2/P1/D3、P2 canonical golden、`d3-v1.0.0` tag
6. **隔离**：无 oracle、无新 SP 算法、无医学声称

## 冻结对照

- P2 canonical golden digest: `8e0ba895050f3d691d8ab3f8ec5ee8147782306c85a8e7af64bb259cad101b3b`
- D3 tag object: `da85aee746453e92b0029ae6ec4f51fefc769e4e`
- D3 tag target: `d0251b3741d99bab955fa288c57424abd301b0b1`
- Tag name: `d3-v1.0.0`

## 验证命令

```powershell
cd d:\project\digital-pulse-diagnosis\.worktrees\m1-p3e-preacceptance-report
python -m pytest -q tests/test_m1_app_report_p3e.py tests/test_m1_api_p3e.py tests/test_m1_p3e_acceptance.py
python scripts/generate_m1_p3e_acceptance.py
```

若 web 已由独立 CI job 验证，可跳过本地 npm：

```powershell
$env:M1_P3E_ASSUME_WEB_PASSED="1"
python scripts/generate_m1_p3e_acceptance.py --skip-web
```

成功判据：`acceptance=true` 且 `failed_gates=[]`。

## 产物

- `artifacts/acceptance/m1-p3e-acceptance.json`
- CI：Python job 在 P3C 之后运行并断言 P3E；P3D 历史 `frozen_backend` 断言不再作为合并门禁（P3E 授权扩展 `m1_app`）
