#!/usr/bin/env python3
"""申报规则加载与执行能力。"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from filing_period import filing_month_label, format_period_label
from qxy_mcp_lib import extract_business_code, extract_message, extract_tax_amount


def _rules_dir() -> Path:
    """返回规则目录。"""

    return Path(__file__).resolve().parents[1] / "rules"


def _load_json_file(file_name: str) -> dict[str, Any]:
    """读取规则 JSON。"""

    path = _rules_dir() / file_name
    with path.open("r", encoding="utf-8") as file_obj:
        payload = json.load(file_obj)
    if not isinstance(payload, dict):
        raise ValueError(f"规则文件 `{file_name}` 必须是 JSON 对象。")
    return payload


def load_rule_sets() -> dict[str, Any]:
    """加载全部规则集合。"""

    return {
        "accrual_rules": _load_json_file("accrual_rules.json"),
        "response_rules": _load_json_file("response_rules.json"),
        "tax_burden_rules": _load_json_file("tax_burden_rules.json"),
        "tax_code_catalog": _load_json_file("tax_code_catalog.json"),
    }


def get_tax_code_entry(rule_sets: dict[str, Any], tax_code: str | None) -> dict[str, Any] | None:
    """按税种代码返回附录定义。"""

    if not tax_code:
        return None
    for item in rule_sets["tax_code_catalog"].get("tax_codes", []):
        if str(item.get("code")) == str(tax_code):
            return item
    return None


def get_tax_code_label(rule_sets: dict[str, Any], tax_code: str | None) -> str | None:
    """返回税种简称。"""

    entry = get_tax_code_entry(rule_sets, tax_code)
    if not entry:
        return None
    label = str(entry.get("short_name") or entry.get("name") or "").strip()
    return label or None


def classify_tax_codes(rule_sets: dict[str, Any], tax_codes: list[str]) -> list[dict[str, Any]]:
    """返回税种代码的结构化分类结果。"""

    result: list[dict[str, Any]] = []
    for tax_code in tax_codes:
        entry = get_tax_code_entry(rule_sets, tax_code)
        if entry is None:
            result.append(
                {
                    "code": str(tax_code),
                    "name": None,
                    "short_name": None,
                    "category": "unknown",
                    "supported_init": False,
                    "unsupported_reason": "附录中未识别该税种代码，需补充码表。",
                }
            )
            continue
        result.append(
            {
                "code": str(entry.get("code")),
                "name": entry.get("name"),
                "short_name": entry.get("short_name"),
                "category": entry.get("category"),
                "supported_init": bool(entry.get("supported_init", False)),
                "unsupported_reason": entry.get("unsupported_reason"),
            }
        )
    return result


def validate_init_tax_codes(rule_sets: dict[str, Any], zsxm_list: list[dict[str, Any]]) -> list[str]:
    """校验初始化税种是否在支持矩阵内。"""

    unsupported_messages: list[str] = []
    for item in zsxm_list:
        tax_code = str(item.get("yzpzzlDm") or "").strip()
        if not tax_code:
            continue
        entry = get_tax_code_entry(rule_sets, tax_code)
        if entry is None:
            unsupported_messages.append(f"{tax_code}: 附录中未识别该税种代码，当前无法初始化。")
            continue
        if not entry.get("supported_init", False):
            label = str(entry.get("short_name") or entry.get("name") or tax_code)
            reason = str(entry.get("unsupported_reason") or "当前不支持初始化。")
            unsupported_messages.append(f"{tax_code} {label}: {reason}")
    return unsupported_messages


def _to_float(value: Any) -> float | None:
    """安全转换数值。"""

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


def _collect_entries(node: Any, row_context: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """扁平化初始化数据中的数值项。"""

    entries: list[dict[str, Any]] = []
    current_context = dict(row_context or {})
    if isinstance(node, dict):
        local_context = dict(current_context)
        for marker in ("ewblxh", "rowLc", "ewbhxh"):
            marker_value = node.get(marker)
            if marker_value not in (None, ""):
                local_context[marker] = str(marker_value)
        for key, value in node.items():
            if isinstance(value, (dict, list)):
                entries.extend(_collect_entries(value, local_context))
                continue
            numeric_value = _to_float(value)
            if numeric_value is None:
                continue
            entries.append(
                {
                    "field": str(key),
                    "value": numeric_value,
                    "row_context": local_context,
                }
            )
    elif isinstance(node, list):
        for item in node:
            entries.extend(_collect_entries(item, current_context))
    return entries


def _matches_row_filters(entry: dict[str, Any], row_filters: dict[str, list[str]] | None) -> bool:
    """判断条目是否命中行过滤条件。"""

    if not row_filters:
        return True
    context = entry.get("row_context", {})
    for key, values in row_filters.items():
        if str(context.get(key, "")) not in {str(item) for item in values}:
            return False
    return True


def _sum_fields(entries: list[dict[str, Any]], fields: list[str], row_filters: dict[str, list[str]] | None) -> float:
    """在条目集合中按字段求和。"""

    expected_fields = set(fields)
    total = 0.0
    for entry in entries:
        if entry["field"] not in expected_fields:
            continue
        if not _matches_row_filters(entry, row_filters):
            continue
        total += float(entry["value"])
    return total


def _walk_named_values(node: Any, items: list[tuple[str, float]]) -> None:
    """从财报结构中提取 name/value 形式数据。"""

    if isinstance(node, dict):
        name = node.get("name")
        for value_key in ("value", "value2", "value1"):
            value = node.get(value_key)
            numeric_value = _to_float(value)
            if name and numeric_value is not None:
                items.append((f"{value_key}:{name}", numeric_value))
                items.append((str(name), numeric_value))
        for value in node.values():
            _walk_named_values(value, items)
    elif isinstance(node, list):
        for item in node:
            _walk_named_values(item, items)


def _extract_named_values(payload: dict[str, Any]) -> dict[str, float]:
    """提取财报中的 name/value 映射。"""

    pairs: list[tuple[str, float]] = []
    _walk_named_values(payload, pairs)
    result: dict[str, float] = {}
    for key, value in pairs:
        result[key] = result.get(key, 0.0) + value
    return result


def _find_profile(rule_sets: dict[str, Any], tax_codes: list[str]) -> dict[str, Any] | None:
    """根据税种匹配计提规则。"""

    for profile in rule_sets["accrual_rules"].get("profiles", []):
        matched_codes = {str(item) for item in profile.get("match_tax_codes", [])}
        if matched_codes.intersection({str(code) for code in tax_codes}):
            return profile
    return None


def _set_dotted_path(payload: dict[str, Any], dotted_path: str, value: Any) -> None:
    """按点路径写入对象。"""

    parts = [item for item in dotted_path.split(".") if item]
    current = payload
    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value


def evaluate_tax_burden(
    metrics: dict[str, float],
    config: dict[str, Any],
    rule_sets: dict[str, Any],
) -> dict[str, Any]:
    """计算行业税负率并判定是否超阈值。"""

    industry_name = str(config.get("rules", {}).get("industry_name") or "").strip()
    if not industry_name:
        return {
            "enabled": False,
            "matched": False,
            "blocked": False,
        }

    actual_rate = metrics.get("tax_burden_rate")
    if actual_rate is None:
        return {
            "enabled": True,
            "matched": False,
            "blocked": False,
            "message": "当前规则未能计算出税负率。",
        }

    for industry in rule_sets["tax_burden_rules"].get("industries", []):
        if str(industry.get("industry_name", "")).strip() != industry_name:
            continue
        min_rate = float(industry["min_rate"])
        max_rate = float(industry["max_rate"])
        status = "normal"
        if actual_rate < min_rate:
            status = "low"
        elif actual_rate > max_rate:
            status = "high"
        blocked = bool(config.get("rules", {}).get("tax_burden_blocking")) and status != "normal"
        return {
            "enabled": True,
            "matched": True,
            "industry_name": industry_name,
            "category": industry.get("category"),
            "actual_rate": round(actual_rate, 4),
            "min_rate": min_rate,
            "max_rate": max_rate,
            "status": status,
            "blocked": blocked,
            "analysis": industry.get("analysis"),
        }

    return {
        "enabled": True,
        "matched": False,
        "blocked": False,
        "industry_name": industry_name,
        "message": "未在规则库中找到该行业对应的税负率区间。",
    }


def apply_accrual_rules(
    *,
    config: dict[str, Any],
    init_queries: list[dict[str, Any]] | None,
    tax_data: dict[str, Any] | None,
    financial_data: dict[str, Any] | None = None,
    rule_sets: dict[str, Any],
) -> dict[str, Any]:
    """应用计提规则，生成建议值和可选的报文补丁。"""

    queries = list(init_queries or [])
    tax_codes = [str(item.get("yzpzzlDm", "")) for item in queries if item.get("yzpzzlDm")]
    profile = _find_profile(rule_sets, tax_codes)
    if not profile:
        return {
            "matched": False,
            "findings": [],
            "metrics": {},
            "patched_tax_data": copy.deepcopy(tax_data or {}),
        }

    metrics: dict[str, float] = {}
    findings: list[dict[str, Any]] = []
    init_payload = {}
    for query in queries:
        result = query.get("result")
        if isinstance(result, dict):
            data = result.get("data")
            if isinstance(data, dict) and isinstance(data.get("initData"), dict):
                init_payload = data["initData"]
                break

    if profile["id"] == "eit_quarterly":
        named_values = _extract_named_values(financial_data or {})
        for metric in profile.get("financial_metrics", []):
            total = 0.0
            for key in metric.get("sum_keys", []):
                total += named_values.get(key, 0.0)
            metrics[metric["id"]] = round(total, 2)
        loss_key = profile.get("init_metric_keys", {}).get("loss_compensation")
        loss_amount = None
        if loss_key:
            entries = _collect_entries(init_payload)
            for entry in entries:
                if entry["field"] == loss_key:
                    loss_amount = entry["value"]
                    break
        profit_total = metrics.get("profit_total", 0.0)
        base_amount = max(profit_total - float(loss_amount or 0.0), 0.0)
        metrics["estimated_income_tax"] = round(base_amount * 0.25, 2)
    else:
        entries = _collect_entries(init_payload)
        for metric in profile.get("metrics", []):
            metrics[metric["id"]] = round(
                _sum_fields(entries, metric.get("sum_fields", []), metric.get("row_filters")),
                2,
            )
        for metric in profile.get("derived_metrics", []):
            if metric.get("formula") == "percentage":
                numerator = metrics.get(metric["numerator_metric_id"], 0.0)
                denominator = metrics.get(metric["denominator_metric_id"], 0.0)
                metrics[metric["id"]] = round((numerator / denominator) * 100, 4) if denominator else 0.0
            else:
                metrics[metric["id"]] = round(
                    sum(metrics.get(item, 0.0) for item in metric.get("sum_metric_ids", [])),
                    2,
                )

    for metric_id, value in metrics.items():
        findings.append(
            {
                "metric_id": metric_id,
                "value": value,
            }
        )

    patched_tax_data = copy.deepcopy(tax_data or {})
    if config.get("rules", {}).get("accrual_mode") == "auto_patch_payload":
        for metric_id, target_path in profile.get("patch_map", {}).items():
            if metric_id in metrics:
                _set_dotted_path(patched_tax_data, target_path, metrics[metric_id])

    tax_burden_result = evaluate_tax_burden(metrics, config, rule_sets)
    return {
        "matched": True,
        "profile_id": profile["id"],
        "profile_label": profile.get("label"),
        "metrics": metrics,
        "findings": findings,
        "tax_burden": tax_burden_result,
        "patched_tax_data": patched_tax_data,
    }


def _render_customer_message(
    rule: dict[str, Any],
    tax_amount: float | None,
    tax_label: str,
    period_label: str,
) -> str | None:
    """按模板生成客户话术。"""

    templates = rule.get("customer_message_templates")
    if isinstance(templates, dict):
        if tax_amount is not None and tax_amount > float(rule.get("payment_threshold", 1.0)):
            template = templates.get("payment_required")
        else:
            template = templates.get("no_payment")
        if template:
            return template.format(
                tax_label=tax_label,
                period_label=period_label,
                tax_amount=f"{tax_amount:.2f}" if tax_amount is not None else "0.00",
            )
    template = rule.get("customer_message_template")
    if isinstance(template, str) and template:
        return template.format(
            tax_label=tax_label,
            period_label=period_label,
            tax_amount=f"{tax_amount:.2f}" if tax_amount is not None else "0.00",
        )
    return None


def _collect_period_pairs(node: Any) -> list[tuple[str, str]]:
    """递归提取报文中的所属期起止。"""

    pairs: list[tuple[str, str]] = []
    if isinstance(node, dict):
        candidate_keys = (
            ("ssqQ", "ssqZ"),
            ("fromDate", "toDate"),
            ("skssqq", "skssqz"),
        )
        for start_key, end_key in candidate_keys:
            start_value = node.get(start_key)
            end_value = node.get(end_key)
            if isinstance(start_value, str) and start_value and isinstance(end_value, str) and end_value:
                pairs.append((start_value, end_value))
        for value in node.values():
            pairs.extend(_collect_period_pairs(value))
    elif isinstance(node, list):
        for item in node:
            pairs.extend(_collect_period_pairs(item))
    return pairs


def _first_unique_period_label(*nodes: Any) -> str | None:
    """从多个节点中提取唯一所属期标签。"""

    unique_pairs = {
        (start_date, end_date)
        for node in nodes
        for start_date, end_date in _collect_period_pairs(node)
    }
    if len(unique_pairs) != 1:
        return None
    start_date, end_date = next(iter(unique_pairs))
    return format_period_label(start_date, end_date)


def _resolve_period_label(payload: Any, step_name: str, step_cfg: dict[str, Any], config: dict[str, Any]) -> str:
    """解析对客话术中的所属期标签。"""

    init_step_cfg = config.get("steps", {}).get("init_data", {})
    period_label = _first_unique_period_label(payload, step_cfg)
    if period_label:
        return period_label
    if step_name in {"tax_report", "financial_report"}:
        period_label = _first_unique_period_label(init_step_cfg)
        if period_label:
            return period_label
    return f"申报月份 {filing_month_label(config['year'], config['period'])}"


def match_response_rule(
    *,
    payload: Any,
    step_name: str,
    step_cfg: dict[str, Any],
    config: dict[str, Any],
    rule_sets: dict[str, Any],
    tax_label: str = "税种",
) -> dict[str, Any]:
    """根据返回报文匹配处理规则。"""

    code = extract_business_code(payload)
    message = extract_message(payload)
    tax_amount = extract_tax_amount(payload)
    period_label = _resolve_period_label(payload, step_name, step_cfg, config)

    for rule in rule_sets["response_rules"].get("rules", []):
        step_names = rule.get("step_names")
        if step_names and step_name not in step_names:
            continue
        codes = {str(item) for item in rule.get("codes", [])}
        keywords = [str(item) for item in rule.get("message_keywords", [])]
        matched = False
        if codes and code in codes:
            matched = True
        if keywords and any(keyword in message for keyword in keywords):
            matched = True
        if not matched:
            continue

        normalized_status = rule.get("normalized_status", "unknown")
        if (
            rule.get("id") == "compare_failed"
            and config.get("rules", {}).get("allow_force_declare_on_4300")
        ):
            normalized_status = "success"

        customer_message = _render_customer_message(rule, tax_amount, tax_label, period_label)
        payment_required = (
            tax_amount is not None and tax_amount > float(rule.get("payment_threshold", 1.0))
        )
        return {
            "matched": True,
            "rule_match_id": rule["id"],
            "normalized_status": normalized_status,
            "business_code": code,
            "business_message": message,
            "retryable": bool(rule.get("retryable", False)),
            "customer_visible": bool(rule.get("customer_visible", False)),
            "customer_message": customer_message,
            "payment_required": payment_required,
            "payment_action": "confirm_payment" if payment_required else None,
            "pdf_action": "download_pdf" if normalized_status == "success" and not payment_required else None,
            "operator_advice": rule.get("operator_advice"),
            "raw_response": payload,
        }

    return {
        "matched": False,
        "rule_match_id": None,
        "normalized_status": "unknown",
        "business_code": code,
        "business_message": message,
        "retryable": False,
        "customer_visible": False,
        "customer_message": None,
        "payment_required": False,
        "payment_action": None,
        "pdf_action": None,
        "operator_advice": None,
        "raw_response": payload,
    }
