from pathlib import Path

import pytest
import yaml
from PIL import Image

from roomplanner.errors import RulesError
from roomplanner.generator import generate
from roomplanner.params import BuildingType
from roomplanner.render.image import RenderOptions, render_building, render_floor
from roomplanner.render.theme import colour, load_theme

from .conftest import make_params

SMALL = RenderOptions(cell_px=8, padding=2)


def test_image_covers_the_building_plus_padding() -> None:
    building = generate(make_params(width=30, depth=20, floors_above=2))
    images = render_building(building, load_theme("neon"), SMALL)
    assert set(images) == {0, 1}
    assert images[0].size == ((30 + 4) * 8, (20 + 4) * 8)


def test_rendering_is_deterministic() -> None:
    building = generate(make_params(width=30, depth=20))
    theme = load_theme("neon")
    first = render_floor(building, building.floor(0), theme, SMALL)
    second = render_floor(building, building.floor(0), theme, SMALL)
    assert first.tobytes() == second.tobytes()


def test_walls_are_drawn_and_outside_stays_dark() -> None:
    building = generate(make_params(width=30, depth=20))
    theme = load_theme("neon")
    image = render_floor(building, building.floor(0), theme, RenderOptions(cell_px=20))
    corner = image.getpixel((2, 2))
    assert isinstance(corner, tuple)
    assert sum(corner[:3]) < 90
    # The west exterior wall runs along x = padding, halfway down the building.
    wall = image.getpixel((2 * 20, (2 + 10) * 20 + 10))
    assert isinstance(wall, tuple)
    assert sum(wall[:3]) > sum(colour(theme.background)[:3]) + 150


@pytest.mark.parametrize("building_type", list(BuildingType))
def test_every_building_type_renders_with_labels_and_grid(building_type: BuildingType) -> None:
    building = generate(make_params(building_type=building_type, width=60, depth=40))
    options = RenderOptions(cell_px=6, labels=True, grid=2)
    image = render_floor(building, building.floor(0), load_theme("neon"), options)
    assert image.mode == "RGB"


def test_custom_theme_file(tmp_path: Path) -> None:
    source = load_theme("neon").model_dump(mode="json")
    source["name"] = "bright"
    source["background"] = "#ffffff"
    source["outside_pattern"] = "plain"
    source["ambient"] = 1.0
    path = tmp_path / "bright.yaml"
    path.write_text(yaml.safe_dump(source))
    theme = load_theme(str(path))
    building = generate(make_params(width=20, depth=14))
    image = render_floor(building, building.floor(0), theme, SMALL)
    assert image.getpixel((1, 1)) == (255, 255, 255)


def test_invalid_themes_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(RulesError, match="unknown theme"):
        load_theme("nope")
    source = load_theme("neon").model_dump(mode="json")
    source["rooms"] = {"office": "marble"}
    path = tmp_path / "broken.yaml"
    path.write_text(yaml.safe_dump(source))
    with pytest.raises(RulesError, match="unknown materials marble"):
        load_theme(str(path))


def test_colours() -> None:
    assert colour("#102030") == (16, 32, 48, 255)
    assert colour("#10203040") == (16, 32, 48, 64)
    with pytest.raises(ValueError, match="invalid colour"):
        colour("#123")


def test_saved_png_opens(tmp_path: Path) -> None:
    building = generate(make_params(width=20, depth=14))
    target = tmp_path / "floor.png"
    render_floor(building, building.floor(0), load_theme("neon"), SMALL).save(target)
    with Image.open(target) as image:
        assert image.size == (24 * 8, 18 * 8)
