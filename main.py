import logging
from typing import Any, Dict

from aggregator import aggregate, count_colors, normalize_color

logger = logging.getLogger(__name__)

RESULT_SCHEMA_VERSION = "monitoring-result/v2-simple"
EXPECTED_TESTS = ("km_test", "local_drift", "global_drift", "oos_oot")

_COLOR_DATIVE = {
    'red': 'красному',
    'amber': 'жёлтому',
    'green': 'зелёному',
    'gray': 'серому',
}
_COLOR_RU = {'red': 'Красный', 'amber': 'Жёлтый', 'green': 'Зелёный', 'gray': 'Серый'}
_COLOR_HEX = {'red': '#e53935', 'amber': '#fb8c00', 'green': '#43a047', 'gray': '#9e9e9e'}

_TEST_LABEL = {
    'km_test': 'Динамика ключевой метрики',
    'local_drift': 'Локальный дрифт запросов',
    'global_drift': 'Глобальный дрифт запросов',
    'oos_oot': 'Разделение выборок (OOS/OOT)',
    'anomaly_detector_test': 'Детектор аномалий',
}


def _color_of(item):
    return normalize_color((item or {}).get('color'))


def _test_name_of(item):
    return (item or {}).get('test_name') or '—'


def _normalize_item(item: Any, position: int) -> dict | None:
    """Принимает all_results теста как есть.

    Достаточно цвета: color либо calculated_traffic_lights.test_light
    (yellow→amber, grey→gray нормализуются). Никаких обязательных полей —
    реальные тест-ноды отдают разные наборы, агрегатору нужен только цвет.
    Нераспознанный вход не роняет ноду: он считается серым и логируется.
    """
    if not isinstance(item, dict):
        logger.warning(
            "laim-agg.in%d: вход не объект (%s) — пропущен",
            position, type(item).__name__,
        )
        return None
    record = dict(item)
    lights = record.get("calculated_traffic_lights")
    lights = dict(lights) if isinstance(lights, dict) else {}
    color = normalize_color(record.get("color"))
    if color == "unknown":
        color = normalize_color(lights.get("test_light"))
    if color == "unknown":
        logger.warning(
            "laim-agg.in%d (%r): цвет не распознан (color=%r, test_light=%r) — "
            "считается серым",
            position, record.get("test_name"),
            record.get("color"), lights.get("test_light"),
        )
        color = "gray"
    record["color"] = color
    lights["test_light"] = color
    lights.setdefault("semaphore_title", "")
    record["calculated_traffic_lights"] = lights
    record.setdefault("test_name", f"in{position}")
    return record


def _dot(c: str, size: int = 12) -> str:
    """CSS-кружок цвета светофора (вместо эмодзи — эмодзи на платформе = боксы)."""
    return (f"<span style='display:inline-block;width:{size}px;height:{size}px;"
            f"border-radius:50%;background:{_COLOR_HEX.get(c, '#9e9e9e')};"
            f"vertical-align:middle'></span>")


def _report_html(color: str, inputs: list, missing_tests: list[str],
                 critical_red: int, critical_amber: int) -> str:
    """Чистая HTML-сводка для вкладки Results. БЕЗ эмодзи — только CSS-кружки
    (эмодзи 🔴🟡🟢 на платформенном HTML-рендере выводятся как пустые боксы)."""
    color_counts = count_colors(inputs)
    n_red, n_amber, n_green = color_counts['red'], color_counts['amber'], color_counts['green']
    n_gray = color_counts['gray'] + color_counts['unknown']
    badge = (
        f"<div style='text-align:center;margin:6px 0 14px'>"
        f"<span style='display:inline-block;padding:8px 22px;border-radius:16px;"
        f"background:{_COLOR_HEX.get(color, '#9e9e9e')};color:#fff;font-weight:700;"
        f"font-size:20px;letter-spacing:1px'>{_COLOR_RU.get(color, color).upper()}</span></div>"
    )
    # счётчики кружками
    def _cnt(c, n):
        return f"{_dot(c)}&nbsp;<b>{n}</b>"
    counts_html = (f"{_cnt('red', n_red)} &nbsp;&nbsp; {_cnt('amber', n_amber)} "
                   f"&nbsp;&nbsp; {_cnt('green', n_green)} &nbsp;&nbsp; {_cnt('gray', n_gray)}")
    rows = ""
    for i in inputs:
        c = _color_of(i) or 'gray'
        name = _test_name_of(i)
        label = _TEST_LABEL.get(name, name)
        rows += (
            f"<tr><td style='padding:6px 12px;border-bottom:1px solid #eee'>{label}</td>"
            f"<td style='padding:6px 12px;border-bottom:1px solid #eee'>"
            f"{_dot(c)}&nbsp; {_COLOR_RU.get(c, c)}</td></tr>"
        )
    if not rows:
        rows = ("<tr><td colspan=2 style='padding:6px 12px;color:#888'>"
                "на вход не пришло ни одного теста</td></tr>")
    # информационная строка о неподключённых тестах — без изменения цвета
    missing_note = (
        "<p style='color:#777;margin:6px 0;font-size:12px'>Не подключены: "
        + ", ".join(_TEST_LABEL.get(t, t) for t in missing_tests) + ".</p>"
    ) if missing_tests else ""
    # легенда-правило кружками
    rule = (
        "<p style='color:#777;font-size:12px;margin-top:12px;line-height:1.7'>"
        f"<b>Правило светофора:</b><br>"
        f"{_dot('red', 10)} красный — если красных тестов ≥ {critical_red};<br>"
        f"{_dot('amber', 10)} жёлтый — если жёлтых ≥ {critical_amber};<br>"
        f"{_dot('green', 10)} зелёный — если есть цветные тесты и нет красных/жёлтых;<br>"
        f"{_dot('gray', 10)} серый — если на вход не пришло ни одного цветного теста."
        "</p>"
    )
    return (
        "<html><body style='font-family:sans-serif;font-size:14px;color:#222'>"
        "<h3 style='margin:0 0 4px;text-align:center'>Итоговый светофор мониторинга</h3>"
        f"{badge}"
        f"<p style='margin:4px 0;color:#555'>Тестов на входе: <b>{len(inputs)}</b> "
        f"&nbsp;&nbsp; {counts_html}</p>"
        f"{missing_note}"
        "<table style='border-collapse:collapse;margin:10px 0;width:100%;max-width:520px'>"
        "<tr style='background:#f5f5f5'>"
        "<th style='padding:6px 12px;text-align:left'>Тест</th>"
        "<th style='padding:6px 12px;text-align:left'>Светофор</th></tr>"
        f"{rows}</table>"
        f"{rule}"
        "</body></html>"
    )


def main(**kwargs) -> Dict[str, Any]:
    """Агрегация светофоров по all_results тестов, подключённых на in*-порты.

    Упрощённый контракт: на вход подаются только результаты тестов, порт
    точности ассессора и мета-заглушки не нужны. Лишние kwargs (в т.ч.
    assessor_accuracy от старой обвязки) игнорируются.
    """
    block_name = "Результаты мониторинга"
    critical_red = 1
    critical_amber = 1

    raw_inputs = [v for k, v in sorted(kwargs.items()) if k.startswith('in')]
    inputs = []
    for position, item in enumerate(raw_inputs):
        record = _normalize_item(item, position)
        if record is not None:
            inputs.append(record)

    logger.info(
        '[g-aiva-agg] block=%r | inputs=%d | critical_red=%d | critical_amber=%d',
        block_name, len(inputs), critical_red, critical_amber,
    )
    color = aggregate(inputs, critical_red=critical_red, critical_amber=critical_amber)
    logger.info(f'[g-aiva-agg] По результатам агрегации получен {color} светофор')

    semaphore_title = (
        f'Результат мониторинга '
        f'соответствует {_COLOR_DATIVE[color]} светофору'
    )

    # Реестр известных тестов — информационно (без гейтов и обязательных полей).
    test_results: dict[str, dict] = {}
    for record in inputs:
        name = record.get("test_name")
        if name in EXPECTED_TESTS and name not in test_results:
            test_results[name] = record
    missing_tests = [name for name in EXPECTED_TESTS if name not in test_results]

    counts = count_colors(inputs)
    return {
        'all_results': {
            'calculated_traffic_lights': {
                'test_light': color,
                'semaphore_title': semaphore_title,
            },
            'block_name': block_name,
            'color': color,
            # Машиночитаемая база вердикта: сколько тестов пришло и какими
            # цветами — «3 зелёных» и «1 зелёный + 3 серых» больше не выглядят
            # одинаково в отчёте.
            'n_inputs': len(inputs),
            'color_counts': counts,
            'received_tests': [_test_name_of(i) for i in inputs],
            'schema_version': RESULT_SCHEMA_VERSION,
            'expected_tests': list(EXPECTED_TESTS),
            'missing_tests': missing_tests,
            'test_results': test_results,
            'report_html': _report_html(
                color, inputs, missing_tests, critical_red, critical_amber,
            ),
        }
    }
