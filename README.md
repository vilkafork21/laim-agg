# laim-agg

Последняя нода мониторингового контура LAIM. Принимает `all_results` четырёх
мониторинговых тестов, точность автоассессора и статус оценивания
monitoring-выборки, проверяет их контракты и отдаёт один объект
`monitoring-result/v2`: итоговый светофор блока, реестр результатов тестов
и причины, по которым потенциальный зелёный итог был понижен.

## Зачем нода нужна

Тесты контура независимы и каждый выставляет свой светофор. Нода снимает шаг
методики «итоговый цвет по блоку»: цвет считается по одному правилу, каждый
вход проходит проверку контракта, машинные поля тестов (`km_baseline`,
`gini_mean`, `metric_value_reference`) переносятся в выход без разбора HTML.

Ключевое решение: **нода не умеет «чуть-чуть зелёный»**. Итог `green`
возможен только когда подключены все четыре теста, автоассессор допущен по
точности, оценивание monitoring-выборки выполнено и ключевая метрика
вычислена; иначе `green` понижается до `gray` с причиной в `gate_reasons`.
На `red` и `amber` гейты не влияют. Второе решение: **входы не чинятся
молча** — несогласованный или неполный `all_results` роняет ноду с явной ошибкой.

## Место в контуре

```text
laim-local-drift-test.all_results   ─┐
laim-global-drift-test.all_results  ─┤
laim-oos-oot-test.all_results       ─┼─► in (динамический)  ─┐
laim-km-dynamic-test.all_results    ─┘                        ├─ laim-agg ──► all_results
laim-asessor-agent.acc_auto          ──► assessor_accuracy    │   (семафор и HTML в UI ноды;
laim-asessor-agent.assessment_result ──► assessment_result   ─┘    потребителей в port_wiring нет)
```

Схема — `laim-sberds-wiring.v7` (`monitoring/shared/port_wiring.json` в `laim`);
выход `all_results` читают только семафор и виджет результата самой ноды.

## Порты и настройки

### Входы

| Порт | Обязательный | Что приходит с платформы |
|---|---|---|
| `assessor_accuracy` | да | `acc_auto` из `laim-asessor-agent`: число `0..1` (`bool`, NaN и inf отвергаются) или `null`, только если `assessment_result.status = not_computable` |
| `assessment_result` | да | `laim-assessment-result.v1` из `laim-asessor-agent`: `contract_version`, `status`, `total_units`, `scored_units`; при `not_computable` — `reason` |
| `in` (динамический) | нет | `all_results` тестов `km_test`, `local_drift`, `global_drift`, `oos_oot` — по одному соединению на тест |

Динамические входы приходят в `main` как `**kwargs`: берутся все ключи с
префиксом `in` в лексикографическом порядке. Итог от порядка не зависит
(индексация по `test_name`), но `laim-agg.in<N>` в тексте ошибки — позиция
после сортировки: при десяти и более входах `in10` идёт раньше `in2`.

### Выход

| Порт | Тип | Контракт |
|---|---|---|
| `all_results` | default | `monitoring-result/v2` (см. «Форматы выхода и контракты») |

Семафор ноды (`uiResults.semaphore`) читает `all_results.color`; виджет результата —
`$.all_results.calculated_traffic_lights.test_light` и `semaphore_title`, HTML — `$.all_results.report_html`.

### Настройки

| Настройка | По умолчанию | Зачем менять |
|---|---|---|
| `red_assessor_accuracy` | `0.6` | Порог допуска автоассессора: при `assessor_accuracy <= порог` зелёный итог понижается до серого. Проверяется тем же правилом `0..1`, что и точность |

## Как проходит прогон

```text
1. Валидация оценивания assessment_result → contract_version, status, счётчики
2. Валидация скаляров   assessor_accuracy и red_assessor_accuracy → число 0..1; для невычислимого оценивания accuracy может быть null
3. Индексация тестов    kwargs in* → поля, статусы, цвета, дубли, поля km_test
4. Агрегация            aggregator.aggregate: red → amber → green / gray
5. Гейты                coverage, accuracy, assessment, key_metric → gate_reasons
6. Публикация           all_results + report_html
```

**3. Индексация тестов.** Вход — объект с полями `test_name`, `status`,
`color`, `calculated_traffic_lights`; `test_name` из набора `km_test`,
`local_drift`, `global_drift`, `oos_oot`, без повторов; `status` — `computed`,
`low_confidence` или `not_computable`. Цвета нормализуются (`yellow` → `amber`,
`grey` → `gray`); `color` и `calculated_traffic_lights.test_light` должны
совпадать; `not_computable` допустим тогда и только тогда, когда цвет `gray`.
Для `km_test` обязательны ещё `km_name`, `km_baseline`, `km_monitoring`,
`km_delta`. В `test_results` уходит копия входа с нормализованными цветами
в порядке `expected_tests`.

**4. Агрегация.** Хотя бы один `red` → `red`; иначе хотя бы один `amber` →
`amber`; ни одного `red`/`amber`/`green` → `gray`; иначе `green`. Нераспознанный
цвет (`unknown`) в агрегации не участвует; через `main` он не проходит — его отвергает шаг 3.

**5. Гейты.** Только для `green`: неполный реестр; точность не выше порога;
`assessment_result.status != computed`; `km_test` отсутствует или его
`status != computed` (`low_confidence` тоже понижает). Сработавший гейт
добавляет строку в `gate_reasons`, флаг `*_gate_applied`, итог становится `gray`.

### Пример лога прогона

Прогон на корзине `CI09997554`: четыре теста `green`, точность
автоассессора `0.789`, оценивание 94/94 (формат строк — из кода; значения условные):

```text
INFO aggregator: [laim-agg] Счётчики цветов: {'red': 0, 'amber': 0, 'green': 4, 'gray': 0, 'unknown': 0}
INFO main: laim-agg: tests=4 missing=[] color=green gates=[]
```

Прогон с понижением до серого (формат строк — из кода; значения условные):

```text
INFO aggregator: [laim-agg] Счётчики цветов: {'red': 0, 'amber': 0, 'green': 3, 'gray': 0, 'unknown': 0}
INFO main: laim-agg: tests=3 missing=['global_drift'] color=gray gates=['Неполный реестр обязательных тестов', 'Точность автоассессора 0.550 не выше порога 0.600']
```

## Форматы выхода и контракты

Единица наблюдения — один прогон мониторинга по блоку: одна корзина, один
итоговый цвет. Ключи `all_results` (присутствуют всегда):

| Поле | Что содержит |
|---|---|
| `schema_version` | строка `monitoring-result/v2` |
| `calculated_traffic_lights` | `test_light` (итоговый цвет) и `semaphore_title` («Результат мониторинга соответствует зелёному светофору») |
| `block_name` | «Результаты мониторинга» |
| `color` | итоговый цвет: `red`, `amber`, `green`, `gray` |
| `expected_tests` | `["km_test", "local_drift", "global_drift", "oos_oot"]` |
| `missing_tests` | не подключённые тесты в том же порядке |
| `test_results` | словарь `test_name → all_results` теста в порядке `expected_tests` |
| `color_counts` | счётчики `red`, `amber`, `green`, `gray`, `unknown` по входам |
| `assessor_accuracy` | принятая точность, `float`; `null`, если оценивание не вычислено |
| `assessment_result` | входной `laim-assessment-result.v1` как есть |
| `coverage_gate_applied`, `assessor_accuracy_gate_applied`, `assessment_gate_applied`, `key_metric_gate_applied` | `bool` по каждому гейту |
| `gate_reasons` | список причин понижения; пуст, если гейты не сработали |
| `report_html` | HTML-сводка: итог, счётчики, причины, таблица тестов |

Пример из прогона `CI09997554`: `color = "green"`, `missing_tests = []`,
`color_counts = {"red": 0, "amber": 0, "green": 4, "gray": 0, "unknown": 0}`,
`assessor_accuracy = 0.7894736842105263`, `gate_reasons = []`; в
`test_results.km_test` — `km_name = "Accuracy"`, `km_baseline = 0.93`,
`km_monitoring = 0.8297872340425532`, `km_delta = 0.10775566231983535`.

## Падение против деградации

Нода падает с `ValueError` (доменного `reason_code` нет; текст называет порт и поле):

| Причина | Текст ошибки |
|---|---|
| `assessor_accuracy` или `red_assessor_accuracy` не число, `bool`, NaN, вне `0..1`; `assessor_accuracy = null` при вычисленном оценивании | `assessor_accuracy должен быть числом в диапазоне 0..1` |
| `assessment_result` не объект, чужой `contract_version`, `status` вне `computed`/`not_computable`, `not_computable` без `reason` | `assessment_result …` с указанием поля |
| `computed` с нарушением `1 <= scored_units <= total_units` (или не `int`) | `assessment_result computed требует 1 <= scored_units <= total_units` |
| вход `in*` не объект или без обязательных полей | `laim-agg.in<N> не содержит поля [...]` |
| неизвестный `test_name`, повтор теста | `неизвестный test_name=…`, `результат … подключён повторно` |
| `status` вне допустимых, `calculated_traffic_lights` не объект | `….status=… не поддерживается` |
| нераспознанный цвет, `color != test_light`, `not_computable` без `gray` (и наоборот) | `… имеет несогласованные цвета` / `… status/color` |
| `km_test` без `km_name`, `km_baseline`, `km_monitoring`, `km_delta` | `km_test не содержит поля [...]` |

Деградации (нода отрабатывает, итог публикуется):

| Событие | Реакция |
|---|---|
| Подключены не все четыре теста | `missing_tests` заполнен; `green` → `gray`, причина «Неполный реестр обязательных тестов» |
| `assessor_accuracy <= red_assessor_accuracy` | `green` → `gray`, причина «Точность автоассессора X не выше порога Y» |
| `assessment_result.status = not_computable` | `assessor_accuracy` может быть `null`; `green` → `gray`, причина «Оценивание monitoring-выборки не выполнено» |
| `km_test` не подключён или `status != computed` | `green` → `gray`, причина «Ключевая метрика monitoring-выборки не вычислена» |
| Ни одного цветного входа (пусто или все `gray`) | итог `gray` без гейтов, INFO «Ни одного цветного светофора на входе» |
| `yellow` / `grey` во входе | приводятся к `amber` / `gray`, в `test_results` уходит нормализованный цвет |

## Внешние сервисы

Не применимо: нода не обращается к LLM-шлюзу, эмбеддингам, HDFS и сети.

## Наблюдаемость

В лог платформы уходят две строки на прогон: счётчики цветов из `aggregator`
и итог из `main` (`tests=`, `missing=`, `color=`, `gates=`). Порта журнала нет —
журналом служит сам `all_results`: на сотне прогонов агрегируйте `color`,
`missing_tests`, `gate_reasons`, флаги `*_gate_applied`, а по
`test_results.<test>.status` и `reason` находите тест-источник понижения.

## Карта кода

```text
descriptor.json     порты, настройка red_assessor_accuracy, семафор и HTML в UI, sourceFiles
main.py             валидация входов, гейты, сборка all_results и report_html
aggregator.py       нормализация цветов, счётчики, правило red → amber → green / gray
requirements.txt    только комментарий: зависимостей нет
tests/test_monitoring_result_contract.py   контракт выхода, отказы по входам, sourceFiles
```

## Что делать, если

- **Итог `gray`, все тесты зелёные** — смотрите `gate_reasons`: не подключён
  тест, автоассессор не прошёл порог, оценивание или КМ `not_computable`.
  Первый шаг — `all_results` соответствующей upstream-ноды.
- **Упала с `несогласованные цвета` / `status/color`** — дефект upstream-теста
  (`color` и `test_light` разные или `not_computable` не серый); чинить тест.
- **`результат … подключён повторно`** — тест подключён к `in` дважды.
- **`km_test не содержит поля`** — `laim-km-dynamic-test` не публикует плоские поля `km_*`; проверить его версию.
- **Нужен другой порог автоассессора** — настройка `red_assessor_accuracy`; равенство порогу тоже понижает.

## Деплой

База — `py312-simple`; функция `main` в `main.py`. `sourceFiles` в
`descriptor.json`: `main.py`, `aggregator.py`; тест
`test_descriptor_deploys_transitive_import` закрепляет, что транзитивный
`aggregator.py` объявлен (полного сравнения списка с диском нет).
`requirements.txt` объявлен в `libraryDependencies` и намеренно содержит только
комментарий: код на стандартной библиотеке, а без файла сборка контейнера
падает. CI `.github/workflows/ci.yml`: `ruff check .`, `python -m pytest -q`
на Python 3.12. ZIP ноды — `descriptor.json`, `requirements.txt` и два файла
`sourceFiles` из головы ветки `dev`.
## Глоссарий

- **Светофор** — цвет результата теста или блока: `red`, `amber`, `green`,
  `gray`; `gray` означает «не вычислено», а не «плохо».
- **Реестр тестов** — четыре обязательных теста `expected_tests`.
- **Гейт** — условие, понижающее `green` до `gray` с причиной в `gate_reasons`.
- **Автоассессор** — LLM-оценщик monitoring-выборки (`laim-asessor-agent`);
  `acc_auto` — его точность относительно разметки.
- **КМ** — ключевая метрика качества агента; её динамику проверяет `km_test`.
