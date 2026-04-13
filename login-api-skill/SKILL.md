---
name: login-api-skill
description: 使用单入口脚本完成企享云 7 步登录链路。默认且唯一推荐入口是 `run-full-login`；代理模型不得自行拆分自然人登录、企业列表、订购、多账号、企业登录等子步骤。
---

# 登录 API Skill

该 skill 的目标只有一个：完成整条登录链路，并把最终企业登录态写入 skills 根目录的 `.qxy_login_state.json`。

## 强制规则

1. 对于“帮我登录”“帮我进行登录”“用这个账号登录”这类请求，只允许执行 `python3 scripts/login_workflow.py run-full-login ...`。
2. 代理模型不得自行拆分 7 步，不得先调用 `start-natural-login`、`list-enterprises`、`subscribe-enterprise-service`、`create-multi-account` 等分步命令来拼流程。
3. 只有 `final_success=true` 才表示整条链路完成。
4. 如果返回 `waiting_for_user_input=true`，代理只能向用户索取验证码，然后再次执行同一个 `run-full-login` 命令续跑。
5. 在自然人链路中，不得使用、推断、解释或输出任何 `orgId/aggOrgId` 字段；自然人阶段唯一合法关键标识是 `accountId`。
6. 只有进入企业链路后，才允许出现 `aggOrgId/orgId`。

## 唯一推荐入口

```bash
export QXY_LOGIN_PASSWORD='your_password'
python3 scripts/login_workflow.py run-full-login \
  --area-code 3100 \
  --phone 13800138000 \
  --password-env QXY_LOGIN_PASSWORD
```

可选参数：
- `--enterprise-phone`
- `--enterprise-password`
- `--enterprise-password-env`
- `--enterprise-username`
- `--identity-type BSY`
- `--nsrsbh`
- `--org-name`
- `--index`
- `--natural-sms-code`
- `--enterprise-sms-code`

## 执行语义

1. 该命令会自动执行：
   - 自然人创建账号
   - 自然人登录
   - 获取企业列表
   - 选择企业
   - 企业服务订购
   - 创建企业多账号
   - 企业登录
2. 只有在必须等待用户提供短信验证码时才允许暂停。
3. 如果自然人验证码未提供，返回：
   - `success=false`
   - `final_success=false`
   - `waiting_for_user_input=true`
   - `user_input_kind=natural_sms_code`
4. 如果企业验证码未提供，返回：
   - `success=false`
   - `final_success=false`
   - `waiting_for_user_input=true`
   - `user_input_kind=enterprise_sms_code`
5. 代理模型看到中间成功时，不得口头宣布“已登录成功”；必须继续执行直到 `final_success=true`。

## 关键约束

- 自然人开户使用 `/v2/public/account/create`，`dlfs=17`
- 自然人登录只使用 `accountId`
- 自然人企业列表只使用 `accountId`
- 企业订购后才获取企业 `aggOrgId/orgId`
- 企业多账号使用 `/v2/public/account/create`，`dlfs=14`
- 企业登录才使用 `aggOrgId + accountId`

## 输出

最终成功后写入：
- `.qxy_login_state.json`
- `.qxy_login_flow_state.json`

查看：
```bash
python3 scripts/login_workflow.py show-login-state
python3 scripts/login_workflow.py show-flow-state
```

清理：
```bash
python3 scripts/login_workflow.py clear-login-state
python3 scripts/login_workflow.py clear-flow-state
```

## 调试说明

分步命令仍保留在脚本中，仅供人工调试，不是代理默认入口。代理模型阅读本 skill 时，应忽略这些分步命令，优先且默认只使用 `run-full-login`。

默认情况下，这些分步命令不会出现在 CLI 里。只有显式设置 `QXY_LOGIN_ENABLE_DEBUG_COMMANDS=1` 时，才会开放人工调试入口。
