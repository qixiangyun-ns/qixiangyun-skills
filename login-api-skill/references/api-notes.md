# 登录 API 说明

本 skill 通过企享云开放平台 API 完成登录前置，不依赖 MCP。

## 核心接口映射

- 第 1 步 自然人创建账号：`POST /v2/public/account/create`
-   推荐参数：`dlfs=17`
- 第 2 步-1 发送自然人登录验证码：`POST /v2/public/login/remote/etaxcookie`
-   按当前业务流程，`dlfs=17` 时优先只传 `accountId`
- 第 2 步-2 上传自然人登录验证码：`POST /v2/public/login/remote/pushsms`
- 第 3 步 获取企业列表：`POST /v2/public/login/queryOrglist`
-   按当前业务流程，优先只传 `accountId`
- 第 5 步 企业服务订购：`POST /v2/public/org/productPurchase`
- 第 6 步 多账号创建：`POST /v2/public/account/create`
-   推荐参数：`dlfs=14`
- 第 7 步-1 发送企业登录验证码：`POST /v2/public/login/remote/etaxcookie`
- 第 7 步-2 上传企业登录验证码：`POST /v2/public/login/remote/pushsms`
- 兼容能力：企业账号登录就绪校验
  - `POST /v2/public/login/remote/checkCache`
  - `POST /v2/public/login/remote/checkRomoteAppCache`

## 调用约束

- 密码必须通过 `scripts/crypto.py` 的 RSA 加密逻辑处理，禁止明文上送。
- 所有业务请求默认携带 OAuth2 Token，由 `scripts/auth.py` 统一管理。
- 第 1 步默认使用 `dlfs=17` 创建自然人账号。
- 第 6 步默认使用 `dlfs=14` 创建企业多账号。
- 第 7 步主流程为企业短信登录，验码成功后写入共享登录态。
- 企业就绪校验命令仅作为兼容能力保留，不再作为主流程步骤。
- 托管企业列表接口返回字段以 `nsrmc / nsrsbh / sflx / glzt` 为主，skill 已统一映射为稳定输出结构。
- 你提供的 Apifox“账号创建”文档正文明确说明支持 `17=自然人登录`；虽然 schema 枚举仍只展示 `9/14/15`，当前 skill 以正文说明为准。

## 配置

- `QXY_API_KEY={client_appkey}.{client_secret}`
- `QXY_API_HOST=https://api.qixiangyun.com`
- `QXY_RSA_PUBLIC_KEY=...`

配置优先级：

1. 环境变量
2. 当前 skill 根目录 `.env`
3. skills 仓库根目录 `.env`
