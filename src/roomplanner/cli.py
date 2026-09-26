from __future__ import annotations

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
from roomplanner.serialization import from_json, to_json

app = typer.Typer(no_args_is_help=True, help="Generate Shadowrun/cyberpunk building battle maps.")


class OutputFormat(StrEnum):
    ASCII = "ascii"
    JSON = "json"


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
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
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
    _emit(building, output_format, output)


@app.command()
def render(
    source: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
) -> None:
    """Render a saved JSON building as ASCII."""
    try:
        building = from_json(source.read_text(encoding="utf-8"))
    except RoomplannerError as error:
        _fail(str(error))
    _emit(building, OutputFormat.ASCII, output)


def _emit(building: Building, output_format: OutputFormat, output: Path | None) -> None:
    text = render_building(building) if output_format is OutputFormat.ASCII else to_json(building)
    if output is None:
        typer.echo(text, nl=output_format is OutputFormat.JSON)
    else:
        output.write_text(text, encoding="utf-8")
    for warning in building.warnings:
        typer.echo(f"warning: {warning}", err=True)


def _fail(message: str) -> NoReturn:
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(1)
