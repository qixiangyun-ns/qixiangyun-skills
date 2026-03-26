# 登录 API 说明

本 skill 通过企享云开放平台 API 完成登录前置，不依赖 MCP。

## 核心接口映射

- 第 1 步 自然人创建账号：`POST /v2/public/account/create`
- 第 2 步-1 发送自然人登录验证码：`POST /v2/public/login/remote/etaxcookie`
- 第 2 步-2 上传自然人登录验证码：`POST /v2/public/login/remote/pushsms`
- 第 3 步 获取企业列表：`POST /v2/public/login/queryOrglist`
- 第 5 步 企业服务订购：`POST /v2/public/org/productPurchase`
- 第 6 步 多账号创建：`POST /v2/public/account/create`
- 第 7 步 企业账号登录就绪校验：
  - `POST /v2/public/login/remote/checkCache`
  - `POST /v2/public/login/remote/checkRomoteAppCache`

## 调用约束

- 密码必须通过 `scripts/crypto.py` 的 RSA 加密逻辑处理，禁止明文上送。
- 所有业务请求默认携带 OAuth2 Token，由 `scripts/auth.py` 统一管理。
- 第 1 步默认使用 `dlfs=15` 创建自然人账号。
- 第 6 步默认使用 `dlfs=9` 创建办税多账号。
- 第 7 步只做登录态可用性判断，不主动触发新的短信验证。

## 配置

- `QXY_API_KEY={client_appkey}.{client_secret}`
- `QXY_API_HOST=https://api.qixiangyun.com`
- `QXY_RSA_PUBLIC_KEY=...`

配置优先级：

1. 环境变量
2. 当前 skill 根目录 `.env`
3. skills 仓库根目录 `.env`
