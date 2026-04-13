#!/usr/bin/env python3
"""申报 Skill 的企享云 MCP 通用能力库。"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import ssl
import time
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOGGER = logging.getLogger(__name__)

PROTOCOL_VERSION = "2024-11-05"
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_LONG_BACKOFF_MINUTES: tuple[int, ...] = (30, 60, 120, 240, 300)
DEFAULT_TRANSPORT_RETRY_COUNT = 2

try:
    import certifi
except ImportError:  # pragma: no cover - 运行环境不一定安装 certifi
    certifi = None

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

SERVICE_UNSTABLE_CODES = {"4998", "4999"}
MANUAL_REVIEW_CODES = {"4300", "4301"}
SERVICE_UNSTABLE_KEYWORDS = (
    "服务不稳定",
    "暂时不可用",
    "税局繁忙",
    "系统异常",
    "核心征管",
    "超时",
)
MANUAL_REVIEW_KEYWORDS = (
    "申报比对不通过",
    "数据比对失败",
)
COPY_TAX_KEYWORDS = ("抄报税",)
PENDING_MESSAGE_KEYWORDS = (
    "还在执行中",
    "请稍后获取",
    "处理中",
    "任务执行中",
    "任务处理中",
)


class QXYMCPError(Exception):
    """企享云 MCP 调用异常。"""


class QXYAuthError(QXYMCPError):
    """企享云凭证缺失或认证失败异常。"""


class QXYWorkflowError(QXYMCPError):
    """申报闭环流程编排异常。"""


def _env_flag(name: str, default: bool = False) -> bool:
    """读取布尔环境变量。"""

    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _build_ssl_context() -> ssl.SSLContext:
    """构建 HTTPS 调用所用的 SSL 上下文。"""

    if _env_flag("QXY_SSL_INSECURE", default=False):
        context = ssl._create_unverified_context()
        context.check_hostname = False
        return context

    ca_bundle = os.environ.get("QXY_SSL_CA_BUNDLE", "").strip()
    if ca_bundle:
        context = ssl.create_default_context(cafile=ca_bundle)
    elif certifi is not None:
        context = ssl.create_default_context(cafile=certifi.where())
    else:
        context = ssl.create_default_context()

    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def _is_retryable_transport_error(exc: Exception) -> bool:
    """判断传输层异常是否适合短重试。"""

    if isinstance(exc, ssl.SSLCertVerificationError):
        return False
    if isinstance(exc, ssl.CertificateError):
        return False
    if isinstance(exc, ssl.SSLEOFError):
        return True
    if isinstance(exc, ssl.SSLError):
        message = str(exc).lower()
        retry_keywords = ("timed out", "timeout", "eof", "unexpected eof", "wrong version number")
        return any(keyword in message for keyword in retry_keywords)
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, socket.timeout):
        return True
    if isinstance(exc, URLError):
        reason = exc.reason
        if isinstance(reason, Exception):
            return _is_retryable_transport_error(reason)
        reason_text = str(reason).lower()
        retry_keywords = ("timed out", "timeout", "tempor", "reset", "refused", "eof")
        return any(keyword in reason_text for keyword in retry_keywords)
    return False


def _format_transport_error(endpoint: str, exc: Exception) -> str:
    """构造更清晰的传输层报错。"""

    if isinstance(exc, ssl.SSLCertVerificationError):
        return f"SSL 证书校验失败: {exc}；endpoint={endpoint}"
    if isinstance(exc, ssl.CertificateError):
        return f"SSL 主机名校验失败: {exc}；endpoint={endpoint}"
    if isinstance(exc, ssl.SSLError):
        return f"SSL 握手失败: {exc}；endpoint={endpoint}"
    if isinstance(exc, URLError):
        return f"网络连接失败: {exc.reason}；endpoint={endpoint}"
    return f"网络传输失败: {exc}；endpoint={endpoint}"


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
    raise QXYMCPError(f"工具 `{tool_name}` 未配置默认服务，请显式传入 --service。")


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

    ssl_context = _build_ssl_context()
    max_transport_attempts = int(os.environ.get("QXY_TRANSPORT_RETRY_COUNT", str(DEFAULT_TRANSPORT_RETRY_COUNT))) + 1
    last_transport_error: Exception | None = None

    for attempt in range(1, max_transport_attempts + 1):
        try:
            with urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS, context=ssl_context) as response:
                new_session_id = response.headers.get("Mcp-Session-Id") or session_id
                body_text = response.read().decode("utf-8")
            break
        except HTTPError as exc:
            raise QXYMCPError(f"HTTP 错误 {exc.code}: {exc.reason}；endpoint={endpoint}") from exc
        except (ssl.SSLError, URLError, TimeoutError, socket.timeout) as exc:
            last_transport_error = exc
            if attempt < max_transport_attempts and _is_retryable_transport_error(exc):
                backoff_seconds = min(2 ** (attempt - 1), 4)
                LOGGER.warning(
                    "MCP 传输异常，准备第 %s/%s 次重试，endpoint=%s，原因=%s",
                    attempt + 1,
                    max_transport_attempts,
                    endpoint,
                    exc,
                )
                time.sleep(backoff_seconds)
                continue
            raise QXYMCPError(_format_transport_error(endpoint, exc)) from exc
    else:  # pragma: no cover - 理论上 break/raise 会提前结束
        if last_transport_error is not None:
            raise QXYMCPError(_format_transport_error(endpoint, last_transport_error)) from last_transport_error
        raise QXYMCPError(f"网络传输失败，未得到有效响应；endpoint={endpoint}")

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
                "version": "2.0.0",
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


def _looks_like_business_code(value: Any) -> bool:
    """判断值是否像业务码。"""

    text = str(value).strip()
    if not text:
        return False
    if text.upper() in {"SUCCESS", "BUSINESS_ERROR", "AUTH_ERROR"}:
        return False
    return bool(re.fullmatch(r"\d{4}", text))


def _collect_values_by_key(payload: Any, wanted_keys: Sequence[str]) -> list[Any]:
    """递归提取指定键的值。"""

    matched: list[Any] = []
    wanted = set(wanted_keys)

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in wanted:
                    matched.append(value)
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(payload)
    return matched


def extract_business_code(payload: Any) -> str:
    """提取最能反映业务语义的业务码。"""

    if not isinstance(payload, dict):
        return ""

    data = payload.get("data")
    if isinstance(data, dict):
        direct_code = data.get("code")
        if _looks_like_business_code(direct_code):
            return str(direct_code)

    for value in _collect_values_by_key(payload, ("code", "resultCode", "errorCode")):
        if _looks_like_business_code(value):
            return str(value).strip()

    top_level_code = payload.get("code")
    if isinstance(top_level_code, str):
        return top_level_code.strip()
    return ""


def extract_message(payload: Any) -> str:
    """提取最有代表性的消息文本。"""

    if not isinstance(payload, dict):
        return ""

    for candidate in (
        payload.get("message"),
        payload.get("resultMessage"),
        payload.get("result"),
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()

    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("message", "resultMessage", "result", "businessStatusName"):
            candidate = data.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return ""


def _to_float(value: Any) -> float | None:
    """将值安全转换为浮点数。"""

    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def extract_tax_amount(payload: Any) -> float | None:
    """提取税额字段。"""

    candidates = _collect_values_by_key(
        payload,
        ("taxAmount", "bqybtse", "bqybtseSj", "ynsehj", "sjyyjsdseLj"),
    )
    for value in candidates:
        number = _to_float(value)
        if number is not None:
            return number
    return None


def is_service_unstable(payload: Any) -> bool:
    """判断是否命中税局不稳定类异常。"""

    code = extract_business_code(payload)
    message = extract_message(payload)
    if code in SERVICE_UNSTABLE_CODES:
        return True
    return any(keyword in message for keyword in SERVICE_UNSTABLE_KEYWORDS)


def requires_manual_review(payload: Any) -> bool:
    """判断是否需要人工复核。"""

    code = extract_business_code(payload)
    message = extract_message(payload)
    if code in MANUAL_REVIEW_CODES:
        return True
    return any(keyword in message for keyword in MANUAL_REVIEW_KEYWORDS)


def is_copy_tax_required(payload: Any) -> bool:
    """判断是否命中抄报税提示。"""

    message = extract_message(payload)
    return any(keyword in message for keyword in COPY_TAX_KEYWORDS)


def infer_task_state(payload: Any) -> str:
    """推断任务状态。"""

    if is_service_unstable(payload):
        return "pending"
    if requires_manual_review(payload):
        return "manual_review_required"
    if is_copy_tax_required(payload):
        return "failed"

    markers = _collect_status_values(payload)

    for key, value in markers:
        if key in {"businessStatus", "business_status"}:
            if value == "1":
                return "pending"
            if value == "2":
                return "failed"
            if value == "3":
                return "success"

    business_code = extract_business_code(payload)
    if business_code in {"2000", "2002", "4601"}:
        return "success"
    if business_code in SERVICE_UNSTABLE_CODES:
        return "pending"
    if business_code in MANUAL_REVIEW_CODES:
        return "manual_review_required"
    if business_code in {"4302", "4317", "4501"}:
        return "failed"

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

    if business_code == "BUSINESS_ERROR":
        return "failed"
    message = extract_message(payload)
    if any(keyword in message for keyword in PENDING_MESSAGE_KEYWORDS):
        return "pending"
    return "unknown"


def is_retryable_response(payload: Any) -> bool:
    """判断当前响应是否适合进入长退避。"""

    state = infer_task_state(payload)
    if state == "pending":
        return True
    return is_service_unstable(payload)


def poll_tool(
    service_name: str,
    tool_name: str,
    tool_args: Mapping[str, Any],
    *,
    interval_seconds: int = 5,
    max_attempts: int = 60,
    short_interval_seconds: int | None = None,
    short_max_attempts: int | None = None,
    long_backoff_minutes: Sequence[int] | None = None,
    log_context: Mapping[str, Any] | None = None,
    sleep_func: Any = time.sleep,
) -> dict[str, Any]:
    """轮询查询类工具，先短轮询，再生成长退避计划。"""

    actual_interval = short_interval_seconds or interval_seconds
    actual_attempts = short_max_attempts or max_attempts
    if actual_interval <= 0:
        raise ValueError("interval_seconds 必须大于 0。")
    if actual_attempts <= 0:
        raise ValueError("max_attempts 必须大于 0。")

    backoff_plan = list(long_backoff_minutes or DEFAULT_LONG_BACKOFF_MINUTES)
    history: list[dict[str, Any]] = []
    last_result: Any = None
    last_state = "unknown"
    retryable = False
    context = dict(log_context or {})
    task_id = str(context.get("taskId") or tool_args.get("taskId") or "-")
    tax_code = str(context.get("yzpzzlDm") or tool_args.get("yzpzzlDm") or "-")

    for attempt in range(1, actual_attempts + 1):
        last_result = call_tool(service_name, tool_name, tool_args)
        last_state = infer_task_state(last_result)
        retryable = is_retryable_response(last_result)
        history.append(
            {
                "attempt": attempt,
                "state": last_state,
                "business_code": extract_business_code(last_result),
                "message": extract_message(last_result),
            }
        )
        LOGGER.info(
            "轮询服务=%s 工具=%s taskId=%s yzpzzlDm=%s 第 %s/%s 次，状态=%s",
            service_name,
            tool_name,
            task_id,
            tax_code,
            attempt,
            actual_attempts,
            last_state,
        )
        if last_state in {"success", "failed", "manual_review_required"}:
            return {
                "state": last_state,
                "phase": "short_poll",
                "attempts": attempt,
                "retryable": retryable,
                "result": last_result,
                "history": history,
                "next_retry_after_minutes": None,
                "backoff_plan_minutes": backoff_plan,
            }
        if attempt < actual_attempts:
            sleep_func(actual_interval)

    next_retry_after = backoff_plan[0] if retryable and backoff_plan else None
    return {
        "state": "pending" if retryable else "timeout",
        "phase": "backoff" if retryable else "short_poll",
        "attempts": actual_attempts,
        "retryable": retryable,
        "result": last_result,
        "history": history,
        "next_retry_after_minutes": next_retry_after,
        "backoff_plan_minutes": backoff_plan,
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
    if period < 1 or period > 12:
        raise ValueError("`period` 必须在 1 到 12 之间，表示申报月份。")
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

    poll_strategy = config.get("poll_strategy", {})
    if poll_strategy and not isinstance(poll_strategy, dict):
        raise ValueError("`poll_strategy` 必须是对象。")
    short_interval_seconds = int(
        poll_strategy.get("short_interval_seconds", poll_interval_seconds)
    )
    short_max_attempts = int(
        poll_strategy.get("short_max_attempts", max_poll_attempts)
    )
    long_backoff_minutes = poll_strategy.get(
        "long_backoff_minutes", list(DEFAULT_LONG_BACKOFF_MINUTES)
    )
    if not isinstance(long_backoff_minutes, list) or not long_backoff_minutes:
        raise ValueError("`poll_strategy.long_backoff_minutes` 必须是非空数组。")

    checkpoint = config.get("checkpoint", {})
    if checkpoint and not isinstance(checkpoint, dict):
        raise ValueError("`checkpoint` 必须是对象。")

    rules = config.get("rules", {})
    if rules and not isinstance(rules, dict):
        raise ValueError("`rules` 必须是对象。")

    manual_review = config.get("manual_review", {})
    if manual_review and not isinstance(manual_review, dict):
        raise ValueError("`manual_review` 必须是对象。")

    post_actions = config.get("post_actions", {})
    if post_actions and not isinstance(post_actions, dict):
        raise ValueError("`post_actions` 必须是对象。")

    normalized["poll_interval_seconds"] = poll_interval_seconds
    normalized["max_poll_attempts"] = max_poll_attempts
    normalized["poll_strategy"] = {
        "short_interval_seconds": short_interval_seconds,
        "short_max_attempts": short_max_attempts,
        "long_backoff_minutes": [int(item) for item in long_backoff_minutes],
    }
    normalized["checkpoint"] = {
        "enabled": bool(checkpoint.get("enabled", True)),
        "path": checkpoint.get("path"),
        "resume_mode": checkpoint.get("resume_mode", "from_pending"),
    }
    normalized["rules"] = {
        "accrual_mode": rules.get("accrual_mode", "validate_and_suggest"),
        "response_rule_set": rules.get("response_rule_set", "default"),
        "tax_burden_enabled": bool(rules.get("tax_burden_enabled", False)),
        "industry_code": rules.get("industry_code"),
        "industry_name": rules.get("industry_name"),
        "tax_burden_blocking": bool(rules.get("tax_burden_blocking", False)),
        "allow_force_declare_on_4300": bool(rules.get("allow_force_declare_on_4300", False)),
    }
    normalized["manual_review"] = {
        "enabled": bool(manual_review.get("enabled", True)),
        "emit_customer_message": bool(manual_review.get("emit_customer_message", True)),
    }
    normalized["post_actions"] = {
        "auto_download_pdf": bool(post_actions.get("auto_download_pdf", False)),
        "auto_missing_check": bool(post_actions.get("auto_missing_check", False)),
        "auto_prepare_payment": bool(post_actions.get("auto_prepare_payment", False)),
    }
    return normalized
