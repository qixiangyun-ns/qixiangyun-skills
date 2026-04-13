---
name: qixiangyun-enterprise-invoice-info-skill
description: 企享云企业开票信息查询 - 根据企业简称或统一社会信用代码查询企业全名、纳税人识别号、企业法人、企业成立日期等信息，支持模糊查询企业列表。
---

# 企享云企业开票信息查询 Skill

根据企业简称或统一社会信用代码查询企业全名、纳税人识别号、企业法人、成立日期，支持模糊查询企业列表。还可查询云抬头、企业精准信息、企业联系人等。

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

1. 查询参数仅需提供企业简称或统一社会信用代码中的一个。
2. 查询结果中 `code=2000` 表示查询成功。
3. 代理模型不得自行编造企业信息。

## 可用命令

### query - 查询企业开票信息

```bash
# 通过企业简称查询（支持模糊查询）
python3 scripts/cli.py query --enterprise-name '企享云'

# 通过统一社会信用代码查询
python3 scripts/cli.py query --credit-code '91310000MA1FLXXX0X'

# 同时提供两个参数
python3 scripts/cli.py query --enterprise-name '企享云' --credit-code '91310000MA1FLXXX0X'
```

参数说明（至少提供一个）：

| 参数 | 说明 | 示例 |
|------|------|------|
| `--enterprise-name` | 企业简称或全称 | "企享云" |
| `--credit-code` | 统一社会信用代码（18位） | "91310000MA1FLXXX0X" |

## 返回信息

- 企业全名
- 纳税人识别号
- 企业法人
- 企业成立日期
- 企业列表（模糊查询时返回多个匹配结果）

## MCP 调用说明

本 skill 通过 MCP HTTP Streamable 协议调用企享云企业开票信息查询服务，端点地址：

`https://mcp.qixiangyun.com/mcp/invoice_title_information-http`

使用的 MCP 工具：
- `verify_invoice_title_information_auto` - 企业开票信息查询

每次调用需携带 `client_appkey` 和 `client_secret` 参数进行身份验证。
