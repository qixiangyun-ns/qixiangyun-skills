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
- `detail` 为必填数组，每个明细至少包含：
  - `yzpzzlDm`
  - `fromDate`
  - `toDate`
  - `taxAmount`
- `fromDate` / `toDate` 必须为 `YYYY-MM-DD`
- `taxAmount` 建议按真实缴款金额原样传递，不要在脚本外做二次换算
- 常见可选字段：
  - `jkfs`
  - `agreementAccount`
  - `yhzh`
  - `zspmDm`
  - `zsxmDm`
  - `bsswjg`
  - `kqyswjgmc`
  - `sebyz`
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

- `payState` 常用于判断该税种当前是否已缴款、处理中或失败
- 如果返回中同时存在 `detail[].taxAmount` 和 `detail[].taxAmountOfPaying`，应优先解释差额，再判断是否需要补缴或重试

## 获取完税证明

- 发起 tool：`initiate_wszm_parse_task_auto`
- 查询 tool：`query_wszm_parse_task_result_auto`
- `zsxmDtos` 为必填数组，每项至少包含：
  - `ssqQ`
  - `ssqZ`
  - `yzpzzlDm`
- 官方约束：
  - 最多 20 条
  - `yzpzzlDm + ssqQ + ssqZ (+ zspmDm)` 不允许重复
  - 最早所属期起与最晚所属期止不可跨自然年
  - 当应征凭证种类为文化事业建设费时，`zspmDm` 不能为空
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

- 如果查询结果返回 `detail[].wszmOssUrl`，说明已经拿到完税证明文件地址
- 如果任务成功但没有地址，需要结合 `resultMessage` 判断是否属于“任务成功但无可下载证明”的业务情况

## 范围边界

本 skill 只覆盖以下事项：

- 税款缴纳
- 获取完税证明

本 skill 不覆盖以下事项：

- 获取应申报清册
- 初始化
- 上传申报数据
- 获取申报 PDF
- 申报信息查询
- 漏报检查

这些事项应转到 `declaration-mcp-skill`。
