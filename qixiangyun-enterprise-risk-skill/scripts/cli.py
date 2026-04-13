#!/usr/bin/env python3
"""企业风控和经营异常 Skill - 命令行入口"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from client import EnterpriseRiskClient
from config import get_config, ConfigError
from exceptions import EnterpriseRiskError


def cmd_query(args):
    """查询企业风控信息"""
    config = get_config()
    client = EnterpriseRiskClient.from_config(config)

    result = client.query_enterprise_risk(
        enterprise_name=args.enterprise_name or "",
        credit_code=args.credit_code or "",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description="企享云企业风控和经营异常 - 根据企业名称查询企业经营异常、严重违法、重大税收违法风险等信息",
    )
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    p_query = subparsers.add_parser("query", help="查询企业风控信息")
    p_query.add_argument("--enterprise-name", help="企业全名")
    p_query.add_argument("--credit-code", help="统一社会信用代码（18位）")
    p_query.set_defaults(func=cmd_query)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        args.func(args)
    except ConfigError as e:
        print(json.dumps({"success": False, "error": "CONFIG_ERROR", "message": str(e)}, ensure_ascii=False))
        sys.exit(1)
    except EnterpriseRiskError as e:
        print(json.dumps({"success": False, "error": e.code, "message": e.message}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
