# skills-qixiangyun

企享云 MCP 服务 Skills 集合，提供企业税务服务能力的 AI Skill 封装。

## 包含的 Skills

| Skill 目录 | 说明 |
|---|---|
| [login-api-skill](./login-api-skill/) | 企享云 7 步企业登录链路（自然人开户→登录→企业列表→订购→多账号→企业登录） |
| [declaration-mcp-skill](./declaration-mcp-skill/) | 税务申报闭环（获取清册、初始化、申报、PDF 下载、申报信息查询、漏报检查） |
| [payment-mcp-skill](./payment-mcp-skill/) | 税款缴纳闭环（发起缴款、查询结果、获取完税证明） |
| [qixiangyun-bill-verification-skill](./qixiangyun-bill-verification-skill/) | 票据查验和发票验真（数电票、税控发票、财政票据，支持批量） |
| [qixiangyun-enterprise-invoice-info-skill](./qixiangyun-enterprise-invoice-info-skill/) | 企业开票信息查询（企业名称、纳税人识别号、企业法人、开户行等） |
| [qixiangyun-enterprise-risk-skill](./qixiangyun-enterprise-risk-skill/) | 企业风控和经营异常查询（经营异常、严重违法、重大税收违法、非正常户） |

## 前提条件

### 1. 获取 API 凭证

前往 [企享云开放平台](https://open.qixiangyun.com) 申请 `client_appkey` 和 `client_secret`。

### 2. 配置凭证

**推荐方式**：在项目根目录创建 `.env` 文件（各 Skill 会自动读取）：

```env
QXY_CLIENT_APPKEY=your_appkey_here
QXY_CLIENT_SECRET=your_secret_here
```

也可以直接设置系统环境变量：

```bash
export QXY_CLIENT_APPKEY="your_appkey_here"
export QXY_CLIENT_SECRET="your_secret_here"
```

**凭证读取优先级**（由高到低）：
1. 系统环境变量
2. Skill 根目录的 `.env`
3. 项目公共父目录的 `.env`

> **首次调用时**，如果凭证缺失，AI 模型会自动引导你完成创建 `.env` 文件的步骤。

## 使用方式

### 登录

```bash
export QXY_LOGIN_PASSWORD='your_password'
python3 login-api-skill/scripts/login_workflow.py run-full-login \
  --area-code 3100 \
  --phone 13800138000 \
  --password-env QXY_LOGIN_PASSWORD
```

### 税务申报（需先完成登录）

```bash
# 生成申报配置模板
python3 declaration-mcp-skill/scripts/declaration_workflow.py \
  scaffold-config --year 2026 --period 4 --output /tmp/declaration-config.json

# 执行申报闭环
python3 declaration-mcp-skill/scripts/declaration_workflow.py \
  run --config /tmp/declaration-config.json
```

### 发票查验

```bash
# 数电票查验
python3 qixiangyun-bill-verification-skill/scripts/cli.py verify-digital \
  --cy-list '[{"fphm":"发票号码","kprq":"2026-03-01","je":"100.00","fj":"123456"}]'
```

### 企业开票信息查询

```bash
python3 qixiangyun-enterprise-invoice-info-skill/scripts/cli.py \
  query --enterprise-name '企业名称'
```

### 企业风控查询

```bash
python3 qixiangyun-enterprise-risk-skill/scripts/cli.py \
  query --enterprise-name '某某科技有限公司'
```

## 添加新 Skill

在项目根目录新建目录，按以下结构组织：

```
my-new-skill/
├── SKILL.md              # 主指令（必需）
├── .env.example          # 凭证模板（可选）
├── requirements.txt      # 依赖列表（可选）
├── references/           # 参考文档
└── scripts/
    └── cli.py            # 调用脚本
```

## 技术信息

| 项目 | 说明 |
|---|---|
| MCP 协议 | Streamable HTTP (SSE) |
| 服务端点 | `https://mcp.qixiangyun.com/mcp/` |
| 认证方式 | `client_appkey` + `client_secret`（secret 签名后传输） |
| Python 要求 | 3.11+ |

## 隐私与安全

- **凭证文件**：`.env` 已加入 `.gitignore`，**请勿手动提交到版本控制**。
- **运行时状态**：登录态文件（`.qxy_login_state.json`）和 Checkpoint 文件（`.checkpoints/`、`.enterprise-checkpoints/`）均已加入 `.gitignore`，因为其中可能包含企业 ID、账号 ID 等业务数据。
- **日志隔离**：脚本不会在日志或标准输出中打印 API Key 原文。
- **代码示例**：文档中的 `aggOrgId`、`phone` 等均为示例占位值，不包含真实业务数据。
