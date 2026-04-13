---
name: qixiangyun-enterprise-risk-skill
description: 企享云企业风控和经营异常 - 根据企业名称查询企业经营异常、严重违法、重大税收违法风险、非正常户查询等信息，判断企业征信是否异常。
---

# 企享云企业风控和经营异常 Skill

根据企业名称或统一社会信用代码查询企业征信与风险信息。包括经营异常、严重违法、重大税收违法风险、非正常户等信息。

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

1. 查询参数需提供企业全名或统一社会信用代码。
2. 查询结果中 `code=2000` 表示查询成功。
3. 代理模型不得自行编造企业名称或信用代码。
4. 风控查询结果仅供参考，不构成最终法律意见。

## 可用命令

### query - 查询企业风控信息

```bash
# 通过企业名称查询
python3 scripts/cli.py query --enterprise-name '某某科技有限公司'

# 通过统一社会信用代码查询
python3 scripts/cli.py query --credit-code '91310000MA1FLXXX0X'

# 同时提供两个参数
python3 scripts/cli.py query --enterprise-name '某某科技有限公司' --credit-code '91310000MA1FLXXX0X'
```

参数说明（至少提供一个）：

| 参数 | 说明 | 示例 |
|------|------|------|
| `--enterprise-name` | 企业全名 | "某某科技有限公司" |
| `--credit-code` | 统一社会信用代码（18位） | "91310000MA1FLXXX0X" |

## 返回信息

- 经营异常信息（列入原因、列入日期、做出决定机关等）
- 严重违法信息（列入原因、列入日期等）
- 重大税收违法风险
- 非正常户状态
- 企业征信综合评估

## MCP 调用说明

本 skill 通过 MCP HTTP Streamable 协议调用企享云企业风控服务，端点地址：

`https://mcp.qixiangyun.com/mcp/enterprise_blacklist_status_enhanced-http`

使用的 MCP 工具：
- `query_enterprise_blacklist_status_enhanced_auto` - 企业风控和经营异常查询

每次调用需携带 `client_appkey` 和 `client_secret` 参数进行身份验证。
