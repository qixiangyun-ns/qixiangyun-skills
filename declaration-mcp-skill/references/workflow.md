# 申报闭环配置说明

本文件对应 `scripts/declaration_workflow.py` 的 JSON 配置结构。

## 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `aggOrgId` | string | 是 | 企业 ID |
| `year` | integer | 是 | 年份 |
| `period` | integer | 是 | 申报月份 |
| `accountId` | string/null | 否 | 多账号场景可传 |
| `poll_interval_seconds` | integer | 否 | 兼容旧配置的默认轮询间隔 |
| `max_poll_attempts` | integer | 否 | 兼容旧配置的最大轮询次数 |
| `poll_strategy` | object | 否 | 新版轮询策略 |
| `checkpoint` | object | 否 | checkpoint 配置 |
| `rules` | object | 否 | 规则引擎配置 |
| `manual_review` | object | 否 | 人工复核控制项 |
| `post_actions` | object | 否 | 申报成功后的自动动作 |
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

## 新增高级配置

### `poll_strategy`

| 字段 | 类型 | 说明 |
|---|---|---|
| `short_interval_seconds` | integer | 短轮询间隔秒数 |
| `short_max_attempts` | integer | 短轮询最大次数 |
| `long_backoff_minutes` | integer[] | 长退避分钟序列，默认 `[30,60,120,240,300]` |

### `checkpoint`

| 字段 | 类型 | 说明 |
|---|---|---|
| `enabled` | boolean | 是否写入 checkpoint |
| `path` | string/null | 自定义 checkpoint 文件路径 |
| `resume_mode` | string | 默认 `from_pending`；恢复时跳过已终态步骤，仅继续 pending/timeout 或未执行步骤；如需重跑失败步骤可设为 `rerun_failed` |

### `rules`

| 字段 | 类型 | 说明 |
|---|---|---|
| `accrual_mode` | string | `validate_and_suggest` 或 `auto_patch_payload` |
| `response_rule_set` | string | 当前默认 `default` |
| `tax_burden_enabled` | boolean | 是否启用行业税负率校验 |
| `industry_code` | string/null | 预留字段 |
| `industry_name` | string/null | 行业名称，命中规则库时用于税负率比对 |
| `tax_burden_blocking` | boolean | 超阈值时是否阻断 |
| `allow_force_declare_on_4300` | boolean | 是否允许在 `4300` 场景继续强制申报 |

### `manual_review`

| 字段 | 类型 | 说明 |
|---|---|---|
| `enabled` | boolean | 是否启用人工复核状态 |
| `emit_customer_message` | boolean | 人工复核场景是否输出客户话术 |

### `post_actions`

| 字段 | 类型 | 说明 |
|---|---|---|
| `auto_download_pdf` | boolean | 预留给后续自动触发 PDF |
| `auto_missing_check` | boolean | 预留给后续自动触发漏报检查 |
| `auto_prepare_payment` | boolean | 预留给后续衔接缴款流程 |

## 关键步骤字段

### `fetch_roster`

- `enabled`
- `poll_result`

### `init_data`

- `enabled`
- `zsxmList`
- `query_after_start`
- `query_items`

#### `init_data.zsxmList[]` 补充说明

- `yzpzzlDm` 必填
- `ssqQ`、`ssqZ` 如已显式提供，会直接作为税款所属期起止使用
- 未提供 `ssqQ`、`ssqZ` 时，可传本地字段 `period_cycle`，支持 `monthly`、`quarterly`、`annual`
- `period_cycle` 只用于本地换算所属期，不会透传到 MCP
- 若税种目录标记为 `monthly_or_quarterly`，则必须显式指定 `period_cycle` 或直接传 `ssqQ`、`ssqZ`
- 若税种目录标记为固定周期（`monthly`、`quarterly`、`annual`），本地配置若与目录冲突会直接报错
- `init_data` 查询阶段会对每个 `yzpzzlDm` 复用统一短轮询策略；若返回“初始化任务还在执行中，请稍后获取”会识别为 `pending`

### `tax_report`

- `enabled`
- `tax_data`
- `tax_type`
- `isDirectDeclare`
- `allowRepeatDeclare`
- `jrwc`
- `poll_result`
- `tax_label`

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
- `tax_label`
- 若 `cbData` 与 `cbnbData` 都为空，步骤会输出 `skipped`，并在日志中说明跳过原因

### `current_pdf`

- `enabled`
- `zsxmList`
- `analysisPdf`
- `poll_result`

#### `current_pdf.zsxmList[]` 补充说明

- 兼容字符串数组：`["BDA0610606", "BDA0611159"]`
- 也兼容对象数组：`[{"yzpzzlDm":"BDA0610606"}]`
- 本地会统一归一化为对象数组后再调用 MCP

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

## CLI 用法

### 全流程执行

```bash
python3 scripts/declaration_workflow.py run --config /tmp/declaration-config.json
```

### 指定步骤执行

```bash
python3 scripts/declaration_workflow.py run \
  --config /tmp/declaration-config.json \
  --steps fetch_roster,init_data,tax_report
```

### 从 checkpoint 恢复

```bash
python3 scripts/declaration_workflow.py resume \
  --checkpoint /tmp/declaration-checkpoint.json
```

### 单步补偿执行

```bash
python3 scripts/declaration_workflow.py run-step \
  --config /tmp/declaration-config.json \
  --step tax_report \
  --phase run
```

### 仅查询已存在任务

```bash
python3 scripts/declaration_workflow.py query-step \
  --checkpoint /tmp/declaration-checkpoint.json \
  --step tax_report
```
