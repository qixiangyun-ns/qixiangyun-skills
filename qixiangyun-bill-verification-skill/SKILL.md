---
name: qixiangyun-bill-verification-skill
description: 企享云票据查验和发票验真 - 通过发票四要素进行验真，返回全票面数据信息及当日最新发票状态。支持数电票查验、增值税发票四要素查验、全电纸质发票查验、财政票据查验。
---

# 企享云票据查验和发票验真 Skill

通过发票四要素进行验真，返回全票面数据信息及当日最新发票状态。与国家税务总局全国增值税查验平台保持一致。

## 支持的查验类型

- 数电票查验（电子发票普通/专票、铁路客票、航空客票、机动车销售发票、二手车统一发票、通行费发票）
- 增值税发票四要素查验（增值税专用/普通发票、机动车销售统一发票等）
- 全电纸质发票查验
- 财政票据查验
- 批量查验（每次最多50张）

## 配置

通过环境变量或 `.env` 文件配置 API 密钥：

```bash
export QXY_CLIENT_APPKEY="your_appkey"
export QXY_CLIENT_SECRET="your_secret"
```

或在 skill 根目录创建 `.env` 文件：

```
QXY_CLIENT_APPKEY=your_appkey
QXY_CLIENT_SECRET=your_secret
```

API 密钥申请：https://open.qixiangyun.com

## 强制规则

1. 代理模型在执行查验前，应先调用 `validate-invoice-info` 验证发票信息格式是否正确。
2. 查验结果中 `code=2000` 表示查验成功，`code!=2000` 表示失败。
3. 批量查验时，每次最多支持50张发票。
4. 代理模型不得自行编造发票四要素信息。

## 可用命令

### 1. validate-invoice-info - 验证发票信息格式

在正式查验前进行预检查，验证发票信息的格式是否正确。

```bash
python3 scripts/cli.py validate-invoice-info \
  --invoice-type-code '发票代码' \
  --invoice-number '发票号码' \
  --billing-date '开票日期' \
  --amount '金额' \
  --check-code '校验码'
```

### 2. verify-tax-control - 税控发票查验

支持增值税专用/普通发票、机动车销售统一发票等类型。cyList 支持多张。

```bash
python3 scripts/cli.py verify-tax-control \
  --cy-list '[{"fpdm":"发票代码","fphm":"发票号码","kprq":"开票日期","je":"金额","fj":"校验码后6位"}]'
```

### 3. verify-digital - 数电票查验

数电票查验（全电发票），cyList 支持多张。

```bash
python3 scripts/cli.py verify-digital \
  --cy-list '[{"fphm":"发票号码","kprq":"开票日期","je":"金额","fj":"校验码后6位"}]'
```

### 4. verify-digital-paper - 数电纸质发票查验

数电纸质发票查验（全电纸质），cyList 支持多张。

```bash
python3 scripts/cli.py verify-digital-paper \
  --cy-list '[{"fpdm":"发票代码","fphm":"发票号码","kprq":"开票日期","je":"金额","fj":"校验码后6位"}]'
```

### 5. verify-invoice - 普通发票查验

税控发票查验，支持增值税专用发票、普通发票等类型。

```bash
python3 scripts/cli.py verify-invoice \
  --invoice-type-code '发票代码' \
  --invoice-number '发票号码' \
  --billing-date '开票日期' \
  --amount '金额' \
  --check-code '校验码'
```

### 6. batch-verify - 批量查验

批量查验税控发票，提高查验效率，每次最多50张。

```bash
python3 scripts/cli.py batch-verify \
  --cy-list '[{"fpdm":"发票代码","fphm":"发票号码","kprq":"开票日期","je":"金额","fj":"校验码后6位"}]'
```

## cyList 参数说明

| 字段 | 说明 | 示例 |
|------|------|------|
| `fpdm` | 发票代码 | "3200222130" |
| `fphm` | 发票号码 | "12345678" |
| `kprq` | 开票日期 | "2024-01-15" |
| `je` | 金额 | "100.00" |
| `fj` | 校验码后6位 | "123456" |

## MCP 调用说明

本 skill 通过 MCP HTTP Streamable 协议调用企享云发票查验服务，端点地址：

`https://mcp.qixiangyun.com/mcp/invoice_verification-http`

使用的 MCP 工具：
- `verify_tax_control_invoice` - 税控发票查验
- `verify_digital_invoice` - 数电票查验
- `verify_digital_paper_invoice` - 数电纸质发票查验
- `verify_invoice` - 普通发票查验
- `batch_verify_invoices` - 批量查验
- `validate_invoice_info` - 验证发票信息格式

每次调用需携带 `client_appkey` 和 `client_secret` 参数进行身份验证。
