"""Pass rates of the soft building-code warnings over a fixed seed sweep.

Guards the numbers in docs/requirements.md ("Building code"): a change that makes buildings
worse against MBO/ASR shows up here. Raise the floors when a rule improves.
"""

from collections import Counter

import pytest

from roomplanner.errors import InfeasibleError
from roomplanner.generator import generate
from roomplanner.model import Building
from roomplanner.params import BuildingType, GenerationParams

TYPES = [BuildingType.OFFICE, BuildingType.HOTEL, BuildingType.CLINIC, BuildingType.APARTMENT]
SEEDS = range(4)
RULES = {
    "escape distance": "farther than 35 m",
    "two routes": "escape route",
    "dead end": "dead-end",
    "window area": "window area",
    "workstation area": "workstation",
}
# Smallest share of buildings without the warning (measured, with some margin).
MIN_PASS = {
    "escape distance": 0.95,
    "two routes": 0.95,
    "dead end": 0.55,
    "window area": 0.7,
    "workstation area": 0.65,
}


@pytest.fixture(scope="module")
def sweep() -> list[Building]:
    out: list[Building] = []
    for t in TYPES:
        for seed in SEEDS:
            params = GenerationParams(
                building_type=t, width=60, depth=40, floors_above=2, seed=seed
            )
            try:
                out.append(generate(params))
            except InfeasibleError:
                continue
    return out


@pytest.mark.parametrize("rule", RULES)
def test_pass_rate(sweep: list[Building], rule: str) -> None:
    failing = Counter(
        i
        for i, b in enumerate(sweep)
        for w in b.warnings
        if w.startswith("building code") and RULES[rule] in w
    )
    rate = 1 - len(failing) / len(sweep)
    assert rate >= MIN_PASS[rule], f"{rule}: {rate:.0%} of {len(sweep)} buildings pass"
