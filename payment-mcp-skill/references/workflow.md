# 缴款闭环配置说明

本文件对应 `scripts/payment_workflow.py` 的 JSON 配置结构。

## 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `aggOrgId` | string | 是 | 企业 ID |
| `year` | integer | 是 | 年份 |
| `period` | integer | 是 | 申报月份 |
| `accountId` | string/null | 否 | 多账号场景可传 |
| `poll_interval_seconds` | integer | 否 | 查询类工具轮询间隔，默认 10 |
| `max_poll_attempts` | integer | 否 | 最大轮询次数，默认 12 |
| `steps` | object | 是 | 分步骤配置 |

说明：

- 顶层 `period` 表示申报月份
- `payment.detail[].fromDate/toDate` 与 `certificate.zsxmDtos[].ssqQ/ssqZ` 表示实际税款所属期起止
- 脚手架默认会按申报月份自动回推出上一个自然月的所属期示例

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

#### `payment.detail[]` 字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `yzpzzlDm` | string | 是 | 应征凭证种类代码 |
| `fromDate` | string | 是 | 所属期起，格式 `YYYY-MM-DD` |
| `toDate` | string | 是 | 所属期止，格式 `YYYY-MM-DD` |
| `taxAmount` | number | 是 | 本次申请缴款金额，必须大于 0 |
| `jkfs` | string/null | 否 | 缴款方式 |
| `agreementAccount` | string/null | 否 | 三方协议号 |
| `yhzh` | string/null | 否 | 银行账号 |
| `zspmDm` | string/null | 否 | 征收品目代码 |
| `zsxmDm` | string/null | 否 | 征收项目代码 |
| `bsswjg` | string/null | 否 | 办税税务机关 |
| `kqyswjgmc` | string/null | 否 | 跨区域税务机关名称 |
| `sebyz` | string/null | 否 | 税额不一致是否允许缴款 |

### `certificate`

- `enabled`
- `zsxmDtos`
- `tdztswjgmc`
- `poll_result`

#### `certificate.zsxmDtos[]` 字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `ssqQ` | string | 是 | 所属期起，格式 `YYYY-MM-DD` |
| `ssqZ` | string | 是 | 所属期止，格式 `YYYY-MM-DD` |
| `yzpzzlDm` | string | 是 | 应征凭证种类代码 |
| `zspmDm` | string/null | 否 | 征收品目代码 |

## 内置校验

`scripts/payment_workflow.py` 会在本地先做一轮参数校验，减少无效请求：

- `payment.detail` 不能为空
- `payment.detail[].fromDate` 不能晚于 `toDate`
- `payment.detail[].taxAmount` 必须大于 0
- `certificate.zsxmDtos` 不能为空且最多 20 条
- `certificate.zsxmDtos[].ssqQ` 不能晚于 `ssqZ`
- `certificate.zsxmDtos` 的最早所属期起与最晚所属期止不可跨自然年
- `certificate.zsxmDtos` 中相同的 `yzpzzlDm + ssqQ + ssqZ (+ zspmDm)` 不允许重复

## 输出结构

每个步骤的输出结构与 `declaration-mcp-skill` 保持一致：

- `start`：发起 tool 的原始返回
- `taskId`：从返回中提取的任务 ID
- `query`：轮询查询结果
- `skipped`：步骤未启用时返回
