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


def _aggregate(tests, **kwargs):
    return agg.main(
        assessor_accuracy=kwargs.pop("assessor_accuracy", 0.9),
        assessment_result=kwargs.pop("assessment_result", _assessment_result()),
        **{f"in{index}": value for index, value in enumerate(tests.values())},
        **kwargs,
    )


def test_full_registry_is_preserved_in_one_output():
    tests = _results()
    result = _aggregate(tests)["all_results"]

    assert result["schema_version"] == "monitoring-result/v2"
    assert result["expected_tests"] == list(agg.EXPECTED_TESTS)
    assert result["missing_tests"] == []
    assert result["test_results"] == tests
    assert result["color"] == "green"
    assert result["assessor_accuracy"] == 0.9


def test_missing_test_cannot_produce_green():
    tests = _results()
    missing = tests.pop("global_drift")
    result = _aggregate(tests)["all_results"]

    assert result["color"] == "gray"
    assert result["coverage_gate_applied"] is True
    assert result["missing_tests"] == ["global_drift"]

    # тот же набор с вернувшимся global_drift снова даёт полный реестр
    restored = _aggregate({**tests, "global_drift": missing})["all_results"]
    assert restored["missing_tests"] == []
    assert restored["color"] == "green"


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


def test_unavailable_assessor_cannot_produce_green():
    result = _aggregate(
        _results(), assessment_result=_assessment_result("not_computable")
    )["all_results"]

    assert result["color"] == "gray"
    assert result["assessment_gate_applied"] is True


def test_descriptor_deploys_transitive_import():
    descriptor = json.loads((MODULE_DIR / "descriptor.json").read_text())
    assert "aggregator.py" in descriptor["script"]["runConfiguration"]["sourceFiles"]
