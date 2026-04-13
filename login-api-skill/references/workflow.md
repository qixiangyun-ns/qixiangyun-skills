# 7 步登录流程

## 流程

1. 自然人创建账号
2. 自然人登录
3. 获取自然人可操作企业列表
4. 选择目标企业
5. 企业服务订购
6. 创建企业多账号
7. 企业短信登录

说明：

- 第 2 步自然人登录在代码里拆成两个动作：发送验证码和上传验证码。
- 第 7 步企业短信登录在代码里也拆成两个动作：发送验证码和上传验证码。
- 第 7 步验码成功后会写共享登录态，供后续申报与缴款 skill 复用。
- `login-enterprise-account` 仅作为兼容能力保留，用于检查是否已有可复用缓存。

## 关键方法

- `TaxLoginWorkflow.create_natural_person_account`
- `TaxLoginWorkflow.start_natural_person_login`
- `TaxLoginWorkflow.verify_natural_person_login`
- `TaxLoginWorkflow.list_enterprises`
- `TaxLoginWorkflow.choose_target_enterprise`
- `TaxLoginWorkflow.subscribe_enterprise_service`
- `TaxLoginWorkflow.create_multi_account`
- `TaxLoginWorkflow.start_enterprise_login`
- `TaxLoginWorkflow.verify_enterprise_login`
- `TaxLoginWorkflow.login_enterprise_account`

## 典型输出

- 自然人开户：`account_id`、`login_mode=17`
- 自然人验证码发送：`task_id`
- 企业列表：`account_id`、`enterprises[]`
- 企业订购：`org_id`、`agg_org_id`
- 多账号创建：`account_id`、`login_mode=14`
- 企业验证码发送：`task_id`
- 企业验码成功：`login_success`、`state_file`
