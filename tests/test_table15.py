"""Таблица 15 методики этапа 4: итог итерации из результатов тестов."""

import importlib.util
import itertools
import sys
from pathlib import Path

import pytest

MODULE_DIR = Path(__file__).resolve().parents[1]


def _load(name):
    sys.path.insert(0, str(MODULE_DIR))
    try:
        spec = importlib.util.spec_from_file_location(f"laim_agg_{name}", MODULE_DIR / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        # dataclass со строковыми аннотациями ищет модуль в sys.modules.
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


t15 = _load("table15")


def _result(color="green", status="computed", reason=None):
    return {"test_name": "x", "status": status, "color": color, "reason": reason}


def _outcome(name, kind, informative=False):
    """kind: green | amber | red | not_assessed | missing."""
    if kind == "missing":
        return t15.outcome_from_result(name, None, informative)
    if kind == "not_assessed":
        return t15.outcome_from_result(
            name, _result("gray", "not_computable", "мало данных"), informative
        )
    return t15.outcome_from_result(name, _result(kind), informative)


def _judge(kind):
    """kind: admitted | amber | red | not_assessed."""
    if kind == "not_assessed":
        return t15.judge_outcome(
            {"status": "not_computable", "reason": "оценивание не выполнено"}, None, 0.6
        )
    if kind == "amber":
        return t15.judge_outcome(
            {"status": "computed", "calibration_metrics": {
                "admission_status": "amber", "admission_reason": "каппа ниже порога"}},
            0.9, 0.6,
        )
    accuracy = 0.9 if kind == "admitted" else 0.5
    return t15.judge_outcome({"status": "computed"}, accuracy, 0.6)


def test_admission_status_overrides_accuracy_threshold():
    admitted = t15.judge_outcome(
        {"status": "computed", "calibration_metrics": {"admission_status": "green"}}, 0.3, 0.6
    )
    assert admitted.light == "green" and admitted.reason is None
    refused = t15.judge_outcome(
        {"status": "computed", "calibration_metrics": {
            "admission_status": "red", "admission_reason": "судья не лучше моды"}},
        0.95, 0.6,
    )
    assert refused.light == "red" and refused.reason == "судья не лучше моды"
    pending = t15.judge_outcome(
        {"status": "computed", "calibration_metrics": {
            "admission_status": "not_assessed", "admission_reason": "holdout мал"}},
        0.95, 0.6,
    )
    assert pending.status == "not_assessed" and pending.reason == "holdout мал"
    with pytest.raises(ValueError, match="admission_status"):
        t15.judge_outcome(
            {"status": "computed", "calibration_metrics": {"admission_status": "blue"}}, 0.9, 0.6
        )


def test_amber_admission_caps_green_but_keeps_red():
    tests = {"km_test": _outcome("km_test", "green")}
    capped = t15.decide(_judge("amber"), tests)
    assert capped.color == "amber" and capped.quality_status == "assessed"
    assert capped.reasons == ("автоассессор допущен с ограничением: каппа ниже порога",)
    tests = {"km_test": _outcome("km_test", "red")}
    assert t15.decide(_judge("amber"), tests).color == "red"


def test_outcome_from_missing_result():
    outcome = t15.outcome_from_result("oos_oot", None, informative=True)
    assert outcome.received is False
    assert outcome.status is None and outcome.light is None
    assert outcome.reason == "ожидаемый результат отсутствует"


def test_outcome_from_not_computable_keeps_reason():
    outcome = t15.outcome_from_result(
        "km_test", _result("gray", "not_computable", "нет базового значения"), False
    )
    assert outcome.status == "not_assessed"
    assert outcome.light is None
    assert outcome.reason == "нет базового значения"


def test_outcome_from_computed_normalizes_color():
    outcome = t15.outcome_from_result("local_drift", _result("yellow"), True)
    assert outcome.status == "computed" and outcome.light == "amber"
    assert outcome.informative is True


def test_judge_admitted_only_above_threshold():
    assert _judge("admitted").light == "green"
    red = _judge("red")
    assert red.light == "red" and "0.500" in red.reason and "0.600" in red.reason
    absent = _judge("not_assessed")
    assert absent.status == "not_assessed" and absent.reason == "оценивание не выполнено"


def test_red_only_from_km_with_admitted_judge():
    tests = {"km_test": _outcome("km_test", "red"), "oos_oot": _outcome("oos_oot", "green", True)}
    decision = t15.decide(_judge("admitted"), tests)
    assert decision.color == "red" and decision.quality_status == "assessed"


def test_km_red_without_judge_admission_is_amber_not_assessed():
    tests = {"km_test": _outcome("km_test", "red")}
    decision = t15.decide(_judge("red"), tests)
    assert decision.color == "amber" and decision.quality_status == "not_assessed"
    assert any("не допущен" in reason for reason in decision.reasons)


def test_informative_red_does_not_change_green():
    tests = {
        "km_test": _outcome("km_test", "green"),
        "oos_oot": _outcome("oos_oot", "red", informative=True),
        "local_drift": _outcome("local_drift", "not_assessed", informative=True),
    }
    decision = t15.decide(_judge("admitted"), tests)
    assert decision.color == "green" and decision.reasons == ()


def test_missing_expected_test_is_amber_even_if_informative():
    tests = {"km_test": _outcome("km_test", "green"), "global_drift": _outcome("global_drift", "missing", True)}
    decision = t15.decide(_judge("admitted"), tests)
    assert decision.color == "amber" and decision.quality_status == "assessed"
    assert decision.reasons == ("global_drift: ожидаемый результат отсутствует",)


def test_light_forming_additional_test_is_capped_at_amber():
    tests = {"km_test": _outcome("km_test", "green"), "oos_oot": _outcome("oos_oot", "red", False)}
    decision = t15.decide(_judge("admitted"), tests)
    assert decision.color == "amber"
    assert decision.reasons == ("oos_oot: red — дополнительный тест не поднимает итог выше жёлтого",)


def test_km_not_assessed_is_amber_and_quality_not_assessed():
    tests = {"km_test": _outcome("km_test", "not_assessed")}
    decision = t15.decide(_judge("admitted"), tests)
    assert decision.color == "amber" and decision.quality_status == "not_assessed"
    assert decision.reasons == ("km_test: не оценено (мало данных)",)


KINDS = ("green", "amber", "red", "not_assessed", "missing")


@pytest.mark.parametrize(
    "judge_kind, km_kind, extra_kind, info_kind",
    list(itertools.product(("admitted", "amber", "red", "not_assessed"), KINDS, KINDS, KINDS)),
)
def test_truth_table_matches_table15(judge_kind, km_kind, extra_kind, info_kind):
    judge = _judge(judge_kind)
    tests = {
        "km_test": _outcome("km_test", km_kind),
        "oos_oot": _outcome("oos_oot", extra_kind, informative=False),
        "local_drift": _outcome("local_drift", info_kind, informative=True),
    }
    decision = t15.decide(judge, tests)

    admitted = judge_kind in ("admitted", "amber")
    km_computed = km_kind in ("green", "amber", "red")
    assessed = admitted and km_computed
    assert (decision.quality_status == "assessed") == assessed
    # Красный — только подтверждённый красный 6.3.4 при допущенном (хотя бы жёлтом) судье.
    assert (decision.color == "red") == (assessed and km_kind == "red")
    # Зелёный — только полный реестр, зелёный допуск и зелёные светофорные результаты.
    expected_green = (
        judge_kind == "admitted" and km_kind == "green" and extra_kind == "green"
        and info_kind != "missing"
    )
    assert (decision.color == "green") == expected_green
    assert decision.color in ("red", "amber", "green")
    assert (decision.color == "green") == (decision.reasons == ())
