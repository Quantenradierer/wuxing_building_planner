import re

import pytest

from roomplanner.generator import generate
from roomplanner.params import BuildingType
from roomplanner.render.ascii import RESERVED_GLYPHS, render_building

from .conftest import make_params


@pytest.mark.parametrize("building_type", list(BuildingType))
def test_each_floor_uses_one_distinct_glyph_per_object_kind(building_type: BuildingType) -> None:
    text = render_building(
        generate(make_params(building_type=building_type, width=48, depth=48, seed=1))
    )
    legends = re.findall(r"^  objects: (.*)$", text, flags=re.M)
    assert legends
    for legend in legends:
        glyphs = [entry[0] for entry in legend.split(", ")]
        assert len(glyphs) == len(set(glyphs)), legend
        assert not RESERVED_GLYPHS & set(glyphs), legend
