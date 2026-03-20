# 缴款闭环配置说明

本文件对应 `scripts/payment_workflow.py` 的 JSON 配置结构。

## 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `aggOrgId` | string | 是 | 企业 ID |
| `year` | integer | 是 | 年份 |
| `period` | integer | 是 | 所属期 |
| `accountId` | string/null | 否 | 多账号场景可传 |
| `poll_interval_seconds` | integer | 否 | 查询类工具轮询间隔，默认 10 |
| `max_poll_attempts` | integer | 否 | 最大轮询次数，默认 12 |
| `steps` | object | 是 | 分步骤配置 |

## 步骤顺序

1. `payment`
2. `certificate`

## 关键步骤字段

### `payment`

- `enabled`
- `detail`
- `duration`
- `tdztswjgmc`
- `poll_result`

### `certificate`

- `enabled`
- `zsxmDtos`
- `tdztswjgmc`
- `poll_result`
