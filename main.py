from __future__ import annotations

import logging
from math import isfinite
from numbers import Real
from typing import Any

from table15 import (
    DATA,
    JUDGE,
    KM_TEST,
    data_outcome,
    decide,
    judge_outcome,
    normalize_color,
    outcome_from_result,
)

logger = logging.getLogger(__name__)

RESULT_SCHEMA_VERSION = "monitoring-result/v3"
ASSESSMENT_RESULT_VERSION = "laim-assessment-result.v1"
KNOWN_TESTS = ("km_test", "local_drift", "global_drift", "oos_oot")
_ALLOWED_STATUSES = {"computed", "low_confidence", "not_computable"}

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
    DATA: "Пригодность данных периода (6.3.2)",
    JUDGE: "Автоассессор: допуск и контроль (6.3.3)",
    "km_test": "Динамика ключевой метрики (6.3.4)",
    "local_drift": "Покрытие потока эталоном (6.3.7)",
    "global_drift": "Прогноз влияния сдвига (6.3.8)",
    "oos_oot": "Различимость выборок (6.3.6)",
}
_STATUS_RU = {"computed": "выполнен", "not_assessed": "не оценено", None: "отсутствует"}


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


def _parse_tests(value: Any, setting: str) -> tuple[str, ...]:
    if not isinstance(value, str):
        raise ValueError(f"{setting} должен быть строкой имён тестов через запятую")
    names = tuple(item.strip() for item in value.split(",") if item.strip())
    unknown = [name for name in names if name not in KNOWN_TESTS]
    if unknown:
        raise ValueError(f"{setting}: неизвестные тесты {unknown}")
    if len(set(names)) != len(names):
        raise ValueError(f"{setting}: имена тестов повторяются: {value!r}")
    return names


def _settings(
    expected_tests: Any, informative_tests: Any
) -> tuple[tuple[str, ...], frozenset[str]]:
    expected = _parse_tests(expected_tests, "expected_tests")
    informative = frozenset(_parse_tests(informative_tests, "informative_tests"))
    if KM_TEST not in expected:
        raise ValueError(f"expected_tests должен содержать {KM_TEST}: тест 6.3.4 обязателен")
    if KM_TEST in informative:
        raise ValueError(f"informative_tests не может содержать {KM_TEST}")
    if not informative <= set(expected):
        raise ValueError(
            f"informative_tests {sorted(informative - set(expected))} нет в expected_tests"
        )
    return expected, informative


def _index_test_results(
    values: list[Any], expected: tuple[str, ...]
) -> dict[str, dict[str, Any]]:
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
        if name not in KNOWN_TESTS:
            raise ValueError(f"laim-agg.in{position}: неизвестный test_name={name!r}")
        if name not in expected:
            raise ValueError(
                f"laim-agg.in{position}: результат {name!r} не ожидается в этом запуске"
            )
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

    km = indexed.get(KM_TEST)
    if km is not None:
        required_km = {"km_name", "km_baseline", "km_monitoring", "km_delta"}
        missing = required_km - set(km)
        if missing:
            raise ValueError(f"laim-agg: km_test не содержит поля {sorted(missing)}")
    return {name: indexed[name] for name in expected if name in indexed}


def _dot(color: str, size: int = 12) -> str:
    return (
        f"<span style='display:inline-block;width:{size}px;height:{size}px;"
        f"border-radius:50%;background:{_COLOR_HEX.get(color, '#9e9e9e')};"
        "vertical-align:middle'></span>"
    )


def _report_html(
    color: str,
    quality_status: str,
    registry: dict[str, dict[str, Any]],
    reasons: list[str],
) -> str:
    rows = "".join(
        "<tr>"
        f"<td style='padding:6px 12px'>{_TEST_LABEL[name]}</td>"
        f"<td style='padding:6px 12px'>{_STATUS_RU[item['status']]}"
        f"{' (информативный)' if item['informative'] else ''}</td>"
        f"<td style='padding:6px 12px'>{_dot(item['light'] or 'gray')}&nbsp;"
        f"{_COLOR_RU.get(item['light'], '—')}</td>"
        f"<td style='padding:6px 12px'>{item['reason'] or ''}</td></tr>"
        for name, item in registry.items()
    )
    quality = (
        "Оценка качества выполнена"
        if quality_status == "assessed"
        else "Оценка качества не выполнена"
    )
    basis = "".join(f"<li>{reason}</li>" for reason in reasons)
    return (
        "<html><body style='font-family:sans-serif;font-size:14px'>"
        "<h3>Итог итерации автомониторинга</h3>"
        f"<p>{_dot(color, 16)}&nbsp;<b>{_COLOR_RU[color]}</b> · {quality}</p>"
        + (f"<p>Основания:</p><ul>{basis}</ul>" if basis else "")
        + "<table><tr><th>Тест</th><th>Статус</th><th>Светофор</th>"
        f"<th>Основание</th></tr>{rows}</table></body></html>"
    )


def main(
    assessor_accuracy: float | None,
    assessment_result: dict,
    red_assessor_accuracy: float = 0.6,
    expected_tests: str = "km_test,local_drift,global_drift,oos_oot",
    informative_tests: str = "local_drift,global_drift,oos_oot",
    processing_report: dict | None = None,
    sample_meta: dict | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Валидирует результаты тестов и формирует monitoring-result/v3 по таблице 15."""
    expected, informative = _settings(expected_tests, informative_tests)
    if processing_report is not None and not isinstance(processing_report, dict):
        raise ValueError("processing_report должен быть объектом")
    if sample_meta is not None and not isinstance(sample_meta, dict):
        raise ValueError("sample_meta должен быть объектом")
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
    test_results = _index_test_results(raw_inputs, expected)

    data = data_outcome(processing_report)
    judge = judge_outcome(assessment, accuracy, threshold)
    outcomes = {
        name: outcome_from_result(name, test_results.get(name), name in informative)
        for name in expected
    }
    decision = decide(judge, outcomes, data)
    registry = {
        item.name: {
            "expected": True,
            "received": item.received,
            "informative": item.informative,
            "status": item.status,
            "light": item.light,
            "reason": item.reason,
        }
        for item in (data, judge, *outcomes.values())
    }
    missing_tests = [name for name in expected if name not in test_results]
    reasons = list(decision.reasons)
    logger.info(
        "[laim-agg] итог=%s оценка=%s получено=%d/%d основания=%s",
        decision.color,
        decision.quality_status,
        len(test_results),
        len(expected),
        reasons,
    )
    quality = "выполнена" if decision.quality_status == "assessed" else "не выполнена"
    title = (
        f"Итог итерации: {_COLOR_RU[decision.color].lower()}; "
        f"оценка качества {quality}"
    )
    return {
        "all_results": {
            "schema_version": RESULT_SCHEMA_VERSION,
            "calculated_traffic_lights": {
                "test_light": decision.color,
                "semaphore_title": title,
            },
            "block_name": "Результаты мониторинга",
            "color": decision.color,
            "quality_status": decision.quality_status,
            "expected_tests": list(expected),
            "informative_tests": sorted(informative),
            "missing_tests": missing_tests,
            "registry": registry,
            "reasons": reasons,
            "provenance": {
                "sample": sample_meta,
                "data_readiness": (processing_report or {}).get("data_readiness"),
            },
            "test_results": test_results,
            "assessor_accuracy": accuracy,
            "assessment_result": assessment,
            "report_html": _report_html(
                decision.color, decision.quality_status, registry, reasons
            ),
        }
    }
