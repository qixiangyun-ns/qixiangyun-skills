# 缴款 MCP 服务清单

以下内容用于 `scripts/mcp_client.py` 和 `scripts/payment_workflow.py` 的服务别名与工具映射。
调用时应通过脚本发起，不要手写 HTTP 请求。

## 税款缴纳

- 服务别名：`tax_payment`
- 中文模块名：税款缴纳
- MCP 地址：`https://mcp.qixiangyun.com/mcp/tax_payment-http/`
- Tool：
  - `load_payment_task`：发起税款缴纳
  - `query_tax_payment_task_result_auto`：查询税款缴纳任务结果

## 获取完税证明

- 服务别名：`tax_payment_certificate`
- 中文模块名：获取完税证明
- MCP 地址：`https://mcp.qixiangyun.com/mcp/tax_payment_certificate-http/`
- Tool：
  - `initiate_wszm_parse_task_auto`：发起下载完税证明并解析任务
  - `query_wszm_parse_task_result_auto`：查询下载完税证明并解析任务结果

## 工作流步骤映射

- `payment` 步骤：
  - 发起服务：`tax_payment`
  - 发起 tool：`load_payment_task`
  - 轮询服务：`tax_payment`
  - 轮询 tool：`query_tax_payment_task_result_auto`
- `certificate` 步骤：
  - 发起服务：`tax_payment_certificate`
  - 发起 tool：`initiate_wszm_parse_task_auto`
  - 轮询服务：`tax_payment_certificate`
  - 轮询 tool：`query_wszm_parse_task_result_auto`
