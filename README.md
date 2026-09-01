# laim-agg

Единственная точка агрегации результатов мониторинговых тестов. Нода получает
четыре `all_results` через динамические входы `in*`, проверяет их контракт,
выставляет итоговый светофор и возвращает один объект `monitoring-result/v1`.

## Входы

| Порт | Источник | Содержание |
|---|---|---|
| `assessor_accuracy` | `assessor.Acc_auto` | точность автоассессора `0..1` |
| `in*` | `*.all_results` четырёх тестов | `km_test`, `local_drift`, `global_drift`, `oos_oot` |

Каждый `all_results` обязан содержать общие поля `test_name`, `color`,
`status`, `calculated_traffic_lights` и специфические машинные поля теста.
Отдельные HTML-выходы `test_description` агрегатору и сборщику не нужны.

## Выход `all_results`

```text
schema_version = monitoring-result/v1
color, calculated_traffic_lights
assessor_accuracy
expected_tests, missing_tests
test_results = {
  km_test: {...},
  local_drift: {...},
  global_drift: {...},
  oos_oot: {...}
}
```

`test_results` сохраняет фактические машинные доказательства тестов без
парсинга HTML. Дублированный тест, неизвестный `test_name`, несовпадающие
`color`/`test_light` или неполный результат вызывают явную ошибку.

## Итоговый цвет

- любой `red` → `red`;
- иначе любой `amber` → `amber`;
- только цветные `green` → `green`;
- нет цветных результатов → `gray`;
- неполный реестр или недопущенный автоассессор не могут дать `green`:
  потенциальный зелёный результат понижается до `gray`.

Детектор аномалий не участвует в светофоре. Его `test_anomalies` подключается
напрямую к `report-assembler.detector_anomalies`.
