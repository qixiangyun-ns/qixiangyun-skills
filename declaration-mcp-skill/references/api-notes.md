# 申报 API 使用要点

## 脚本调用规则

- 所有调用都应通过 `scripts/mcp_client.py` 或 `scripts/declaration_workflow.py`
- 脚本会自动从 `.env` 或环境变量注入：
  - `client_appkey`
  - `client_secret`
- 不要改用 `curl`、`requests`、OpenAPI 或其他 HTTP 通道

## 通用异步规则

- 发起类接口通常先返回 `taskId` 或 `taskIds`
- 查询类接口通常需要至少提供：
  - `aggOrgId`
  - `taskId`
- 如需持续追踪结果，默认按 10 秒间隔查询一次
- 状态含义：
  - `businessStatus=1`：进行中
  - `businessStatus=2`：失败
  - `businessStatus=3`：完成

## 初始化

- `load_init_data_task` 用于发起初始化
- `get_init_data` 用于取初始化结果
- `get_init_data` 常见参数示例：

```json
{
  "period": 6,
  "yzpzzlDm": "BDA0610606",
  "year": 2025,
  "aggOrgId": "4788840764917695"
}
```

## 上传申报数据

- `upload_tax_report_data_auto` 和 `upload_financial_report_data` 都可能带 `isDirectDeclare`
- `isDirectDeclare=true` 表示上传后直接发起申报
- 结果查询分别使用：
  - `query_upload_tax_report_result_auto`
  - `query_upload_financial_report_result_auto`

## 获取 PDF

- 当前 PDF 查询结果使用 `query_pdf_task_result_auto`
- 常见查询参数示例：

```json
{
  "aggOrgId": "xxx",
  "taskId": "xxx"
}
```

- 查询成功时通常能拿到：
  - `businessStatus`
  - `pdfFileUrl`
  - `detail`

## 申报信息查询

- `load_declare_info_task` 用于发起查询
- `query_declare_info_task_result_auto` 用于获取结构化结果
- 重要字段含义：
  - `state=1`：已申报
  - `state=0`：未申报
  - `payState=1`：已缴款
  - `payState=0`：未缴款
  - `payState=-1`：无需缴款
  - `fromDate`：税款所属期起
  - `toDate`：税款所属期止
  - `zspmDm`：征收品目代码
  - `yzpzzlDm`：应征凭证种类代码
- 注意：`detail[].payState` 不是所有税种都会返回，部分税种只返回 `state` 或其它业务字段，消费方不要假设结构完全一致

## 漏报检查

- 漏报检查遵循同样的异步模式
- 先用 `initiate_missing_declaration_check_task_auto`
- 再用 `query_missing_declaration_check_task_auto`

## 范围边界

本 skill 只覆盖申报相关流程，不包含以下事项：

- 税款缴纳
- 获取完税证明

这两类事项应使用 `payment-mcp-skill`
