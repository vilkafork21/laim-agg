"""Итог итерации автомониторинга по таблице 15 методики этапа 4.

Красный — только от теста 6.3.4 (km_test) при допущенном автоассессоре.
Жёлтый — жёлтый 6.3.4; не допущенный или не оценённый автоассессор (6.3.3);
отсутствующий или не оценённый ожидаемый результат; жёлтый или красный
дополнительного светофорного теста. Зелёный — полный реестр и зелёные
результаты всех ожидаемых светофорных тестов. Информативные тесты в цвет не
входят. Голосование и взвешивание не применяются.
"""

from __future__ import annotations

from dataclasses import dataclass

KM_TEST = "km_test"
JUDGE = "assessor"
DATA = "data_readiness"
_DATA_STATES = {
    "sufficient": ("computed", "green"),
    "limited": ("computed", "amber"),
    "insufficient": ("not_assessed", None),
    "failed": ("computed", "red"),
}
_COLOR_ALIASES = {"yellow": "amber", "grey": "gray"}
_KNOWN_COLORS = ("red", "amber", "green", "gray")


def normalize_color(value: object) -> str:
    """Цвет входа → канонический ('red'/'amber'/'green'/'gray'/'unknown')."""
    color = _COLOR_ALIASES.get(value, value)
    return color if color in _KNOWN_COLORS else "unknown"


@dataclass(frozen=True)
class Outcome:
    """Результат одного теста в терминах методики."""

    name: str
    received: bool
    informative: bool
    status: str | None = None
    light: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class Decision:
    """Итог итерации: цвет, статус оценки качества, основания."""

    color: str
    quality_status: str
    reasons: tuple[str, ...]


def outcome_from_result(name: str, result: dict | None, informative: bool) -> Outcome:
    """all_results теста (или его отсутствие) → Outcome."""
    if result is None:
        return Outcome(name, False, informative, reason="ожидаемый результат отсутствует")
    if result["status"] == "not_computable":
        reason = result.get("reason") or result.get("reason_code") or "причина не указана"
        return Outcome(name, True, informative, "not_assessed", None, str(reason))
    return Outcome(name, True, informative, "computed", normalize_color(result["color"]))


def data_outcome(report: dict | None) -> Outcome:
    """Тест 6.3.2 по processing_report.data_readiness конвертера."""
    if report is None:
        return Outcome(DATA, False, False, reason="ожидаемый результат отсутствует")
    readiness = report.get("data_readiness")
    if not isinstance(readiness, dict):
        return Outcome(
            DATA, True, False, "not_assessed", None,
            "processing_report без блока data_readiness",
        )
    state = readiness.get("state")
    if state not in _DATA_STATES:
        raise ValueError(f"processing_report.data_readiness.state={state!r} не поддерживается")
    status, light = _DATA_STATES[state]
    reason = readiness.get("reason") or readiness.get("reason_code") or "причина не указана"
    return Outcome(DATA, True, False, status, light, None if state == "sufficient" else str(reason))


def judge_outcome(assessment: dict, accuracy: float | None, threshold: float) -> Outcome:
    """Тест 6.3.3: статус допуска из calibration_metrics, иначе порог acc_auto."""
    if assessment["status"] != "computed":
        return Outcome(JUDGE, True, False, "not_assessed", None, str(assessment.get("reason")))
    calibration = assessment.get("calibration_metrics") or {}
    admission = calibration.get("admission_status")
    if admission is not None:
        reason = str(calibration.get("admission_reason") or "основание не указано")
        if admission == "not_assessed":
            return Outcome(JUDGE, True, False, "not_assessed", None, reason)
        if admission not in ("green", "amber", "red"):
            raise ValueError(
                f"assessment_result.calibration_metrics.admission_status={admission!r} "
                "не поддерживается"
            )
        return Outcome(
            JUDGE, True, False, "computed", admission,
            None if admission == "green" else reason,
        )
    if accuracy is not None and accuracy <= threshold:
        reason = f"точность автоассессора {accuracy:.3f} не выше порога {threshold:.3f}"
        return Outcome(JUDGE, True, False, "computed", "red", reason)
    return Outcome(JUDGE, True, False, "computed", "green")


def decide(judge: Outcome, tests: dict[str, Outcome], data: Outcome | None = None) -> Decision:
    """Таблица 15: приоритет красный → жёлтый → зелёный без голосования."""
    km = tests[KM_TEST]
    reasons: list[str] = []
    data_usable = True
    if data is not None:
        # Базовый тест 6.3.2: непригодные или не оценённые данные периода
        # запрещают вывод о качестве; ограничение — жёлтый итог.
        if not data.received:
            reasons.append(f"{DATA}: ожидаемый результат отсутствует")
            data_usable = False
        elif data.status != "computed":
            reasons.append(f"{DATA}: не оценено ({data.reason})")
            data_usable = False
        elif data.light == "red":
            reasons.append(f"{DATA}: данные периода непригодны ({data.reason})")
            data_usable = False
        elif data.light == "amber":
            reasons.append(f"{DATA}: ограничение данных ({data.reason})")
    judge_admitted = judge.status == "computed" and judge.light in ("green", "amber")
    if not judge_admitted:
        reasons.append(f"автоассессор не допущен: {judge.reason}")
    elif judge.light == "amber":
        # Жёлтый допуск (6.3.3): прокси-оценки применимы с усиленным контролем,
        # итог итерации не выше жёлтого, красный 6.3.4 сохраняется.
        reasons.append(f"автоассессор допущен с ограничением: {judge.reason}")
    if not km.received:
        reasons.append(f"{KM_TEST}: ожидаемый результат отсутствует")
    elif km.status != "computed":
        reasons.append(f"{KM_TEST}: не оценено ({km.reason})")
    elif not judge_admitted:
        reasons.append(
            f"{KM_TEST}: результат {km.light} не учитывается без допуска автоассессора"
        )
    assessed = data_usable and judge_admitted and km.received and km.status == "computed"
    if assessed and km.light == "red":
        reasons.append(f"{KM_TEST}: красный результат при допущенном автоассессоре")
        return Decision("red", "assessed", tuple(reasons))
    if assessed and km.light == "amber":
        reasons.append(f"{KM_TEST}: жёлтый результат")
    for name, outcome in tests.items():
        if name == KM_TEST:
            continue
        if not outcome.received:
            reasons.append(f"{name}: ожидаемый результат отсутствует")
        elif outcome.informative:
            continue
        elif outcome.status != "computed":
            reasons.append(f"{name}: не оценено ({outcome.reason})")
        elif outcome.light != "green":
            reasons.append(
                f"{name}: {outcome.light} — дополнительный тест не поднимает итог выше жёлтого"
            )
    color = "amber" if reasons else "green"
    return Decision(color, "assessed" if assessed else "not_assessed", tuple(reasons))
