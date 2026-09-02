"""
Агрегатор светофоров по результатам тестов.

Правила агрегации:
- красных >= critical_red → 'red'
- иначе жёлтых (amber) >= critical_amber → 'amber'
- иначе есть хотя бы один цветной тест → 'green'
- ни одного цветного теста (все gray/неизвестные/пусто) → 'gray'

yellow приводится к amber, grey — к gray. Неизвестные значения цвета не
считаются ни в одну сторону и логируются: молчаливое игнорирование делало
опечатку в цвете неотличимой от зелёного результата.
"""
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_COLOR_ALIASES = {"yellow": "amber", "grey": "gray"}
_KNOWN_COLORS = ("red", "amber", "green", "gray")


def normalize_color(value: Any) -> str:
    """Цвет входа → канонический ('red'/'amber'/'green'/'gray'/'unknown')."""
    color = _COLOR_ALIASES.get(value, value)
    return color if color in _KNOWN_COLORS else "unknown"


def count_colors(inputs: List[Dict[str, Any]]) -> Dict[str, int]:
    """Счётчики цветов по входам (после нормализации)."""
    counts = {color: 0 for color in _KNOWN_COLORS + ("unknown",)}
    for item in inputs:
        counts[normalize_color((item or {}).get("color"))] += 1
    return counts


def aggregate(
    inputs: List[Dict[str, Any]],
    critical_red: int = 1,
    critical_amber: int = 1,
) -> str:
    """Итоговый цвет светофора по списку результатов тестов."""
    counts = count_colors(inputs)
    logger.info("[laim-agg] Счётчики цветов: %s", counts)
    if counts["unknown"]:
        logger.warning(
            "[laim-agg] %d входов с нераспознанным цветом — "
            "не участвуют в агрегации", counts["unknown"],
        )

    if counts["red"] >= critical_red:
        return "red"
    if counts["amber"] >= critical_amber:
        return "amber"
    if counts["red"] + counts["amber"] + counts["green"] == 0:
        logger.info("[laim-agg] Ни одного цветного светофора на входе")
        return "gray"
    return "green"
