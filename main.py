from __future__ import annotations

import logging
from math import isfinite
from numbers import Real
from typing import Any

from aggregator import aggregate, count_colors, normalize_color

logger = logging.getLogger(__name__)

RESULT_SCHEMA_VERSION = "monitoring-result/v2"
ASSESSMENT_RESULT_VERSION = "laim-assessment-result.v1"
EXPECTED_TESTS = ("km_test", "local_drift", "global_drift", "oos_oot")
_ALLOWED_STATUSES = {"computed", "low_confidence", "not_computable"}

_COLOR_DATIVE = {
    "red": "красному",
    "amber": "жёлтому",
    "green": "зелёному",
    "gray": "серому",
}
_COLOR_RU = {
    "red": "Красный",
    "amber": "Жёлтый",
    "green": "Зелёный",
    "gray": "Серый",
}
_COLOR_HEX = {
    "red": "#e53935",
    "amber": "#fb8c00",
    "green": "#43a047",
    "gray": "#9e9e9e",
}
_TEST_LABEL = {
    "km_test": "Динамика ключевой метрики",
    "local_drift": "Локальный дрифт запросов",
    "global_drift": "Глобальный дрифт запросов",
    "oos_oot": "Разделение выборок (OOS/OOT)",
}


def _validate_accuracy(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError("assessor_accuracy должен быть числом в диапазоне 0..1")
    accuracy = float(value)
    if not isfinite(accuracy) or not 0 <= accuracy <= 1:
        raise ValueError("assessor_accuracy должен быть числом в диапазоне 0..1")
    return accuracy


def _validate_assessment_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("assessment_result должен быть объектом")
    result = dict(value)
    if result.get("contract_version") != ASSESSMENT_RESULT_VERSION:
        raise ValueError(
            "assessment_result имеет неподдерживаемый contract_version"
        )
    status = result.get("status")
    if status not in {"computed", "not_computable"}:
        raise ValueError(
            "assessment_result.status должен быть computed или not_computable"
        )
    if status == "not_computable":
        if not str(result.get("reason") or "").strip():
            raise ValueError("assessment_result.status=not_computable требует reason")
        return result

    total = result.get("total_units")
    scored = result.get("scored_units")
    if (
        isinstance(total, bool)
        or not isinstance(total, int)
        or total < 1
        or isinstance(scored, bool)
        or not isinstance(scored, int)
        or not 1 <= scored <= total
    ):
        raise ValueError(
            "assessment_result computed требует 1 <= scored_units <= total_units"
        )
    return result


def _index_test_results(values: list[Any]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    required = {"test_name", "status", "color", "calculated_traffic_lights"}
    for position, value in enumerate(values):
        if not isinstance(value, dict):
            raise ValueError(f"laim-agg.in{position} должен быть объектом")
        missing = required - set(value)
        if missing:
            raise ValueError(
                f"laim-agg.in{position} не содержит поля {sorted(missing)}"
            )
        name = value["test_name"]
        if name not in EXPECTED_TESTS:
            raise ValueError(f"laim-agg.in{position}: неизвестный test_name={name!r}")
        if name in indexed:
            raise ValueError(f"laim-agg: результат {name!r} подключён повторно")

        status = value["status"]
        if status not in _ALLOWED_STATUSES:
            raise ValueError(f"laim-agg: {name}.status={status!r} не поддерживается")
        lights = value["calculated_traffic_lights"]
        if not isinstance(lights, dict):
            raise ValueError(
                f"laim-agg: {name}.calculated_traffic_lights должен быть объектом"
            )
        color = normalize_color(value["color"])
        light = normalize_color(lights.get("test_light"))
        if color == "unknown" or light == "unknown" or color != light:
            raise ValueError(
                f"laim-agg: {name} имеет несогласованные цвета: "
                f"color={value['color']!r}, test_light={lights.get('test_light')!r}"
            )
        if (status == "not_computable") != (color == "gray"):
            raise ValueError(
                f"laim-agg: {name} имеет несогласованные status/color: "
                f"status={status!r}, color={color!r}"
            )
        record = dict(value)
        record["color"] = color
        record["calculated_traffic_lights"] = dict(lights, test_light=color)
        indexed[name] = record

    km = indexed.get("km_test")
    if km is not None:
        required_km = {"km_name", "km_baseline", "km_monitoring", "km_delta"}
        missing = required_km - set(km)
        if missing:
            raise ValueError(f"laim-agg: km_test не содержит поля {sorted(missing)}")
    return {name: indexed[name] for name in EXPECTED_TESTS if name in indexed}


def _dot(color: str, size: int = 12) -> str:
    return (
        f"<span style='display:inline-block;width:{size}px;height:{size}px;"
        f"border-radius:50%;background:{_COLOR_HEX.get(color, '#9e9e9e')};"
        "vertical-align:middle'></span>"
    )


def _report_html(
    color: str,
    inputs: list[dict[str, Any]],
    gate_reasons: list[str],
    missing_tests: list[str],
) -> str:
    counts = count_colors(inputs)
    rows = "".join(
        "<tr>"
        f"<td style='padding:6px 12px'>{_TEST_LABEL[item['test_name']]}</td>"
        f"<td style='padding:6px 12px'>{_dot(item['color'])}&nbsp;"
        f"{_COLOR_RU[item['color']]}</td></tr>"
        for item in inputs
    )
    gates = "".join(
        f"<p style='color:#b71c1c;margin:6px 0'>{reason}</p>"
        for reason in gate_reasons
    )
    missing = (
        "<p>Не подключены: "
        + ", ".join(_TEST_LABEL[name] for name in missing_tests)
        + ".</p>"
        if missing_tests
        else ""
    )
    return (
        "<html><body style='font-family:sans-serif;font-size:14px'>"
        "<h3>Итоговый светофор мониторинга</h3>"
        f"<p>{_dot(color, 16)}&nbsp;<b>{_COLOR_RU[color]}</b></p>"
        f"<p>red={counts['red']}, amber={counts['amber']}, "
        f"green={counts['green']}, gray={counts['gray']}</p>"
        f"{gates}{missing}<table>{rows}</table></body></html>"
    )


def main(
    assessor_accuracy: float | None,
    assessment_result: dict,
    red_assessor_accuracy: float = 0.6,
    **kwargs: Any,
) -> dict[str, Any]:
    """Валидирует результаты тестов и формирует monitoring-result/v2."""
    assessment = _validate_assessment_result(assessment_result)
    accuracy = (
        None
        if assessor_accuracy is None and assessment["status"] == "not_computable"
        else _validate_accuracy(assessor_accuracy)
    )
    threshold = _validate_accuracy(red_assessor_accuracy)
    raw_inputs = [
        value for key, value in sorted(kwargs.items()) if key.startswith("in")
    ]
    test_results = _index_test_results(raw_inputs)
    inputs = list(test_results.values())
    missing_tests = [name for name in EXPECTED_TESTS if name not in test_results]

    color = aggregate(inputs, critical_red=1, critical_amber=1)
    gate_reasons: list[str] = []
    coverage_gate = bool(missing_tests and color == "green")
    accuracy_gate = bool(
        accuracy is not None and accuracy <= threshold and color == "green"
    )
    assessment_gate = bool(
        assessment["status"] != "computed" and color == "green"
    )
    km = test_results.get("km_test")
    key_metric_gate = bool(
        (km is None or km["status"] != "computed") and color == "green"
    )
    if coverage_gate:
        gate_reasons.append("Неполный реестр обязательных тестов")
    if accuracy_gate:
        gate_reasons.append(
            f"Точность автоассессора {accuracy:.3f} не выше порога {threshold:.3f}"
        )
    if assessment_gate:
        gate_reasons.append("Оценивание monitoring-выборки не выполнено")
    if key_metric_gate:
        gate_reasons.append("Ключевая метрика monitoring-выборки не вычислена")
    if gate_reasons and color == "green":
        color = "gray"

    logger.info(
        "laim-agg: tests=%d missing=%s color=%s gates=%s",
        len(inputs),
        missing_tests,
        color,
        gate_reasons,
    )
    title = f"Результат мониторинга соответствует {_COLOR_DATIVE[color]} светофору"
    return {
        "all_results": {
            "schema_version": RESULT_SCHEMA_VERSION,
            "calculated_traffic_lights": {
                "test_light": color,
                "semaphore_title": title,
            },
            "block_name": "Результаты мониторинга",
            "color": color,
            "expected_tests": list(EXPECTED_TESTS),
            "missing_tests": missing_tests,
            "test_results": test_results,
            "color_counts": count_colors(inputs),
            "assessor_accuracy": accuracy,
            "assessment_result": assessment,
            "coverage_gate_applied": coverage_gate,
            "assessor_accuracy_gate_applied": accuracy_gate,
            "assessment_gate_applied": assessment_gate,
            "key_metric_gate_applied": key_metric_gate,
            "gate_reasons": gate_reasons,
            "report_html": _report_html(
                color, inputs, gate_reasons, missing_tests
            ),
        }
    }
