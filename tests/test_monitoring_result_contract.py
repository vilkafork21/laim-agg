"""Контракт monitoring-result/v2 на выходе laim-agg."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest


MODULE_DIR = Path(__file__).resolve().parents[1]


def _load_main():
    sys.path.insert(0, str(MODULE_DIR))
    try:
        spec = importlib.util.spec_from_file_location("laim_agg_contract", MODULE_DIR / "main.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


agg = _load_main()


def _common(test_name, color="green", status="computed"):
    return {
        "test_name": test_name,
        "color": color,
        "status": status,
        "calculated_traffic_lights": {
            "test_light": color,
            "semaphore_title": f"{test_name}: {color}",
        },
    }


def _results():
    return {
        "km_test": {
            **_common("km_test"),
            "reason": "КМ в норме",
            "km_name": "target",
            "km_baseline": 0.8,
            "km_monitoring": 0.75,
            "km_delta": 0.0625,
            "n_scored": 100,
            "n_valid": 100,
            "invalid_share": 0.0,
            "thresholds": {"green": 0.15, "red": 0.25},
            "min_valid_rows": 100,
        },
        "local_drift": {
            **_common("local_drift"),
            "metric_value": 0.8,
            "metric_value_estimate": 0.78,
            "drop_estimate": 0.02,
            "reliability_mean": 0.9,
            "share_uncovered": 0.0,
            "n_oos": 100,
            "n_oot": 100,
            "n_closest": 5,
        },
        "global_drift": {
            **_common("global_drift"),
            "reason": "дрифт не обнаружен",
            "metric_value": 0.8,
            "metric_value_estimate": 0.79,
            "estimate_source": "regression",
            "n_selected_features": 2,
            "selected_features": ["f1", "f2"],
            "n_chunks": 10,
        },
        "oos_oot": {
            **_common("oos_oot"),
            "reason": "выборки неразличимы",
            "gini_mean": 0.1,
            "gini_std": 0.02,
            "gini_spread_lower": 0.08,
            "gini_spread_upper": 0.12,
            "resampling_iterations": 10,
            "n_oos": 100,
            "n_oot": 100,
        },
    }


def _assessment_result(status="computed"):
    result = {
        "contract_version": "laim-assessment-result.v1",
        "status": status,
        "assessment_mode": "dialogue",
        "total_units": 1,
        "scored_units": 1 if status == "computed" else 0,
    }
    if status == "not_computable":
        result["reason"] = "judge unavailable"
    return result


def _report(state="sufficient", reason=None):
    return {"status": "complete", "data_readiness": {"state": state, "reason": reason}}


def _aggregate(tests, **kwargs):
    return agg.main(
        assessor_accuracy=kwargs.pop("assessor_accuracy", 0.9),
        assessment_result=kwargs.pop("assessment_result", _assessment_result()),
        processing_report=kwargs.pop("processing_report", _report()),
        **{f"in{index}": value for index, value in enumerate(tests.values())},
        **kwargs,
    )


def test_schema_v3_and_registry_fields():
    tests = _results()
    result = _aggregate(tests)["all_results"]

    assert result["schema_version"] == "monitoring-result/v3"
    assert result["color"] == "green"
    assert result["quality_status"] == "assessed"
    assert result["reasons"] == []
    assert result["expected_tests"] == ["km_test", "local_drift", "global_drift", "oos_oot"]
    assert result["informative_tests"] == ["global_drift", "local_drift", "oos_oot"]
    assert result["missing_tests"] == []
    assert result["test_results"] == tests
    assert result["assessor_accuracy"] == 0.9
    assert list(result["registry"]) == [
        "data_readiness", "assessor", "km_test", "local_drift", "global_drift", "oos_oot",
    ]
    assert result["registry"]["data_readiness"]["light"] == "green"
    assert result["provenance"] == {"sample": None, "data_readiness": {"state": "sufficient", "reason": None}}
    assert result["registry"]["assessor"] == {
        "expected": True, "received": True, "informative": False,
        "status": "computed", "light": "green", "reason": None,
    }
    assert result["registry"]["oos_oot"]["informative"] is True
    for key in ("gate_reasons", "color_counts", "coverage_gate_applied"):
        assert key not in result


def test_informative_red_does_not_change_light():
    results = _results()
    results["oos_oot"] = {**results["oos_oot"], **_common("oos_oot", "red")}
    result = _aggregate(results)["all_results"]
    assert result["color"] == "green"
    assert result["registry"]["oos_oot"]["light"] == "red"


def test_missing_expected_test_is_amber():
    tests = _results()
    missing = tests.pop("global_drift")
    result = _aggregate(tests)["all_results"]

    assert result["color"] == "amber" and result["quality_status"] == "assessed"
    assert result["missing_tests"] == ["global_drift"]
    assert result["reasons"] == ["global_drift: ожидаемый результат отсутствует"]
    assert result["registry"]["global_drift"]["received"] is False

    restored = _aggregate({**tests, "global_drift": missing})["all_results"]
    assert restored["missing_tests"] == [] and restored["color"] == "green"


def test_km_not_computable_is_amber_not_assessed():
    results = _results()
    results["km_test"] = {
        **results["km_test"],
        **_common("km_test", "gray", "not_computable"),
        "reason": "нет базового значения",
    }
    result = _aggregate(results)["all_results"]
    assert result["color"] == "amber" and result["quality_status"] == "not_assessed"
    assert result["reasons"] == ["km_test: не оценено (нет базового значения)"]
    assert "не выполнена" in result["calculated_traffic_lights"]["semaphore_title"]


def test_km_red_with_admitted_judge_is_red():
    results = _results()
    results["km_test"] = {**results["km_test"], **_common("km_test", "red")}
    result = _aggregate(results)["all_results"]
    assert result["color"] == "red" and result["quality_status"] == "assessed"
    assert result["calculated_traffic_lights"]["test_light"] == "red"


def test_low_accuracy_blocks_red_and_green():
    results = _results()
    results["km_test"] = {**results["km_test"], **_common("km_test", "red")}
    result = _aggregate(results, assessor_accuracy=0.3)["all_results"]
    assert result["color"] == "amber" and result["quality_status"] == "not_assessed"
    assert result["registry"]["assessor"]["light"] == "red"


def test_unexpected_test_is_rejected():
    with pytest.raises(ValueError, match="не ожидается"):
        _aggregate(_results(), expected_tests="km_test,oos_oot", informative_tests="oos_oot")


@pytest.mark.parametrize(
    "expected, informative, message",
    [
        ("local_drift,oos_oot", "oos_oot", "km_test"),
        ("km_test,oos_oot", "km_test", "km_test"),
        ("km_test,oos_oot", "global_drift", "informative_tests"),
        ("km_test,nope", "", "nope"),
    ],
)
def test_settings_are_validated(expected, informative, message):
    with pytest.raises(ValueError, match=message):
        _aggregate(_results(), expected_tests=expected, informative_tests=informative)


def test_duplicate_test_is_rejected():
    km = _results()["km_test"]
    with pytest.raises(ValueError, match="повторно"):
        agg.main(
            assessor_accuracy=0.9,
            assessment_result=_assessment_result(),
            in0=km,
            in1=dict(km),
        )


def test_incomplete_all_results_is_rejected():
    km = _results()["km_test"]
    km.pop("km_baseline")
    with pytest.raises(ValueError, match="km_baseline"):
        _aggregate({"km_test": km})


def test_conflicting_colors_are_rejected():
    km = _results()["km_test"]
    km["calculated_traffic_lights"]["test_light"] = "red"
    with pytest.raises(ValueError, match="несогласованные цвета"):
        _aggregate({"km_test": km})


def test_unknown_status_is_rejected():
    km = _results()["km_test"]
    km["status"] = "success"
    with pytest.raises(ValueError, match="status='success'.*не поддерживается"):
        _aggregate({"km_test": km})


def test_not_computable_must_be_gray():
    km = _results()["km_test"]
    km["status"] = "not_computable"
    with pytest.raises(ValueError, match="status/color"):
        _aggregate({"km_test": km})


def test_unavailable_assessor_is_amber_not_assessed():
    result = _aggregate(
        _results(),
        assessor_accuracy=None,
        assessment_result=_assessment_result("not_computable"),
    )["all_results"]

    assert result["color"] == "amber" and result["quality_status"] == "not_assessed"
    assert result["assessor_accuracy"] is None
    assert result["registry"]["assessor"]["status"] == "not_assessed"


def test_computed_assessment_requires_numeric_accuracy():
    with pytest.raises(ValueError, match="assessor_accuracy.*числом"):
        _aggregate(_results(), assessor_accuracy=None)


def test_descriptor_deploys_table15_and_settings():
    descriptor = json.loads((MODULE_DIR / "descriptor.json").read_text())
    source_files = descriptor["script"]["runConfiguration"]["sourceFiles"]
    assert "table15.py" in source_files and "aggregator.py" not in source_files
    settings = descriptor["ui"]["settings"][0]["components"][0]["config"]["components"]
    by_name = {item["parameter"]: item for item in settings}
    assert by_name["expected_tests"]["defaultValue"] == "km_test,local_drift,global_drift,oos_oot"
    assert by_name["informative_tests"]["defaultValue"] == "local_drift,global_drift,oos_oot"
    assert by_name["red_assessor_accuracy"]["defaultValue"] == 0.6
    port = next(p for p in descriptor["ports"] if p["name"] == "all_results")
    assert "monitoring-result/v3" in port["description"]


def test_missing_processing_report_is_amber_not_assessed():
    result = _aggregate(_results(), processing_report=None)["all_results"]
    assert result["color"] == "amber" and result["quality_status"] == "not_assessed"
    assert result["reasons"][0] == "data_readiness: ожидаемый результат отсутствует"
    assert result["registry"]["data_readiness"]["received"] is False


def test_limited_data_is_amber_but_assessed():
    result = _aggregate(
        _results(), processing_report=_report("limited", "покрытие извлечения 0.80 ниже минимума 0.90"),
    )["all_results"]
    assert result["color"] == "amber" and result["quality_status"] == "assessed"
    assert "ограничение данных" in result["reasons"][0]


def test_failed_data_blocks_quality():
    results = _results()
    results["km_test"] = {**results["km_test"], **_common("km_test", "red")}
    result = _aggregate(results, processing_report=_report("failed", "K2"))["all_results"]
    assert result["color"] == "amber" and result["quality_status"] == "not_assessed"


def test_sample_meta_is_passed_through_as_provenance():
    meta = {"unit": "session", "population_units": 283, "sampled_units": 94, "fraction": 0.33}
    result = _aggregate(_results(), sample_meta=meta)["all_results"]
    assert result["provenance"]["sample"] == meta
    with pytest.raises(ValueError, match="sample_meta"):
        _aggregate(_results(), sample_meta="not a dict")


def test_descriptor_declares_data_and_sample_ports():
    descriptor = json.loads((MODULE_DIR / "descriptor.json").read_text())
    ports = {port["name"]: port for port in descriptor["ports"]}
    assert ports["processing_report"]["in"] is True and ports["processing_report"]["required"] is False
    assert ports["sample_meta"]["in"] is True and ports["sample_meta"]["required"] is False
    assert ports["in"]["dynamic"] is True
