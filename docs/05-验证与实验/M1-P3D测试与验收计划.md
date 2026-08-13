# M1-P3D — React Analysis UI / Waveform + Quality + Audit Views

## 状态

- Stage: M1-P3D
- Baseline: `5033cd5e76d62d0492a0cf79bf9e8a5b2150b637`（P3C merge）
- 目标：把冻结的 P3C HTTP API 变成可用的工程分析 UI
- **不做**：正式报告（P3E）、医学诊断结论、前端 SP/质量算法、后端语义变更

## 架构

```
P3C HTTP API
  → web/src/m1/api.ts（typed client）
  → M1Workspace React state（AbortController + token 防竞态）
  → presentation components
```

## 前端结构

- `web/src/main.tsx`：保留 D1/D2/D3，新增 `M1 Analysis` / `D3 Simulation / Legacy` 切换
- `web/src/m1/`：API client、types、workspace、panels

## 路由消费

仅使用冻结 P3C：

- `GET /api/m1/sessions`
- `GET /api/m1/sessions/{id}`
- `GET /api/m1/sessions/{id}/channels`
- `GET /api/m1/sessions/{id}/analysis`
- `GET /api/m1/sessions/{id}/runs`
- `GET /api/m1/sessions/{id}/runs/{run_id}`
- `POST /api/m1/sessions/{id}/replay`

## 安全边界

- `formal_parameters` 保持 null / 不可用展示
- `synthetic_only` / `pending_h1_calibration` 明确可见
- 重放默认 `persist=false`；持久化需显式勾选并填写 `run_id`
- 临时重放与已提交 Run 视觉区分
- 无报告生成 UI；无 oracle 暴露

## 验证

```powershell
cd web
npm ci
npm test -- --run
npm run build

python scripts/generate_m1_p3d_acceptance.py
```

依赖新增（前端测试）：

- vitest
- @testing-library/react / jest-dom / user-event
- jsdom

用途：组件与 API 契约行为的确定性回归；不引入 E2E/图表库。
