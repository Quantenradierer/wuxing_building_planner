from __future__ import annotations

import webbrowser
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from pydantic import ValidationError

from roomplanner.errors import RoomplannerError
from roomplanner.export.common import ExportOptions
from roomplanner.export.foundry import to_foundry
from roomplanner.export.uvtt import uvtt_json
from roomplanner.generator import generate as generate_building
from roomplanner.geometry import Side
from roomplanner.model import Building
from roomplanner.params import (
    BuildingType,
    Condition,
    EntranceKind,
    GenerationParams,
    Security,
    Shape,
    Wealth,
)
from roomplanner.render.ascii import render_building
from roomplanner.render.image import DEFAULT_CELL_PX, RenderOptions
from roomplanner.render.image import render_building as render_images
from roomplanner.render.theme import load_theme
from roomplanner.serialization import from_json, to_json
from roomplanner.ui.server import make_server

app = typer.Typer(no_args_is_help=True, help="Generate Shadowrun/cyberpunk building battle maps.")


class OutputFormat(StrEnum):
    ASCII = "ascii"
    JSON = "json"
    PNG = "png"
    WEBP = "webp"
    DD2VTT = "dd2vtt"  # Universal VTT, one file per floor
    FOUNDRY = "foundry"  # Foundry VTT scenes folder with an import macro


IMAGE_FORMATS = (OutputFormat.PNG, OutputFormat.WEBP)

ThemeOption = Annotated[str, typer.Option(help="Bundled theme name or theme YAML file")]
CellPxOption = Annotated[int, typer.Option(min=4, max=400, help="Image pixels per cell")]
LabelsOption = Annotated[bool, typer.Option(help="Write room types into images")]
GridOption = Annotated[int, typer.Option(min=0, help="Image grid line every n cells (0: none)")]
GridMOption = Annotated[
    float, typer.Option("--grid-m", help="VTT exports: grid square size in metres")
]
LightsOption = Annotated[bool, typer.Option(help="VTT exports: include light sources")]
BakedOption = Annotated[
    bool, typer.Option(help="VTT exports: bake the lighting into the image (VTT stays bright)")
]


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
    entrances: Annotated[
        str | None,
        typer.Option(help="Comma-separated entrance kinds (main, service, emergency, roof)"),
    ] = None,
    seed: Annotated[int | None, typer.Option(help="Random if omitted")] = None,
    output_format: Annotated[OutputFormat, typer.Option("--format", "-f")] = OutputFormat.ASCII,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output", "-o", help="Images, dd2vtt: one file per floor, suffixed; foundry: folder"
        ),
    ] = None,
    theme: ThemeOption = "neon",
    cell_px: CellPxOption = DEFAULT_CELL_PX,
    labels: LabelsOption = False,
    grid: GridOption = 0,
    grid_m: GridMOption = 1.0,
    lights: LightsOption = True,
    baked_lighting: BakedOption = False,
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
            entrances=_entrances(entrances),
            seed=seed,
        )
        building = generate_building(params)
    except (ValidationError, ValueError) as error:
        _fail(str(error))
    except RoomplannerError as error:
        _fail(str(error))
    _emit(
        building,
        output_format,
        output,
        _ImageSettings(theme, cell_px, labels, grid, grid_m, lights),
    )


@app.command()
def render(
    source: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    output_format: Annotated[OutputFormat, typer.Option("--format", "-f")] = OutputFormat.ASCII,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    theme: ThemeOption = "neon",
    cell_px: CellPxOption = DEFAULT_CELL_PX,
    labels: LabelsOption = False,
    grid: GridOption = 0,
    grid_m: GridMOption = 1.0,
    lights: LightsOption = True,
    baked_lighting: BakedOption = False,
) -> None:
    """Render a saved JSON building as ASCII or images."""
    try:
        building = from_json(source.read_text(encoding="utf-8"))
    except RoomplannerError as error:
        _fail(str(error))
    _emit(
        building,
        output_format,
        output,
        _ImageSettings(theme, cell_px, labels, grid, grid_m, lights),
    )


@app.command()
def ui(
    port: Annotated[int, typer.Option(help="Port to listen on")] = 8000,
    host: Annotated[str, typer.Option(help="Interface to listen on")] = "127.0.0.1",
    browser: Annotated[bool, typer.Option(help="Open the page in the browser")] = True,
) -> None:
    """Start the local web UI: all parameters, floor viewer with toggleable layers."""
    try:
        server = make_server(host, port)
    except OSError as error:
        _fail(f"cannot listen on {host}:{port}: {error.strerror}")
    url = f"http://{host}:{server.server_port}/"
    typer.echo(f"Roomplanner UI on {url} (Ctrl+C to stop)")
    if browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


@dataclass(frozen=True)
class _ImageSettings:
    theme: str
    cell_px: int
    labels: bool
    grid: int
    grid_m: float = 1.0
    lights: bool = True
    baked_lighting: bool = False


def _emit(
    building: Building, output_format: OutputFormat, output: Path | None, image: _ImageSettings
) -> None:
    if output_format in IMAGE_FORMATS:
        _write_images(building, output_format, output, image)
        return
    if output_format in (OutputFormat.DD2VTT, OutputFormat.FOUNDRY):
        _export_vtt(building, output_format, output, image)
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
    base.parent.mkdir(parents=True, exist_ok=True)
    for level, picture in render_images(building, theme, options).items():
        target = floor_path(base, level, output_format.value)
        picture.save(target)
        typer.echo(str(target))
    for warning in building.warnings:
        typer.echo(f"warning: {warning}", err=True)


def _export_vtt(
    building: Building, output_format: OutputFormat, output: Path | None, settings: _ImageSettings
) -> None:
    try:
        theme = load_theme(settings.theme)
        options = ExportOptions(
            settings.grid_m, settings.cell_px, settings.lights, settings.baked_lighting
        )
        options.cells_per_square  # noqa: B018 - validates the grid size early
        stem = f"{building.params.building_type}_{building.seed}"
        if output_format is OutputFormat.DD2VTT:
            base = output or Path(f"{stem}.dd2vtt")
            base.parent.mkdir(parents=True, exist_ok=True)
            for floor in building.floors:
                target = floor_path(base, floor.level, "dd2vtt")
                target.write_text(uvtt_json(building, floor, theme, options), encoding="utf-8")
                typer.echo(str(target))
        else:
            folder = output or Path(stem)
            export = to_foundry(building, theme, options, folder.name)
            for path in export.write(folder):
                typer.echo(str(path))
    except (RoomplannerError, OSError) as error:
        _fail(str(error))
    for warning in building.warnings:
        typer.echo(f"warning: {warning}", err=True)


def _entrances(value: str | None) -> tuple[EntranceKind, ...] | None:
    if value is None:
        return None
    return tuple(EntranceKind(part.strip()) for part in value.split(",") if part.strip())


def floor_path(base: Path, level: int, extension: str) -> Path:
    """`map.png` -> `map_F0.png`, `map_F1.png`, `map_B1.png`."""
    tag = f"F{level}" if level >= 0 else f"B{-level}"
    return base.with_name(f"{base.stem}_{tag}.{extension}")


def _fail(message: str) -> NoReturn:
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(1)
