# 企业级申报编排配置说明

本文件对应 `scripts/enterprise_filing_workflow.py` 的 JSON 配置结构。

## 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `poll_interval_seconds` | integer | 否 | 轮询间隔秒数，默认 10 |
| `max_poll_attempts` | integer | 否 | 最大轮询次数，默认 30 |
| `checkpoint` | object | 否 | checkpoint 配置 |
| `enterprises` | array | 是 | 企业列表，批量串行执行 |

### `checkpoint`

| 字段 | 类型 | 说明 |
|---|---|---|
| `enabled` | boolean | 是否写入 checkpoint |
| `path` | string/null | 自定义批量 checkpoint 路径 |

## `enterprises[]`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `aggOrgId` | string | 是 | 企业 ID |
| `accountId` | string/null | 否 | 不传时默认复用共享登录态中的 `accountId` |
| `display_name` | string/null | 否 | 展示名称 |
| `year` | integer | 是 | 申报年份 |
| `period` | integer | 是 | 申报月份 |
| `financial_report_input` | object/null | 否 | 财报输入 |
| `vat_adjustment` | object/null | 否 | 增值税调整输入 |

## `financial_report_input`

### Excel 模式

```json
{
  "mode": "excel",
  "file_path": "/tmp/example.xlsx",
  "yzpzzlDm": "CWBBSB",
  "ssqQ": "2026-01-01",
  "ssqZ": "2026-03-31",
  "zlbsxlDm": "ZL1001003",
  "templateCode": "0",
  "isDirectDeclare": true
}
```

规则：

- `yzpzzlDm` 仅支持 `CWBBSB`、`CWBBNDSB`、`CWBBJTHB`
- `zlbsxlDm` 当前仅支持 `ZL1001001`、`ZL1001002`、`ZL1001003`、`ZL1001050`
- `ZL1001001` 时 `templateCode` 必须为 `1` 或 `2`
- 其他当前支持准则默认传 `0`

### JSON 模式

```json
{
  "mode": "json",
  "cbData": {},
  "cbnbData": null,
  "isDirectDeclare": true
}
```

规则：

- `cbData` / `cbnbData` 至少传一个
- 当前不做任意文件自动解析为 `cbData/cbnbData`

## `vat_adjustment`

```json
{
  "no_ticket_income_amount": 0
}
```

规则：

- 不传时默认按 `0` 处理
- 大于 `0` 时，V1 直接进入 `manual_review_required`

## 输出结构

批量结果输出包含：

- `summary`
- `enterprises[]`
- `successful_declarations`
- `pdf_download_requests`
- `payment_preparation`

单企业结果常见字段：

- `status`
- `roster`
- `financial_report`
- `income_tax`
- `vat`
- `declare_info`
- `pdfs`
- `payment_preparation`
- `operator_advice`

## CLI 用法

生成样例配置：

```bash
python3 scripts/enterprise_filing_workflow.py scaffold-config \
  --year 2026 \
  --period 4 \
  --output /tmp/enterprise-filing-config.json
```

执行批量企业申报编排：

```bash
python3 scripts/enterprise_filing_workflow.py run \
  --config /tmp/enterprise-filing-config.json
```

从 checkpoint 恢复：

```bash
python3 scripts/enterprise_filing_workflow.py resume \
  --checkpoint /tmp/enterprise-filing-checkpoint.json
```
