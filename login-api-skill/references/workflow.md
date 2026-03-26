# 7 步登录流程

## 流程

1. 自然人创建账号
2. 自然人登录发送验证码
3. 自然人登录上传验证码
4. 获取自然人可操作企业列表
5. 选择目标企业
6. 企业服务订购
7. 创建企业多账号
8. 校验企业账号是否可直接开展办税业务

说明：

- 文档中的“第 2 步自然人登录”在代码里拆成两个动作：发送验证码和上传验证码。
- 代码仍然对外保持你昨天确认的 7 步业务语义，只是把验证码流程拆开实现。

## 关键方法

- `TaxLoginWorkflow.create_natural_person_account`
- `TaxLoginWorkflow.start_natural_person_login`
- `TaxLoginWorkflow.verify_natural_person_login`
- `TaxLoginWorkflow.list_enterprises`
- `TaxLoginWorkflow.choose_target_enterprise`
- `TaxLoginWorkflow.subscribe_enterprise_service`
- `TaxLoginWorkflow.create_multi_account`
- `TaxLoginWorkflow.login_enterprise_account`

## 典型输出

- 自然人开户：`account_id`、`agg_org_id`
- 验证码发送：`task_id`
- 企业列表：`enterprises[]`
- 企业订购：`agg_org_id`
- 多账号创建：`account_id`
- 企业登录校验：`ready`、`source`
