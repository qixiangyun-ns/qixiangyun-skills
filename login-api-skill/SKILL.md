---
name: login-api-skill
description: 通过企享云开放平台 API 处理税务登录相关任务，包括自然人账号创建、自然人登录、企业列表获取、企业订购、多账号创建和企业账号登录就绪校验。使用 bundled Python scripts 严格通过 API 调用，适用于需要按 7 步登录流程编排税务登录能力、避免 curl 或零散 OpenAPI 调用的场景。
---

# 登录 API Skill

基于企享云开放平台 API 实现税务登录，不依赖 MCP、不走浏览器自动化。

## 何时激活

- 用户需要按“自然人创建账号 -> 自然人登录 -> 企业列表 -> 企业订购 -> 多账号创建 -> 企业账号登录”流程办税
- 用户要用 API 而不是网页手工操作完成税务登录前置
- 用户提到“登录 skill”“自然人账号”“多账号创建”“企业账号登录”

## 严格规则

1. 所有调用都只能通过 bundled scripts：
   - `scripts/client.py`
   - `scripts/workflow.py`
2. 不要直接拼 `curl` 或手写签名逻辑
3. 不要把 `client_appkey`、`client_secret` 硬编码到代码里
4. 密码一律走内置 RSA 加密，不要明文透传到日志

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

```python
from scripts import TaxLoginClient, TaxLoginWorkflow

client = TaxLoginClient.from_config()
workflow = TaxLoginWorkflow(client)

natural = workflow.create_natural_person_account(
    area_code="3300",
    phone="13800138000",
    password="your_password",
)

sms_task = workflow.start_natural_person_login(
    agg_org_id=natural["agg_org_id"],
    account_id=natural["account_id"],
)

verify = workflow.verify_natural_person_login(
    task_id=sms_task["task_id"],
    sms_code="123456",
)
```

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

## 参考文件

- 接口映射与调用约束：[references/api-notes.md](references/api-notes.md)
- 7 步流程与输出说明：[references/workflow.md](references/workflow.md)
