# laim-agg

Последняя нода мониторингового контура LAIM. Принимает `all_results`
мониторинговых тестов, точность автоассессора и статус оценивания
monitoring-выборки, проверяет их контракты и отдаёт один объект
`monitoring-result/v3`: итоговый светофор итерации, статус оценки качества,
реестр ожидаемых и полученных тестов и основания итога.

## Зачем нода нужна

Тесты контура независимы и каждый выставляет свой светофор. Нода выполняет
шаг методики «правила выставления светофора за итерацию автомониторинга»
(таблица 15 методики валидации GenAI-решений, этап 4): итог не складывается
голосованием цветов, а выводится последовательным применением правил.

Три решения, на которых стоит нода:

- **Красный итог только от ключевой метрики.** Красный возможен лишь при
  красном `km_test` (тест 6.3.4) и допущенном автоассессоре (тест 6.3.3).
  Дрейф-тесты и различимость выборок красный итог не формируют.
- **Отсутствие результата — не зелёный.** Ожидаемый тест, которого нет на
  входе или который не оценён, даёт жёлтый итог с основанием; серого итога
  нет — при невозможности оценки итог жёлтый, а `quality_status` равен
  `not_assessed`.
- **Информативные тесты в цвет не входят.** Покрытие потока (`local_drift`,
  6.3.7), прогноз влияния сдвига (`global_drift`, 6.3.8) и различимость
  выборок без эталона стабильного периода (`oos_oot`, 6.3.6) публикуются в
  реестре и HTML, но итог не меняют. Состав задаётся настройкой.

Входы не чинятся молча: несогласованный или неполный `all_results` роняет
ноду с явной ошибкой.

## Место в контуре

```text
laim-local-drift-test.all_results   ─┐
laim-global-drift-test.all_results  ─┤
laim-oos-oot-test.all_results       ─┼─► in (динамический)  ─┐
laim-km-dynamic-test.all_results    ─┘                        ├─ laim-agg ──► all_results
laim-asessor-agent.acc_auto          ──► assessor_accuracy    │   (семафор и HTML в UI ноды;
laim-asessor-agent.assessment_result ──► assessment_result    │    потребителей в port_wiring нет)
laim-traces-dataset-converter.processing_report ──► processing_report │
laim-extract-sample.sample_meta      ──► sample_meta         ─┘
```

Схема — `laim-sberds-wiring.v8` (`monitoring/shared/port_wiring.json` в `laim`);
выход `all_results` читают только семафор и виджет результата самой ноды.

## Порты и настройки

### Входы

| Порт | Обязательный | Что приходит с платформы |
|---|---|---|
| `assessor_accuracy` | да | `acc_auto` из `laim-asessor-agent`: число `0..1` (`bool`, NaN и inf отвергаются) или `null`, только если `assessment_result.status = not_computable` |
| `assessment_result` | да | `laim-assessment-result.v1` из `laim-asessor-agent`: `contract_version`, `status`, `total_units`, `scored_units`; при `not_computable` — `reason` |
| `processing_report` | нет (ожидается) | `processing_report` из `laim-traces-dataset-converter`: блок `data_readiness` — базовый тест 6.3.2 (пригодность данных периода). Без него итог не выше жёлтого: «ожидаемый результат отсутствует» |
| `sample_meta` | нет | `sample_meta` из `laim-extract-sample`: население и отобранные единицы, доля, seed — провенанс, на цвет не влияет |
| `in` (динамический) | нет | `all_results` тестов из `expected_tests` — по одному соединению на тест |

Динамические входы приходят в `main` как `**kwargs`: берутся все ключи с
префиксом `in` в лексикографическом порядке. Итог от порядка не зависит
(индексация по `test_name`), но `laim-agg.in<N>` в тексте ошибки — позиция
после сортировки: при десяти и более входах `in10` идёт раньше `in2`.

### Выход

| Порт | Тип | Контракт |
|---|---|---|
| `all_results` | default | `monitoring-result/v3` (см. «Форматы выхода и контракты») |

Семафор ноды (`uiResults.semaphore`) читает `all_results.color`; виджет результата —
`$.all_results.calculated_traffic_lights.test_light` и `semaphore_title`, HTML — `$.all_results.report_html`.

### Настройки

| Настройка | По умолчанию | Зачем менять |
|---|---|---|
| `expected_tests` | `km_test,local_drift,global_drift,oos_oot` | Реестр ожидаемых тестов итерации. `km_test` обязателен; результат теста вне списка отвергается ошибкой. Убирайте тест из списка, если он снят с wiring, иначе его отсутствие даст жёлтый итог |
| `informative_tests` | `local_drift,global_drift,oos_oot` | Тесты, результат которых публикуется, но итог не меняет. Подмножество `expected_tests`, без `km_test`. Уберите `oos_oot`, когда тест сравнивает поток с эталонной выборкой стабильного периода: тогда он светофорный, максимум жёлтый |
| `red_assessor_accuracy` | `0.6` | Порог допуска автоассессора: при `assessor_accuracy <= порог` автоассессор не допущен, итог не выше жёлтого, `quality_status = not_assessed`. Проверяется тем же правилом `0..1`, что и точность |

## Как проходит прогон

```text
1. Настройки            expected_tests / informative_tests → реестр итерации
2. Валидация оценивания assessment_result → contract_version, status, счётчики
3. Валидация скаляров   assessor_accuracy и red_assessor_accuracy → число 0..1; для невычислимого оценивания accuracy может быть null
4. Индексация тестов    kwargs in* → поля, статусы, цвета, дубли, поля km_test; тест вне expected_tests отвергается
5. Результаты методики  данные периода (6.3.2) по data_readiness → sufficient / limited / insufficient / failed; каждый тест → выполнен / не оценено / отсутствует; автоассессор → допущен / не допущен / не оценён
6. Таблица 15           table15.decide: красный → жёлтый → зелёный без голосования
7. Публикация           all_results + report_html
```

**4. Индексация тестов.** Вход — объект с полями `test_name`, `status`,
`color`, `calculated_traffic_lights`; `test_name` из `expected_tests`, без
повторов; `status` — `computed`, `low_confidence` или `not_computable`. Цвета
нормализуются (`yellow` → `amber`, `grey` → `gray`); `color` и
`calculated_traffic_lights.test_light` должны совпадать; `not_computable`
допустим тогда и только тогда, когда цвет `gray`. Для `km_test` обязательны
ещё `km_name`, `km_baseline`, `km_monitoring`, `km_delta`. В `test_results`
уходит копия входа с нормализованными цветами в порядке `expected_tests`.

**5. Результаты методики.** `status = not_computable` → «не оценено» с
`reason` (или `reason_code`) теста; тест без входа → «отсутствует»;
`computed` → цвет теста. Автоассессор: `assessment_result.status !=
computed` → «не оценён»; если ассессор публикует
`calibration_metrics.admission_status` (тест 6.3.3: `green` / `amber` / `red` /
`not_assessed`), допуск берётся из него; иначе `assessor_accuracy <=
red_assessor_accuracy` → «не допущен» (красный), иначе допущен. Жёлтый допуск
— прокси-оценки применимы с усиленным контролем: итог не выше жёлтого, красный
`km_test` сохраняется.

**6. Таблица 15.** Красный — `km_test` красный, автоассессор допущен и данные
периода пригодны (`data_readiness.state` `sufficient` или `limited`).
Иначе жёлтый, если есть хотя бы одно основание: `processing_report` не
подключён, данные не оценены (`insufficient`), непригодны (`failed`) или
ограничены (`limited`); автоассессор не допущен или
не оценён; `km_test` отсутствует, не оценён или жёлтый; любой ожидаемый тест
отсутствует (и информативный тоже — реестр должен быть полон); светофорный
дополнительный тест не оценён, жёлтый или красный (дополнительный тест не
поднимает итог выше жёлтого). Иначе зелёный. `quality_status = assessed`
только при допущенном автоассессоре и выполненном `km_test`.

### Пример лога прогона

Прогон с полным реестром и зелёным `km_test` (формат строки — из кода;
значения условные):

```text
INFO main: [laim-agg] итог=green оценка=assessed получено=4/4 основания=[]
```

Прогон без результата `global_drift` и с точностью автоассессора ниже порога:

```text
INFO main: [laim-agg] итог=amber оценка=not_assessed получено=3/4 основания=['автоассессор не допущен: точность автоассессора 0.550 не выше порога 0.600', 'km_test: результат green не учитывается без допуска автоассессора', 'global_drift: ожидаемый результат отсутствует']
```

## Форматы выхода и контракты

Единица наблюдения — одна итерация автомониторинга: одна корзина, один
период, один итог. Ключи `all_results` (присутствуют всегда):

| Поле | Что содержит |
|---|---|
| `schema_version` | строка `monitoring-result/v3` |
| `calculated_traffic_lights` | `test_light` (итоговый цвет) и `semaphore_title` («Итог итерации: жёлтый; оценка качества не выполнена») |
| `block_name` | «Результаты мониторинга» |
| `color` | итоговый цвет: `red`, `amber`, `green` |
| `quality_status` | `assessed` — оценка качества получена; `not_assessed` — автоассессор не допущен или `km_test` не выполнен |
| `expected_tests` | реестр из настройки, в порядке настройки |
| `informative_tests` | информативные тесты из настройки, отсортированы |
| `missing_tests` | ожидаемые тесты без входа, в порядке `expected_tests` |
| `registry` | словарь `data_readiness`, `assessor` и каждого ожидаемого теста → `expected`, `received`, `informative`, `status` (`computed` / `not_assessed` / `null`), `light` (`red` / `amber` / `green` / `null`), `reason` |
| `provenance` | `sample` — `sample_meta` как есть (или `null`), `data_readiness` — блок конвертера как есть (или `null`) |
| `reasons` | основания итога в порядке проверки; пуст только при зелёном итоге |
| `test_results` | словарь `test_name → all_results` теста в порядке `expected_tests` |
| `assessor_accuracy` | принятая точность, `float`; `null`, если оценивание не вычислено |
| `assessment_result` | входной `laim-assessment-result.v1` как есть |
| `report_html` | HTML: итог, статус оценки, основания, таблица реестра |

Пример: зелёный `km_test` при `acc_auto = 0.79`, `oos_oot` красный
(информативный) → `color = "green"`, `quality_status = "assessed"`,
`reasons = []`, `registry.oos_oot = {"expected": true, "received": true,
"informative": true, "status": "computed", "light": "red", "reason": null}`.

## Падение против деградации

Нода падает с `ValueError` (доменного `reason_code` нет; текст называет порт и поле):

| Причина | Текст ошибки |
|---|---|
| `expected_tests` / `informative_tests` не строка, неизвестное имя, повтор, `km_test` не в ожидаемых или в информативных, информативный тест вне ожидаемых | `expected_tests …` / `informative_tests …` с указанием имени |
| `assessor_accuracy` или `red_assessor_accuracy` не число, `bool`, NaN, вне `0..1`; `assessor_accuracy = null` при вычисленном оценивании | `assessor_accuracy должен быть числом в диапазоне 0..1` |
| `assessment_result` не объект, чужой `contract_version`, `status` вне `computed`/`not_computable`, `not_computable` без `reason` | `assessment_result …` с указанием поля |
| `computed` с нарушением `1 <= scored_units <= total_units` (или не `int`) | `assessment_result computed требует 1 <= scored_units <= total_units` |
| вход `in*` не объект или без обязательных полей | `laim-agg.in<N> не содержит поля [...]` |
| неизвестный `test_name`, тест вне `expected_tests`, повтор теста | `неизвестный test_name=…`, `результат … не ожидается в этом запуске`, `результат … подключён повторно` |
| `status` вне допустимых, `calculated_traffic_lights` не объект | `….status=… не поддерживается` |
| нераспознанный цвет, `color != test_light`, `not_computable` без `gray` (и наоборот) | `… имеет несогласованные цвета` / `… status/color` |
| `km_test` без `km_name`, `km_baseline`, `km_monitoring`, `km_delta` | `km_test не содержит поля [...]` |

Деградации (нода отрабатывает, итог публикуется):

| Событие | Реакция |
|---|---|
| `km_test` красный, автоассессор допущен, данные пригодны | итог `red`, `quality_status = assessed` |
| `processing_report` не подключён или без `data_readiness`; `data_readiness.state` `insufficient` / `failed` | итог `amber`, `quality_status = not_assessed`, результат `km_test` не учитывается |
| `data_readiness.state = limited` | итог не выше `amber`, `quality_status = assessed` |
| `km_test` жёлтый | итог `amber` |
| `km_test` `not_computable` или не подключён | итог `amber`, `quality_status = not_assessed`, основание с `reason` теста |
| `assessment_result.status = not_computable` или `assessor_accuracy <= red_assessor_accuracy` | автоассессор не допущен: итог не выше `amber`, `quality_status = not_assessed`, результат `km_test` не учитывается |
| `calibration_metrics.admission_status = red` / `not_assessed` | то же: допуск ассессора имеет приоритет над порогом точности |
| `calibration_metrics.admission_status = amber` | итог не выше `amber`, `quality_status = assessed`, красный `km_test` сохраняется |
| Ожидаемый тест не подключён (в том числе информативный) | итог `amber`, тест в `missing_tests`, `registry.<тест>.received = false` |
| Информативный тест любого цвета или `not_computable` | итог не меняется; результат виден в `registry` и HTML |
| Дополнительный светофорный тест (ожидаемый, но не информативный) жёлтый, красный или `not_computable` | итог `amber` |
| `yellow` / `grey` во входе | приводятся к `amber` / `gray`, в `test_results` уходит нормализованный цвет |

## Внешние сервисы

Не применимо: нода не обращается к LLM-шлюзу, эмбеддингам, HDFS и сети.

## Наблюдаемость

В лог платформы уходит одна строка на прогон (`итог=`, `оценка=`,
`получено=`, `основания=`). Порта журнала нет — журналом служит сам
`all_results`: на сотне прогонов агрегируйте `color`, `quality_status`,
`missing_tests`, `reasons`, а по `registry.<тест>.status` и `reason`
находите источник жёлтого итога.

## Карта кода

```text
descriptor.json     порты, настройки expected_tests / informative_tests / red_assessor_accuracy, семафор и HTML в UI, sourceFiles
main.py             валидация входов и настроек, реестр, сборка all_results и report_html
table15.py          результат теста в терминах методики и решающая функция таблицы 15 (без I/O)
requirements.txt    только комментарий: зависимостей нет
tests/test_table15.py                      таблица истинности итога по всем комбинациям статусов
tests/test_monitoring_result_contract.py   контракт выхода, отказы по входам, настройки, sourceFiles
```

## Что делать, если

- **Итог `amber`, все тесты зелёные** — смотрите `reasons`: не подключён
  ожидаемый тест, автоассессор не прошёл порог, оценивание или КМ
  `not_computable`. Первый шаг — `all_results` соответствующей upstream-ноды.
- **Ожидали красный, получили `amber`** — красный возможен только от
  `km_test` при допущенном автоассессоре; красный дрейф-теста итог не
  формирует по методике. Проверьте `registry.assessor.light`.
- **Упала с `не ожидается в этом запуске`** — к `in` подключён тест, которого
  нет в `expected_tests`; исправьте настройку или wiring.
- **Упала с `несогласованные цвета` / `status/color`** — дефект upstream-теста
  (`color` и `test_light` разные или `not_computable` не серый); чинить тест.
- **`результат … подключён повторно`** — тест подключён к `in` дважды.
- **`km_test не содержит поля`** — `laim-km-dynamic-test` не публикует плоские поля `km_*`; проверить его версию.
- **Нужен другой порог автоассессора** — настройка `red_assessor_accuracy`; равенство порогу тоже не допускает.

## Деплой

База — `py312-simple`; функция `main` в `main.py`. `sourceFiles` в
`descriptor.json`: `main.py`, `table15.py`; тест
`test_descriptor_deploys_table15_and_settings` закрепляет список файлов и
дефолты настроек. `requirements.txt` объявлен в `libraryDependencies` и
намеренно содержит только комментарий: код на стандартной библиотеке, а без
файла сборка контейнера падает. CI `.github/workflows/ci.yml`: `ruff check .`,
`python -m pytest -q` на Python 3.12. ZIP ноды — `descriptor.json`,
`requirements.txt` и два файла `sourceFiles` из головы ветки `dev`.

## Глоссарий

- **Светофор итерации** — итог по таблице 15: `red`, `amber`, `green`.
- **Статус оценки качества** — `assessed` / `not_assessed`: получена ли
  надёжная оценка ключевой метрики; не является четвёртым цветом.
- **Реестр тестов** — `expected_tests` с отметкой, получен ли результат и
  каков его статус.
- **Информативный тест** — тест, результат которого публикуется, но в
  светофор не входит (`informative_tests`).
- **Автоассессор** — LLM-оценщик monitoring-выборки (`laim-asessor-agent`);
  `acc_auto` — его точность относительно разметки; допуск — тест 6.3.3.
- **КМ** — ключевая метрика качества агента; её уровень и динамику проверяет
  `km_test` (тест 6.3.4).
