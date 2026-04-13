#!/usr/bin/env python3
"""票据查验和发票验真 Skill - 命令行入口"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from client import BillVerificationClient
from config import get_config, ConfigError
from exceptions import BillVerificationError


def cmd_validate_invoice_info(args):
    """验证发票信息格式"""
    config = get_config()
    client = BillVerificationClient.from_config(config)

    result = client.validate_invoice_info(
        invoice_type_code=args.invoice_type_code or "",
        invoice_number=args.invoice_number or "",
        billing_date=args.billing_date or "",
        amount=args.amount or "",
        check_code=args.check_code or "",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_verify_tax_control(args):
    """税控发票查验"""
    config = get_config()
    client = BillVerificationClient.from_config(config)

    cy_list = json.loads(args.cy_list)
    result = client.verify_tax_control_invoice(cy_list)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_verify_digital(args):
    """数电票查验"""
    config = get_config()
    client = BillVerificationClient.from_config(config)

    cy_list = json.loads(args.cy_list)
    result = client.verify_digital_invoice(cy_list)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_verify_digital_paper(args):
    """数电纸质发票查验"""
    config = get_config()
    client = BillVerificationClient.from_config(config)

    cy_list = json.loads(args.cy_list)
    result = client.verify_digital_paper_invoice(cy_list)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_verify_invoice(args):
    """普通发票查验"""
    config = get_config()
    client = BillVerificationClient.from_config(config)

    result = client.verify_invoice(
        invoice_type_code=args.invoice_type_code,
        invoice_number=args.invoice_number,
        billing_date=args.billing_date,
        amount=args.amount,
        check_code=args.check_code or "",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_batch_verify(args):
    """批量查验"""
    config = get_config()
    client = BillVerificationClient.from_config(config)

    cy_list = json.loads(args.cy_list)
    result = client.batch_verify_invoices(cy_list)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description="企享云票据查验和发票验真 - 通过发票四要素进行验真，返回全票面数据信息",
    )
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # validate-invoice-info
    p_validate = subparsers.add_parser("validate-invoice-info", help="验证发票信息格式")
    p_validate.add_argument("--invoice-type-code", help="发票代码")
    p_validate.add_argument("--invoice-number", help="发票号码")
    p_validate.add_argument("--billing-date", help="开票日期")
    p_validate.add_argument("--amount", help="金额")
    p_validate.add_argument("--check-code", help="校验码")
    p_validate.set_defaults(func=cmd_validate_invoice_info)

    # verify-tax-control
    p_tax = subparsers.add_parser("verify-tax-control", help="税控发票查验")
    p_tax.add_argument("--cy-list", required=True, help="发票查验列表 JSON")
    p_tax.set_defaults(func=cmd_verify_tax_control)

    # verify-digital
    p_digital = subparsers.add_parser("verify-digital", help="数电票查验")
    p_digital.add_argument("--cy-list", required=True, help="发票查验列表 JSON")
    p_digital.set_defaults(func=cmd_verify_digital)

    # verify-digital-paper
    p_paper = subparsers.add_parser("verify-digital-paper", help="数电纸质发票查验")
    p_paper.add_argument("--cy-list", required=True, help="发票查验列表 JSON")
    p_paper.set_defaults(func=cmd_verify_digital_paper)

    # verify-invoice
    p_invoice = subparsers.add_parser("verify-invoice", help="普通发票查验")
    p_invoice.add_argument("--invoice-type-code", required=True, help="发票代码")
    p_invoice.add_argument("--invoice-number", required=True, help="发票号码")
    p_invoice.add_argument("--billing-date", required=True, help="开票日期")
    p_invoice.add_argument("--amount", required=True, help="金额")
    p_invoice.add_argument("--check-code", default="", help="校验码")
    p_invoice.set_defaults(func=cmd_verify_invoice)

    # batch-verify
    p_batch = subparsers.add_parser("batch-verify", help="批量查验税控发票")
    p_batch.add_argument("--cy-list", required=True, help="发票查验列表 JSON")
    p_batch.set_defaults(func=cmd_batch_verify)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        args.func(args)
    except ConfigError as e:
        print(json.dumps({"success": False, "error": "CONFIG_ERROR", "message": str(e)}, ensure_ascii=False))
        sys.exit(1)
    except BillVerificationError as e:
        print(json.dumps({"success": False, "error": e.code, "message": e.message}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
