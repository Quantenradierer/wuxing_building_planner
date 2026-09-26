from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from pydantic import ValidationError

from roomplanner.errors import RoomplannerError
from roomplanner.generator import generate as generate_building
from roomplanner.geometry import Side
from roomplanner.model import Building
from roomplanner.params import BuildingType, Condition, GenerationParams, Security, Shape, Wealth
from roomplanner.render.ascii import render_building
from roomplanner.render.image import DEFAULT_CELL_PX, RenderOptions
from roomplanner.render.image import render_building as render_images
from roomplanner.render.theme import load_theme
from roomplanner.serialization import from_json, to_json

app = typer.Typer(no_args_is_help=True, help="Generate Shadowrun/cyberpunk building battle maps.")


class OutputFormat(StrEnum):
    ASCII = "ascii"
    JSON = "json"
    PNG = "png"
    WEBP = "webp"


IMAGE_FORMATS = (OutputFormat.PNG, OutputFormat.WEBP)

ThemeOption = Annotated[str, typer.Option(help="Bundled theme name or theme YAML file")]
CellPxOption = Annotated[int, typer.Option(min=4, max=400, help="Image pixels per cell")]
LabelsOption = Annotated[bool, typer.Option(help="Write room types into images")]
GridOption = Annotated[int, typer.Option(min=0, help="Image grid line every n cells (0: none)")]


@app.command()
def generate(
    building_type: Annotated[BuildingType, typer.Option("--type", "-t")],
    width: Annotated[int, typer.Option("--width", "-w", help="East-west extent in cells")],
    depth: Annotated[int, typer.Option("--depth", "-d", help="North-south extent in cells")],
    floors_above: Annotated[int, typer.Option(help="Floors above ground incl. ground")] = 1,
    floors_below: Annotated[int, typer.Option(help="Basement levels")] = 0,
    wealth: Wealth = Wealth.MIDDLE,
    condition: Condition = Condition.MAINTAINED,
    security: Security = Security.LOW,
    shape: Shape = Shape.RECTANGLE,
    street_side: Annotated[Side, typer.Option(case_sensitive=False)] = Side.S,
    service_side: Annotated[
        Side | None, typer.Option(case_sensitive=False, help="Default: opposite of street side")
    ] = None,
    seed: Annotated[int | None, typer.Option(help="Random if omitted")] = None,
    output_format: Annotated[OutputFormat, typer.Option("--format", "-f")] = OutputFormat.ASCII,
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Images: one file per floor, suffixed")
    ] = None,
    theme: ThemeOption = "neon",
    cell_px: CellPxOption = DEFAULT_CELL_PX,
    labels: LabelsOption = False,
    grid: GridOption = 0,
) -> None:
    """Generate a building."""
    try:
        params = GenerationParams(
            building_type=building_type,
            width=width,
            depth=depth,
            floors_above=floors_above,
            floors_below=floors_below,
            wealth=wealth,
            condition=condition,
            security=security,
            shape=shape,
            street_side=street_side,
            service_side=service_side,  # pyright: ignore[reportArgumentType]  # None = default
            seed=seed,
        )
        building = generate_building(params)
    except ValidationError as error:
        _fail(str(error))
    except RoomplannerError as error:
        _fail(str(error))
    _emit(building, output_format, output, _ImageSettings(theme, cell_px, labels, grid))


@app.command()
def render(
    source: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    output_format: Annotated[OutputFormat, typer.Option("--format", "-f")] = OutputFormat.ASCII,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    theme: ThemeOption = "neon",
    cell_px: CellPxOption = DEFAULT_CELL_PX,
    labels: LabelsOption = False,
    grid: GridOption = 0,
) -> None:
    """Render a saved JSON building as ASCII or images."""
    try:
        building = from_json(source.read_text(encoding="utf-8"))
    except RoomplannerError as error:
        _fail(str(error))
    _emit(building, output_format, output, _ImageSettings(theme, cell_px, labels, grid))


@dataclass(frozen=True)
class _ImageSettings:
    theme: str
    cell_px: int
    labels: bool
    grid: int


def _emit(
    building: Building, output_format: OutputFormat, output: Path | None, image: _ImageSettings
) -> None:
    if output_format in IMAGE_FORMATS:
        _write_images(building, output_format, output, image)
        return
    text = render_building(building) if output_format is OutputFormat.ASCII else to_json(building)
    if output is None:
        typer.echo(text, nl=output_format is OutputFormat.JSON)
    else:
        output.write_text(text, encoding="utf-8")
    for warning in building.warnings:
        typer.echo(f"warning: {warning}", err=True)


def _write_images(
    building: Building, output_format: OutputFormat, output: Path | None, settings: _ImageSettings
) -> None:
    try:
        theme = load_theme(settings.theme)
    except (RoomplannerError, OSError) as error:
        _fail(str(error))
    options = RenderOptions(settings.cell_px, labels=settings.labels, grid=settings.grid)
    base = output or Path(f"{building.params.building_type}_{building.seed}.{output_format}")
    for level, picture in render_images(building, theme, options).items():
        target = floor_path(base, level, output_format.value)
        picture.save(target)
        typer.echo(str(target))
    for warning in building.warnings:
        typer.echo(f"warning: {warning}", err=True)


def floor_path(base: Path, level: int, extension: str) -> Path:
    """`map.png` -> `map_F0.png`, `map_F1.png`, `map_B1.png`."""
    tag = f"F{level}" if level >= 0 else f"B{-level}"
    return base.with_name(f"{base.stem}_{tag}.{extension}")


def _fail(message: str) -> NoReturn:
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(1)
