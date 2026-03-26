#!/usr/bin/env python3
"""申报 Skill 的多服务 MCP 客户端。"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from qxy_mcp_lib import (
    QXYAuthError,
    QXYMCPError,
    SERVICE_LABELS,
    call_tool,
    describe_tool,
    load_credentials,
    list_services,
    list_tools,
    parse_json_mapping,
    resolve_service_for_tool,
)

LOGGER = logging.getLogger(__name__)


def _dump_json(payload: Any) -> None:
    """将结果以 JSON 输出到标准输出。"""

    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def build_parser() -> argparse.ArgumentParser:
    """构建 CLI 参数解析器。"""

    parser = argparse.ArgumentParser(
        description="企享云申报 MCP 工具调用客户端",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python3 scripts/mcp_client.py --list-services\n\n"
            "  python3 scripts/mcp_client.py --service roster_entry --list-tools\n\n"
            "  python3 scripts/mcp_client.py --service roster_entry "
            "--tool initiate_declaration_entry_task_auto "
            "--args '{\"aggOrgId\": \"4788840764917695\", \"year\": 2026, \"period\": 3}'\n\n"
            "  python3 scripts/mcp_client.py --tool query_roster_entry_task_auto "
            "--args '{\"aggOrgId\": \"4788840764917695\", \"taskId\": \"123\"}'"
        ),
    )
    parser.add_argument("--service", help="服务别名，如 roster_entry")
    parser.add_argument("--tool", help="工具名称")
    parser.add_argument("--args", help="工具参数 JSON，支持 @文件路径")
    parser.add_argument("--list-services", action="store_true", help="列出所有服务")
    parser.add_argument("--list-tools", action="store_true", help="列出服务下的工具")
    parser.add_argument("--check-config", action="store_true", help="检查凭证是否已配置")
    parser.add_argument(
        "--describe-tool",
        metavar="TOOL_NAME",
        help="查看指定工具的 Schema，需要配合 --service 使用",
    )
    return parser


def main() -> int:
    """CLI 入口。"""

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.list_services:
            services = list_services()
            _dump_json(
                [
                    {
                        "service": service_name,
                        "label": SERVICE_LABELS.get(service_name, service_name),
                        "endpoint": endpoint,
                    }
                    for service_name, endpoint in services.items()
                ]
            )
            return 0

        if args.check_config:
            credentials = load_credentials()
            _dump_json(
                {
                    "success": True,
                    "client_appkey": credentials["client_appkey"],
                    "client_secret_masked": "***",
                    "message": "凭证已找到。实际有效性会在首次真实工具调用时进一步校验。",
                }
            )
            return 0

        if args.list_tools:
            if not args.service:
                parser.error("--list-tools 需要配合 --service 使用。")
            _dump_json(list_tools(args.service))
            return 0

        if args.describe_tool:
            if not args.service:
                parser.error("--describe-tool 需要配合 --service 使用。")
            _dump_json(describe_tool(args.service, args.describe_tool))
            return 0

        if args.tool:
            service_name = resolve_service_for_tool(args.service, args.tool)
            _dump_json(call_tool(service_name, args.tool, parse_json_mapping(args.args)))
            return 0

        parser.print_help()
        return 1
    except (QXYMCPError, QXYAuthError, ValueError, json.JSONDecodeError) as exc:
        LOGGER.error("%s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
