# 申报 MCP 服务清单

以下内容用于 `scripts/mcp_client.py` 和 `scripts/declaration_workflow.py` 的服务别名与工具映射。
调用时应通过脚本发起，不要手写 HTTP 请求。

## 获取应申报清册

- 服务别名：`roster_entry`
- 中文模块名：获取应申报清册
- Tool：
  - `initiate_declaration_entry_task_auto`：发起获取申报条目
  - `query_roster_entry_task_auto`：查询获取条目任务

## 初始化

- 服务别名：`initialize_data`
- 中文模块名：初始化
- Tool：
  - `load_init_data_task`：发起数据初始化任务
  - `get_init_data`：初始化数据查询

## 上传申报数据

- 服务别名：`declaration_submission`
- 中文模块名：上传申报数据
- Tool：
  - `upload_tax_report_data_auto`：上传申报数据
  - `query_upload_tax_report_result_auto`：获取申报结果
  - `upload_financial_report_data`：上传财报数据
  - `query_upload_financial_report_result_auto`：获取财报申报结果

## 获取 PDF

- 服务别名：`pdf_download`
- 中文模块名：获取PDF
- Tool：
  - `load_pdf_task`：发起下载当期 PDF 任务
  - `load_wq_pdf_task`：发起下载往期 PDF 任务
  - `query_pdf_task_result_auto`：查询 PDF 任务返回结构化数据

## 申报信息查询

- 服务别名：`declaration_query`
- 中文模块名：申报信息查询
- Tool：
  - `load_declare_info_task`：申报信息查询
  - `query_declare_info_task_result_auto`：查询申报信息任务

## 漏报检查

- 服务别名：`missing_declaration_check`
- 中文模块名：漏报检查
- Tool：
  - `initiate_missing_declaration_check_task_auto`：发起漏报检查任务
  - `query_missing_declaration_check_task_auto`：查询漏报检查任务
