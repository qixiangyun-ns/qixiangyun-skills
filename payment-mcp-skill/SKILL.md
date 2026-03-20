---
name: payment-mcp-skill
description: 通过企享云 MCP 服务处理税款缴纳和完税证明相关任务，包括发起税款缴纳、查询缴款结果、发起下载完税证明并解析、查询完税证明解析结果。使用 bundled Python scripts 严格通过 MCP 调用，支持原子工具调用和闭环流程编排，适用于需要避免 Bash/curl/OpenAPI 发散调用的场景。
---

# 缴款 MCP Skill

通过企享云 MCP 直接完成税款缴纳和完税证明处理。

## 前置条件：鉴权参数

本 skill 依赖企享云开放平台凭证：

- `client_appkey`
- `client_secret`

**凭证读取优先级**：

1. Skill 根目录的 `.env`
2. Skills 公共父目录的 `.env`
3. 系统环境变量 `QXY_CLIENT_APPKEY` / `QXY_CLIENT_SECRET`

**首次使用时**，如果凭证不存在：

1. 询问用户的 `client_appkey` 和 `client_secret`
2. 如果用户没有凭证，提示：
   `appKey和appSecret请注册企享云开放平台申请 https://open.qixiangyun.com`
3. 在 skill 根目录或公共父目录创建 `.env`：
   ```env
   QXY_CLIENT_APPKEY=用户提供的appkey
   QXY_CLIENT_SECRET=用户提供的secret
   ```
4. 后续调用由脚本自动读取，无需再次询问

## 严格调用规则

1. 所有调用都只能通过 bundled scripts：
   - `scripts/mcp_client.py`
   - `scripts/payment_workflow.py`
2. 禁止直接使用 `curl`、`requests`、OpenAPI、网页浏览或手写 HTTP 请求
3. 禁止去探测、修改或依赖本地 Claude 的 MCP 配置文件
4. 如果脚本调用失败，直接根据脚本报错处理，不要自行切换到其他调用通道
5. 这个 skill 的目标是“只走 MCP”，不是“只要拿到结果就行”

## 可用脚本

### `scripts/mcp_client.py` — 原子调用

用于单个服务、单个 tool 的调用。

支持能力：

- 列出服务
- 列出服务下工具
- 查看工具 Schema
- 调用指定工具

### `scripts/payment_workflow.py` — 闭环调用

用于按固定步骤执行缴款闭环。

适合场景：

- 税款缴纳闭环
- 完税证明闭环
- 缴款归档闭环

## 支持模块

- `tax_payment`：税款缴纳
- `tax_payment_certificate`：获取完税证明

详细映射见 [references/mcp-services.md](references/mcp-services.md)

## 调用方式

列出当前 skill 支持的服务：

```bash
python3 scripts/mcp_client.py --list-services
```

列出某个服务下的工具：

```bash
python3 scripts/mcp_client.py --service tax_payment --list-tools
```

发起税款缴纳：

```bash
python3 scripts/mcp_client.py \
  --service tax_payment \
  --tool load_payment_task \
  --args @/tmp/payment.json
```

查询税款缴纳任务结果：

```bash
python3 scripts/mcp_client.py \
  --service tax_payment \
  --tool query_tax_payment_task_result_auto \
  --args '{"aggOrgId":"4788840764917695","taskId":"任务ID"}'
```

生成闭环配置模板：

```bash
python3 scripts/payment_workflow.py scaffold-config --output /tmp/payment-config.json
```

执行缴款闭环：

```bash
python3 scripts/payment_workflow.py run --config /tmp/payment-config.json --steps payment
```

执行缴款归档闭环：

```bash
python3 scripts/payment_workflow.py run --config /tmp/payment-config.json
```

工作流配置说明见 [references/workflow.md](references/workflow.md)

## 输出要求

输出结果应尽量包含以下信息：

- 当前使用的脚本
- 当前所属模块
- 实际调用的服务别名和 tool 名称
- 请求中的关键业务参数
- 返回中的 `aggOrgId`、`taskId`、`businessStatus`
- 当前是“原子调用”还是“闭环流程”
- 若成功，说明缴款结果或完税证明链接
- 若失败，给出可执行的下一步建议

## 参考文件

- 服务别名与工具映射：[references/mcp-services.md](references/mcp-services.md)
- 参数、轮询和任务状态说明：[references/api-notes.md](references/api-notes.md)
- 闭环配置说明：[references/workflow.md](references/workflow.md)
