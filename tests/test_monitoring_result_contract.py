"""Контракт единого monitoring-result/v1 между laim-agg и сборщиком."""

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest


MODULE_DIR = Path(__file__).resolve().parents[1]
REPORT_DIR = MODULE_DIR.parent / "laim-report-assembler"


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


def _load_report_main():
    sys.path.insert(0, str(REPORT_DIR))
    try:
        spec = importlib.util.spec_from_file_location(
            "report_assembler_handoff", REPORT_DIR / "main.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


report_node = _load_report_main()


def _common(test_name, color="green", status="ok"):
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


def test_full_registry_is_preserved_in_one_output():
    tests = _results()
    result = agg.main(
        assessor_accuracy=0.9,
        **{f"in{index}": value for index, value in enumerate(tests.values())},
    )["all_results"]

    assert result["schema_version"] == "monitoring-result/v1"
    assert result["expected_tests"] == list(agg.EXPECTED_TESTS)
    assert result["missing_tests"] == []
    assert result["test_results"] == tests
    assert result["color"] == "green"
    assert result["assessor_accuracy"] == 0.9


def test_report_assembler_accepts_aggregator_output_without_adapters(tmp_path):
    tests = _results()
    result = agg.main(
        assessor_accuracy=0.9,
        **{f"in{index}": value for index, value in enumerate(tests.values())},
    )

    report = report_node.main(
        scored_data=pd.DataFrame({
            "query_id": ["trace-1"],
            "question": ["вопрос"],
            "answer": ["ответ"],
            "agent_target": [0.9],
        }),
        metric_selector_res={"main_metric": "target"},
        aggregator_result=result,
        detector_anomalies=[],
        output_dir=str(tmp_path),
    )["report_json"]

    assert report["timeline"][0]["zone"] == "green"
    assert report["km_dynamics"]["baseline"] == 0.8
    assert report["km_dynamics"]["series"][0]["km"] == 0.75
    assert report["km_dynamics"]["series"][0]["assessor_accuracy"] == 0.9
    assert len(report["anomalies"]) == 4


def test_missing_test_cannot_produce_green(tmp_path):
    tests = _results()
    tests.pop("global_drift")
    result = agg.main(
        assessor_accuracy=0.9,
        **{f"in{index}": value for index, value in enumerate(tests.values())},
    )["all_results"]

    assert result["color"] == "gray"
    assert result["coverage_gate_applied"] is True
    assert result["missing_tests"] == ["global_drift"]

    report = report_node.main(
        scored_data=pd.DataFrame({
            "query_id": ["trace-1"], "question": ["вопрос"],
            "answer": ["ответ"], "agent_target": [0.9],
        }),
        metric_selector_res={"main_metric": "target"},
        aggregator_result=result,
        detector_anomalies=[],
        output_dir=str(tmp_path),
    )["report_json"]
    assert report["timeline"][0]["zone"] == "gray"
    assert "global_drift" in report["general_comment"]


def test_duplicate_test_is_rejected():
    km = _results()["km_test"]
    with pytest.raises(ValueError, match="повторно"):
        agg.main(assessor_accuracy=0.9, in0=km, in1=dict(km))


def test_report_refuses_bundle_without_km_test():
    tests = _results()
    tests.pop("km_test")
    result = agg.main(
        assessor_accuracy=0.9,
        **{f"in{index}": value for index, value in enumerate(tests.values())},
    )
    with pytest.raises(ValueError, match="не содержит km_test"):
        report_node._as_monitoring_result(result)


def test_incomplete_all_results_is_rejected():
    km = _results()["km_test"]
    km.pop("km_baseline")
    with pytest.raises(ValueError, match="km_baseline"):
        agg.main(assessor_accuracy=0.9, in0=km)


def test_conflicting_colors_are_rejected():
    km = _results()["km_test"]
    km["calculated_traffic_lights"]["test_light"] = "red"
    with pytest.raises(ValueError, match="несогласованные цвета"):
        agg.main(assessor_accuracy=0.9, in0=km)


def test_unknown_status_is_rejected():
    km = _results()["km_test"]
    km["status"] = "success"
    with pytest.raises(ValueError, match="status='success'"):
        agg.main(assessor_accuracy=0.9, in0=km)


def test_not_computable_must_be_gray():
    km = _results()["km_test"]
    km["status"] = "not_computable"
    with pytest.raises(ValueError, match="status/color"):
        agg.main(assessor_accuracy=0.9, in0=km)
