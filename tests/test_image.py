from pathlib import Path

import pytest
import yaml
from PIL import Image

from roomplanner.errors import RulesError
from roomplanner.generator import generate
from roomplanner.params import BuildingType, Wealth
from roomplanner.render.image import RenderOptions, render_building, render_floor, sprite_name
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


def test_theme_extends_a_bundled_theme(tmp_path: Path) -> None:
    path = tmp_path / "child.yaml"
    path.write_text("extends: neon\nname: child\nobjects: {desk: {shape: box}}\n")
    theme = load_theme(str(path))
    neon = load_theme("neon")
    assert theme.walls == neon.walls
    assert theme.object_style("desk").shape == "box"
    assert theme.object_style("bed") == neon.object_style("bed")


def test_objects_with_a_sprite_are_drawn_as_the_sprite(tmp_path: Path) -> None:
    building = generate(make_params(width=30, depth=20))
    floor = building.floor(0)
    marked = {(d.x, d.y) for d in floor.devices}
    obj = next(
        o for o in floor.objects if o.blocking and not {(c.x, c.y) for c in o.cells} & marked
    )
    sprites = tmp_path / "sprites"
    sprites.mkdir()
    Image.new("RGBA", (40, 20), (255, 0, 0, 255)).save(sprites / f"{obj.kind}.png")
    path = tmp_path / "sprited.yaml"
    path.write_text(
        "extends: neon\nname: sprited\nsprites: sprites\nambient: 1.0\nglow_radius: 0\nnoise: 0\n"
    )
    options = RenderOptions(cell_px=20, padding=2, lighting=False)
    image = render_floor(building, floor, load_theme(str(path)), options)
    centre = (round((obj.x + 2 + obj.w / 2) * 20), round((obj.y + 2 + obj.h / 2) * 20))
    pixel = image.getpixel(centre)
    assert isinstance(pixel, tuple)
    assert pixel[0] > 150 and pixel[0] > 3 * max(pixel[1], pixel[2])


def test_missing_sprite_directory_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("extends: neon\nname: broken\nsprites: nowhere\n")
    with pytest.raises(RulesError, match="sprite directory"):
        load_theme(str(path))


@pytest.mark.parametrize(
    ("tier", "expected"),
    [
        (Wealth.SQUATTER, "bed.squatter"),
        (Wealth.LOW, "bed"),
        (Wealth.MIDDLE, "bed"),
        (Wealth.HIGH, "bed.high"),
        (Wealth.LUXURY, "bed.high"),
    ],
)
def test_wealth_sprite_falls_back_towards_middle(tier: Wealth, expected: str) -> None:
    blank = Image.new("RGBA", (1, 1))
    sprites = {"bed": blank, "bed.squatter": blank, "bed.high": blank}
    assert sprite_name(sprites, "bed", tier) == expected
    assert sprite_name(sprites, "sofa", tier) is None
