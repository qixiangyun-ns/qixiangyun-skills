#!/usr/bin/env python3
"""申报 Skill 的企享云 MCP 通用能力库。"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOGGER = logging.getLogger(__name__)

PROTOCOL_VERSION = "2024-11-05"
DEFAULT_TIMEOUT_SECONDS = 30

SERVICE_ENDPOINTS: dict[str, str] = {
    "roster_entry": "https://mcp.qixiangyun.com/mcp/roster_entry-http/",
    "initialize_data": "https://mcp.qixiangyun.com/mcp/initialize_data-http/",
    "declaration_submission": "https://mcp.qixiangyun.com/mcp/declaration_submission-http/",
    "pdf_download": "https://mcp.qixiangyun.com/mcp/pdf_download-http/",
    "declaration_query": "https://mcp.qixiangyun.com/mcp/declaration_query-http/",
    "missing_declaration_check": "https://mcp.qixiangyun.com/mcp/missing_declaration_check-http/",
}

SERVICE_LABELS: dict[str, str] = {
    "roster_entry": "获取应申报清册",
    "initialize_data": "初始化",
    "declaration_submission": "上传申报数据",
    "pdf_download": "获取PDF",
    "declaration_query": "申报信息查询",
    "missing_declaration_check": "漏报检查",
}

TOOL_TO_SERVICE: dict[str, str] = {
    "initiate_declaration_entry_task_auto": "roster_entry",
    "query_roster_entry_task_auto": "roster_entry",
    "load_init_data_task": "initialize_data",
    "get_init_data": "initialize_data",
    "upload_tax_report_data_auto": "declaration_submission",
    "query_upload_tax_report_result_auto": "declaration_submission",
    "upload_financial_report_data": "declaration_submission",
    "query_upload_financial_report_result_auto": "declaration_submission",
    "load_pdf_task": "pdf_download",
    "load_wq_pdf_task": "pdf_download",
    "query_pdf_task_result_auto": "pdf_download",
    "load_declare_info_task": "declaration_query",
    "query_declare_info_task_result_auto": "declaration_query",
    "initiate_missing_declaration_check_task_auto": "missing_declaration_check",
    "query_missing_declaration_check_task_auto": "missing_declaration_check",
}

TASK_ID_KEYS: tuple[str, ...] = (
    "taskId",
    "task_id",
    "bizTaskId",
    "biz_task_id",
    "taskNo",
    "taskCode",
)

STATUS_KEYS: tuple[str, ...] = (
    "status",
    "state",
    "taskStatus",
    "task_state",
    "progressStatus",
    "resultStatus",
    "businessStatus",
    "business_status",
)

BOOLEAN_PENDING_KEYS: tuple[str, ...] = (
    "finished",
    "isFinish",
    "isFinished",
    "done",
    "completed",
)

PENDING_MARKERS = {
    "pending",
    "processing",
    "running",
    "queued",
    "queue",
    "waiting",
    "doing",
    "executing",
    "submitted",
    "init",
    "created",
    "inprogress",
    "in_progress",
    "0",
    "1",
}

SUCCESS_MARKERS = {
    "success",
    "succeeded",
    "done",
    "finished",
    "complete",
    "completed",
    "ok",
    "pass",
    "passed",
    "3",
}

FAILURE_MARKERS = {
    "fail",
    "failed",
    "error",
    "exception",
    "cancel",
    "cancelled",
    "canceled",
    "timeout",
    "rejected",
    "2",
}


class QXYMCPError(Exception):
    """企享云 MCP 调用异常。"""


class QXYAuthError(QXYMCPError):
    """企享云凭证缺失或认证失败异常。"""


class QXYWorkflowError(QXYMCPError):
    """申报闭环流程编排异常。"""


def _find_env_file(start_path: Path | None = None) -> Path | None:
    """向上查找 `.env` 文件。"""

    current = start_path or Path(__file__).resolve().parent
    for _ in range(6):
        env_path = current / ".env"
        if env_path.exists():
            return env_path
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def _load_env(env_path: Path) -> None:
    """读取 `.env` 文件到进程环境变量。"""

    with env_path.open("r", encoding="utf-8") as file_obj:
        for raw_line in file_obj:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env_key = key.strip()
            env_value = value.strip().strip("'\"")
            if env_key and env_key not in os.environ:
                os.environ[env_key] = env_value


def load_credentials() -> dict[str, str]:
    """加载企享云凭证。"""

    env_file = _find_env_file()
    if env_file is not None:
        _load_env(env_file)

    appkey = os.environ.get("QXY_CLIENT_APPKEY", "").strip()
    secret = os.environ.get("QXY_CLIENT_SECRET", "").strip()
    if appkey and secret:
        return {"client_appkey": appkey, "client_secret": secret}

    raise QXYAuthError(
        "企享云凭证未配置。请按以下任一方式提供：\n"
        "1. 在 skill 根目录或公共父目录创建 .env 文件：\n"
        "   QXY_CLIENT_APPKEY=你的appkey\n"
        "   QXY_CLIENT_SECRET=你的secret\n"
        "2. 设置环境变量：\n"
        "   export QXY_CLIENT_APPKEY=你的appkey\n"
        "   export QXY_CLIENT_SECRET=你的secret\n"
        "如果还没有凭证，请前往 https://open.qixiangyun.com 申请。"
    )


def list_services() -> dict[str, str]:
    """返回当前 skill 支持的服务列表。"""

    return SERVICE_ENDPOINTS.copy()


def merge_non_null(*parts: Mapping[str, Any]) -> dict[str, Any]:
    """合并多个字典并剔除空值。"""

    merged: dict[str, Any] = {}
    for part in parts:
        for key, value in part.items():
            if value is not None:
                merged[key] = value
    return merged


def load_json_data(path: str | Path) -> Any:
    """读取 JSON 文件。"""

    json_path = Path(path).expanduser().resolve()
    with json_path.open("r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def parse_json_mapping(raw_value: str | None) -> dict[str, Any]:
    """解析 JSON 参数，支持 `@文件路径` 语法。"""

    if not raw_value:
        return {}

    payload: Any
    if raw_value.startswith("@"):
        payload = load_json_data(raw_value[1:])
    else:
        payload = json.loads(raw_value)

    if not isinstance(payload, dict):
        raise ValueError("JSON 参数必须是对象类型。")
    return payload


def _service_endpoint(service_name: str) -> str:
    """获取服务地址。"""

    if service_name not in SERVICE_ENDPOINTS:
        available = ", ".join(sorted(SERVICE_ENDPOINTS))
        raise QXYMCPError(f"未知服务 `{service_name}`。可选服务：{available}")
    return SERVICE_ENDPOINTS[service_name]


def resolve_service_for_tool(service_name: str | None, tool_name: str) -> str:
    """解析工具对应的服务别名。"""

    if service_name:
        return service_name
    if tool_name in TOOL_TO_SERVICE:
        return TOOL_TO_SERVICE[tool_name]
    raise QXYMCPError(
        f"工具 `{tool_name}` 未配置默认服务，请显式传入 --service。"
    )


def _parse_response_body(body_text: str) -> dict[str, Any]:
    """解析 MCP 响应体，兼容 SSE 与纯 JSON。"""

    result_data: dict[str, Any] | None = None
    for line in body_text.strip().splitlines():
        line = line.strip()
        if line.startswith("data: "):
            result_data = json.loads(line[6:])

    if result_data is not None:
        return result_data

    return json.loads(body_text)


def _send_jsonrpc(
    endpoint: str,
    method: str,
    params: Mapping[str, Any],
    request_id: int,
    session_id: str | None = None,
) -> tuple[dict[str, Any], str | None]:
    """发送 JSON-RPC 请求。"""

    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "params": dict(params),
        "id": request_id,
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id

    request = Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
            new_session_id = response.headers.get("Mcp-Session-Id") or session_id
            body_text = response.read().decode("utf-8")
    except HTTPError as exc:
        raise QXYMCPError(f"HTTP 错误 {exc.code}: {exc.reason}") from exc
    except URLError as exc:
        raise QXYMCPError(f"网络连接失败: {exc.reason}") from exc

    result_data = _parse_response_body(body_text)
    if "error" in result_data:
        error_info = result_data["error"]
        raise QXYMCPError(
            f"JSON-RPC 错误 [{error_info.get('code')}]: {error_info.get('message')}"
        )
    return result_data, new_session_id


def _initialize_session(service_name: str) -> str:
    """初始化服务会话并返回 Session ID。"""

    endpoint = _service_endpoint(service_name)
    _, session_id = _send_jsonrpc(
        endpoint=endpoint,
        method="initialize",
        params={
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {
                "name": "qixiangyun-declaration-skill",
                "version": "1.0.0",
            },
        },
        request_id=1,
    )
    if not session_id:
        raise QXYMCPError(f"服务 `{service_name}` 初始化失败，未返回 Session ID。")
    return session_id


def list_tools(service_name: str) -> list[dict[str, Any]]:
    """列出某个服务下的 MCP 工具。"""

    endpoint = _service_endpoint(service_name)
    session_id = _initialize_session(service_name)
    result, _ = _send_jsonrpc(endpoint, "tools/list", {}, 2, session_id)
    return result.get("result", {}).get("tools", [])


def describe_tool(service_name: str, tool_name: str) -> dict[str, Any]:
    """返回某个工具的定义。"""

    for tool in list_tools(service_name):
        if tool.get("name") == tool_name:
            return tool
    raise QXYMCPError(f"服务 `{service_name}` 下未找到工具 `{tool_name}`。")


def _extract_tool_payload(tool_result: dict[str, Any]) -> Any:
    """提取工具响应中的结构化结果。"""

    if "structuredContent" in tool_result:
        return tool_result["structuredContent"]

    content = tool_result.get("content")
    if isinstance(content, list) and content:
        text = content[0].get("text", "")
        if not text:
            return tool_result
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw_text": text}

    return tool_result


def call_tool(
    service_name: str,
    tool_name: str,
    tool_args: Mapping[str, Any] | None = None,
    *,
    inject_credentials: bool = True,
) -> Any:
    """调用指定服务的 MCP 工具。"""

    endpoint = _service_endpoint(service_name)
    session_id = _initialize_session(service_name)
    payload = dict(tool_args or {})
    if inject_credentials:
        payload = {**payload, **load_credentials()}

    result, _ = _send_jsonrpc(
        endpoint=endpoint,
        method="tools/call",
        params={"name": tool_name, "arguments": payload},
        request_id=2,
        session_id=session_id,
    )

    tool_result = result.get("result", {})
    content = _extract_tool_payload(tool_result)
    if isinstance(content, dict) and content.get("code") == "AUTH_ERROR":
        raise QXYAuthError(f"企享云认证失败: {content.get('message', '未知错误')}")
    return content


def extract_task_id(payload: Any) -> str | None:
    """从响应中递归提取任务 ID。"""

    matches: list[str] = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in TASK_ID_KEYS and value not in (None, ""):
                    matches.append(str(value))
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(payload)
    return matches[0] if matches else None


def _collect_status_values(payload: Any) -> list[tuple[str, str]]:
    """递归收集状态字段和值。"""

    markers: list[tuple[str, str]] = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in STATUS_KEYS and value not in (None, ""):
                    markers.append((key, str(value).strip().lower().replace(" ", "")))
                if key in BOOLEAN_PENDING_KEYS and isinstance(value, bool):
                    markers.append((key, "true" if value else "false"))
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(payload)
    return markers


def infer_task_state(payload: Any) -> str:
    """推断任务状态。"""

    markers = _collect_status_values(payload)

    for key, value in markers:
        if key in {"businessStatus", "business_status"}:
            if value == "1":
                return "pending"
            if value == "2":
                return "failed"
            if value == "3":
                return "success"

    for _, value in markers:
        if value in FAILURE_MARKERS:
            return "failed"
    for _, value in markers:
        if value in SUCCESS_MARKERS:
            return "success"
    for _, value in markers:
        if value in PENDING_MARKERS:
            return "pending"

    boolean_markers = [value for key, value in markers if key in BOOLEAN_PENDING_KEYS]
    if boolean_markers:
        return "success" if all(value == "true" for value in boolean_markers) else "pending"

    return "unknown"


def poll_tool(
    service_name: str,
    tool_name: str,
    tool_args: Mapping[str, Any],
    *,
    interval_seconds: int = 5,
    max_attempts: int = 60,
) -> dict[str, Any]:
    """轮询查询类工具，直到状态终态或超时。"""

    if interval_seconds <= 0:
        raise ValueError("interval_seconds 必须大于 0。")
    if max_attempts <= 0:
        raise ValueError("max_attempts 必须大于 0。")

    last_result: Any = None
    last_state = "unknown"
    for attempt in range(1, max_attempts + 1):
        last_result = call_tool(service_name, tool_name, tool_args)
        last_state = infer_task_state(last_result)
        LOGGER.info(
            "轮询服务=%s 工具=%s 第 %s/%s 次，状态=%s",
            service_name,
            tool_name,
            attempt,
            max_attempts,
            last_state,
        )
        if last_state in {"success", "failed"}:
            return {
                "state": last_state,
                "attempts": attempt,
                "result": last_result,
            }
        if attempt < max_attempts:
            time.sleep(interval_seconds)

    return {
        "state": "timeout",
        "attempts": max_attempts,
        "result": last_result,
    }


def resolve_init_query_items(step_config: Mapping[str, Any]) -> list[dict[str, Any]]:
    """解析初始化数据查询项。"""

    explicit_items = step_config.get("query_items")
    if explicit_items is not None:
        if not isinstance(explicit_items, list):
            raise ValueError("`init_data.query_items` 必须是数组。")
        normalized: list[dict[str, Any]] = []
        for item in explicit_items:
            if not isinstance(item, dict) or not item.get("yzpzzlDm"):
                raise ValueError("`init_data.query_items` 中每项都必须包含 `yzpzzlDm`。")
            normalized.append({"yzpzzlDm": str(item["yzpzzlDm"])})
        return normalized

    zsxm_list = step_config.get("zsxmList", [])
    if not isinstance(zsxm_list, list):
        raise ValueError("`init_data.zsxmList` 必须是数组。")

    seen_codes: set[str] = set()
    query_items: list[dict[str, Any]] = []
    for item in zsxm_list:
        if not isinstance(item, dict):
            continue
        code = item.get("yzpzzlDm")
        if code and str(code) not in seen_codes:
            seen_codes.add(str(code))
            query_items.append({"yzpzzlDm": str(code)})
    return query_items


def validate_workflow_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """校验并标准化闭环配置。"""

    agg_org_id = config.get("aggOrgId")
    year = config.get("year")
    period = config.get("period")
    steps = config.get("steps", {})

    if not isinstance(agg_org_id, str) or not agg_org_id.strip():
        raise ValueError("`aggOrgId` 是必填字符串。")
    if not isinstance(year, int):
        raise ValueError("`year` 必须是整数。")
    if not isinstance(period, int):
        raise ValueError("`period` 必须是整数。")
    if not isinstance(steps, dict):
        raise ValueError("`steps` 必须是对象。")

    normalized = dict(config)
    normalized["aggOrgId"] = agg_org_id.strip()
    normalized["year"] = year
    normalized["period"] = period
    normalized["steps"] = steps
    poll_interval_seconds = int(config.get("poll_interval_seconds", 5))
    max_poll_attempts = int(config.get("max_poll_attempts", 60))
    if poll_interval_seconds <= 0:
        raise ValueError("`poll_interval_seconds` 必须大于 0。")
    if max_poll_attempts <= 0:
        raise ValueError("`max_poll_attempts` 必须大于 0。")

    normalized["poll_interval_seconds"] = poll_interval_seconds
    normalized["max_poll_attempts"] = max_poll_attempts
    return normalized
