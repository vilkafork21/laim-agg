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


def judge_outcome(assessment: dict, accuracy: float | None, threshold: float) -> Outcome:
    """Тест 6.3.3 по assessment_result и acc_auto: допущен, не допущен, не оценён."""
    if assessment["status"] != "computed":
        return Outcome(JUDGE, True, False, "not_assessed", None, str(assessment.get("reason")))
    if accuracy is not None and accuracy <= threshold:
        reason = f"точность автоассессора {accuracy:.3f} не выше порога {threshold:.3f}"
        return Outcome(JUDGE, True, False, "computed", "red", reason)
    return Outcome(JUDGE, True, False, "computed", "green")


def decide(judge: Outcome, tests: dict[str, Outcome]) -> Decision:
    """Таблица 15: приоритет красный → жёлтый → зелёный без голосования."""
    km = tests[KM_TEST]
    reasons: list[str] = []
    judge_admitted = judge.status == "computed" and judge.light == "green"
    if not judge_admitted:
        reasons.append(f"автоассессор не допущен: {judge.reason}")
    if not km.received:
        reasons.append(f"{KM_TEST}: ожидаемый результат отсутствует")
    elif km.status != "computed":
        reasons.append(f"{KM_TEST}: не оценено ({km.reason})")
    elif not judge_admitted:
        reasons.append(
            f"{KM_TEST}: результат {km.light} не учитывается без допуска автоассессора"
        )
    assessed = judge_admitted and km.received and km.status == "computed"
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
