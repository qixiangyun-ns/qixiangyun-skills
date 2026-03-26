---
name: login-api-skill
description: 通过企享云开放平台 API 处理税务登录相关任务，包括自然人账号创建、自然人登录、企业列表获取、企业订购、多账号创建和企业账号登录就绪校验。使用 bundled Python scripts 严格通过 API 调用，适用于需要按 7 步登录流程编排税务登录能力、避免 curl 或零散 OpenAPI 调用的场景。
---

# 登录 API Skill

基于企享云开放平台 API 实现税务登录，不依赖 MCP、不走浏览器自动化。

该 skill 是 `declaration-mcp-skill` 和 `payment-mcp-skill` 的前置依赖。
完成企业账号登录就绪校验后，会自动在 skills 仓库根目录写入共享登录态 `.qxy_login_state.json`，
供申报和缴款 skill 复用。

## 何时激活

- 用户需要按“自然人创建账号 -> 自然人登录 -> 企业列表 -> 企业订购 -> 多账号创建 -> 企业账号登录”流程办税
- 用户要用 API 而不是网页手工操作完成税务登录前置
- 用户提到“登录 skill”“自然人账号”“多账号创建”“企业账号登录”

## 严格规则

1. 所有调用都只能通过 bundled scripts：
   - `scripts/login_workflow.py`
   - `scripts/client.py`
   - `scripts/workflow.py`
2. 禁止使用 `python -c` 动态拼接导入命令，统一走 `scripts/login_workflow.py`
3. 不要直接拼 `curl` 或手写签名逻辑
4. 不要把 `client_appkey`、`client_secret` 硬编码到代码里
5. 密码一律走内置 RSA 加密，不要明文透传到日志

## 核心工作流

### 第1步：自然人创建账号

输入：

- 地区
- 手机号
- 密码

输出：

- `account_id`
- `agg_org_id`

对应方法：

- `TaxLoginWorkflow.create_natural_person_account`

### 第2步：自然人登录

拆为两个 API 动作：

1. `TaxLoginWorkflow.start_natural_person_login` 发送验证码
2. `TaxLoginWorkflow.verify_natural_person_login` 上传验证码

输出：

- `login_success`

### 第3步：获取企业列表

输入：

- 自然人 `agg_org_id`
- 自然人 `account_id`

输出：

- 企业名称
- 企业税号
- 身份类型

对应方法：

- `TaxLoginWorkflow.list_enterprises`

### 第4步：选择目标企业

输入：

- 企业列表
- 目标企业名称或税号

输出：

- 单个目标企业对象

对应方法：

- `TaxLoginWorkflow.choose_target_enterprise`

### 第5步：企业服务订购

输入：

- 地区
- 企业名称
- 企业税号

输出：

- `agg_org_id`

对应方法：

- `TaxLoginWorkflow.subscribe_enterprise_service`

### 第6步：多账号创建

输入：

- 企业 `agg_org_id`
- 地区
- 登录信息

输出：

- 多账号 `account_id`

对应方法：

- `TaxLoginWorkflow.create_multi_account`

### 第7步：企业账号登录就绪校验

输入：

- 企业 `agg_org_id`
- 多账号 `account_id`

输出：

- 是否可直接开展办税业务

对应方法：

- `TaxLoginWorkflow.login_enterprise_account`

## 推荐调用方式

优先使用 CLI，而不是临时拼 Python 导入：

```bash
python3 scripts/login_workflow.py create-natural-account \
  --area-code 3100 \
  --phone 13800138000 \
  --password 'your_password'
```

发送验证码：

```bash
python3 scripts/login_workflow.py start-natural-login \
  --agg-org-id 自然人aggOrgId \
  --account-id 自然人accountId
```

上传验证码：

```bash
python3 scripts/login_workflow.py verify-natural-login \
  --task-id 验证码任务ID \
  --sms-code 123456
```

获取企业列表：

```bash
python3 scripts/login_workflow.py list-enterprises \
  --natural-agg-org-id 自然人aggOrgId \
  --natural-account-id 自然人accountId
```

订购企业并创建多账号后，执行企业登录就绪校验：

```bash
python3 scripts/login_workflow.py login-enterprise-account \
  --agg-org-id 企业aggOrgId \
  --account-id 企业多账号accountId
```

查看当前共享登录态：

```bash
python3 scripts/login_workflow.py show-login-state
```

如果确实需要在 Python 中直接调用，再使用模块方式：

```python
from scripts import TaxLoginClient, TaxLoginWorkflow
```

完成第 7 步 `TaxLoginWorkflow.login_enterprise_account` 后：

- 若返回 `ready=True`
- 会自动写入共享登录态文件
- 后续申报和缴款 workflow 会自动读取该登录态并复用 `accountId`

## 配置

优先级：

1. 代码显式传入
2. 环境变量
3. Skill 根目录内 `.env`

配置项：

- `QXY_API_KEY={client_appkey}.{client_secret}`
- `QXY_API_HOST=https://api.qixiangyun.com`
- `QXY_RSA_PUBLIC_KEY=...`

## 说明

- 第1步默认按代理业务登录 `dlfs=15` 创建自然人账号
- 第6步默认按企业业务登录 `dlfs=9` 创建多账号
- 第7步不会主动再次发验证码，只做“缓存有效 / 可快速登录”校验
- 第7步成功后会自动写入共享登录态，供申报和缴款 skill 联动

## 参考文件

- 接口映射与调用约束：[references/api-notes.md](references/api-notes.md)
- 7 步流程与输出说明：[references/workflow.md](references/workflow.md)
