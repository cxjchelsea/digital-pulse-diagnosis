# D3-F-A 会话报告与重放 API 设计

## 范围

本子工作包把 D3-E 故障矩阵转为可持久化、可查询、可校验和可重放的规范化报告。Web 状态时间线属于 D3-F-B；两个子包都通过前不标记 D3-F 完成。

## 存储结构

```text
sessions/d3-experiments/<report_sha256>/
├── request.json
├── events.jsonl
└── report.json
```

报告 ID 是不包含 `report_sha256` 字段的规范化 JSON SHA-256。读取时重新计算校验和，防止篡改后的报告继续参与重放。报告 ID 必须是 64 位小写十六进制，禁止路径穿越。

## 报告内容

报告包含 Schema 版本、seed、故障 case ID、14 类矩阵结果、安全事件时间线、检测延迟、动作、最终状态、汇总和证据限制。固定声明：

- 单位为合成相对单位；
- `medical_use=false`；
- `analysis_allowed=false`；
- 不证明真实执行器、传感器、人体组织、释放时间或人体安全。

## API

- `POST /api/experiments/d3/run`：运行全部或指定故障 case 并保存报告；
- `GET /api/experiments/d3/{sha256}`：查询且校验报告；
- `GET /api/experiments/d3/{sha256}/events`：查询安全事件时间线；
- `POST /api/experiments/d3/{sha256}/replay`：按原 case 顺序和 seed 重放并比较哈希。

非法报告 ID 返回 400，不存在返回 404，未知或重复 case 返回 422。

## 验收

```bash
python -m pytest tests/test_d3_experiment.py tests/test_d3_api.py -q
python -m pytest -q
```

专项测试覆盖规范化哈希、顺序敏感性、存储文件、篡改检测、路径穿越、查询、事件和重放。Web 生产构建将在 D3-F-B 验收。
