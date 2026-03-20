# 缴款 API 使用要点

## 脚本调用规则

- 所有调用都应通过 `scripts/mcp_client.py` 或 `scripts/payment_workflow.py`
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

## 税款缴纳

- 发起 tool：`load_payment_task`
- 查询 tool：`query_tax_payment_task_result_auto`
- 常见发起参数示例：

```json
{
  "period": 6,
  "year": 2025,
  "aggOrgId": 538272437674687,
  "detail": [
    {
      "fromDate": "2025-05-01",
      "sebyz": "N",
      "yzpzzlDm": "BDA0610606",
      "toDate": "2025-05-31",
      "taxAmount": 6462455.03,
      "agreementAccount": "165232400001102517"
    }
  ]
}
```

- 查询成功时常见字段：
  - `resultMessage`
  - `detail`
  - `detail[].payState`
  - `detail[].taxAmount`
  - `detail[].taxAmountOfPaying`

## 获取完税证明

- 发起 tool：`initiate_wszm_parse_task_auto`
- 查询 tool：`query_wszm_parse_task_result_auto`
- 常见发起参数示例：

```json
{
  "zsxmDtos": [
    {
      "yzpzzlDm": "BDA0610606",
      "ssqQ": "2025-03-01",
      "ssqZ": "2025-03-31"
    }
  ],
  "period": 4,
  "year": 2025,
  "aggOrgId": "4797678715636544"
}
```

- 查询成功时常见字段：
  - `result`
  - `resultMessage`
  - `detail`
  - `detail[].wszmOssUrl`
