# 申报闭环配置说明

本文件对应 `scripts/declaration_workflow.py` 的 JSON 配置结构。

## 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `aggOrgId` | string | 是 | 企业 ID |
| `year` | integer | 是 | 年份 |
| `period` | integer | 是 | 所属期 |
| `accountId` | string/null | 否 | 多账号场景可传 |
| `poll_interval_seconds` | integer | 否 | 查询类工具轮询间隔，默认 10 |
| `max_poll_attempts` | integer | 否 | 最大轮询次数，默认 30 |
| `steps` | object | 是 | 分步骤配置 |

## 步骤顺序

1. `fetch_roster`
2. `init_data`
3. `tax_report`
4. `financial_report`
5. `current_pdf`
6. `history_pdf`
7. `declare_info`
8. `missing_check`

## 关键步骤字段

### `fetch_roster`

- `enabled`
- `poll_result`

### `init_data`

- `enabled`
- `zsxmList`
- `query_after_start`
- `query_items`

### `tax_report`

- `enabled`
- `tax_data`
- `tax_type`
- `isDirectDeclare`
- `allowRepeatDeclare`
- `jrwc`
- `poll_result`

### `financial_report`

- `enabled`
- `cbData`
- `cbnbData`
- `isDirectDeclare`
- `exAction`
- `duration`
- `jrwc`
- `gzDeclare`
- `poll_result`

### `current_pdf`

- `enabled`
- `zsxmList`
- `analysisPdf`
- `poll_result`

### `history_pdf`

- `enabled`
- `projectType`
- `skssqq`
- `skssqz`
- `yzpzzlDms`
- `analysisPdf`
- `poll_result`

### `declare_info`

- `enabled`
- `poll_result`

### `missing_check`

- `enabled`
- `poll_result`
