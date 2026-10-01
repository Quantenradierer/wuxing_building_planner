"""Small street shops: gun shop, talismonger, ramen bar, boutique, deli."""

import pytest

from roomplanner.generator import generate
from roomplanner.params import BuildingType, Wealth
from roomplanner.rules import rules_for
from roomplanner.validation import hard_violations

from .conftest import make_params

SHOPS = {
    BuildingType.GUN_SHOP: ("gun_shop_floor", {"weapon_rack", "display_case"}, "strongroom"),
    BuildingType.TALISMONGER: ("talisman_shop", {"reagent_shelf", "counter"}, "ritual_room"),
    BuildingType.RAMEN_BAR: ("ramen_bar_room", {"ramen_counter", "stool"}, "prep_kitchen"),
    BuildingType.BOUTIQUE: ("boutique_floor", {"clothes_rack", "fitting_booth"}, "wc"),
    BuildingType.DELI: ("deli_floor", {"deli_counter", "shelf"}, "butchery"),
}


@pytest.mark.parametrize("building_type", SHOPS)
@pytest.mark.parametrize("wealth", [Wealth.SQUATTER, Wealth.MIDDLE, Wealth.LUXURY])
def test_a_small_shop_has_its_sales_floor_and_trade_room(
    building_type: BuildingType, wealth: Wealth
) -> None:
    hall, objects, room = SHOPS[building_type]
    params = make_params(building_type=building_type, width=26, depth=18, wealth=wealth, seed=1)
    building = generate(params)
    assert hard_violations(building, rules_for(building_type, wealth)) == []
    ground = building.floor(0)
    types = [r.type for r in ground.rooms]
    assert types.count(hall) == 1 and room in types and "wc" in types
    halls = {r.id for r in ground.rooms if r.type == hall}
    assert objects <= {o.kind for o in ground.objects if o.room in halls}


@pytest.mark.parametrize("building_type", SHOPS)
def test_the_owner_lives_upstairs(building_type: BuildingType) -> None:
    params = make_params(building_type=building_type, width=26, depth=20, floors_above=3, seed=2)
    building = generate(params)
    top, middle = building.floor(2), building.floor(1)
    assert {"flat_loft", "bathroom", "bedroom"} <= {r.type for r in top.rooms}
    assert "stockroom" in {r.type for r in middle.rooms}


def test_rich_gun_shops_have_a_firing_range_in_the_basement() -> None:
    for wealth, expected in ((Wealth.HIGH, True), (Wealth.LOW, False)):
        params = make_params(
            building_type=BuildingType.GUN_SHOP, width=28, depth=20, floors_below=1, wealth=wealth
        )
        basement = generate(params).floor(-1)
        lanes = [o for o in basement.objects if o.kind == "shooting_lane"]
        assert ("test_range" in {r.type for r in basement.rooms}) is expected
        assert bool(lanes) is expected
