import pytest

from roomplanner.geometry import Axis, Cell, Edge, Side, boundary_edges, meters_to_cells, rectangle


@pytest.mark.parametrize("side", list(Side))
def test_edge_of_separates_cell_from_its_neighbour(side: Side) -> None:
    cell = Cell(3, 4)
    edge = Edge.of(cell, side)
    assert set(edge.cells()) == {cell, cell.neighbour(side)}
    assert edge.axis is side.axis


def test_neighbouring_cells_share_one_edge() -> None:
    assert Edge.of(Cell(0, 0), Side.E) == Edge.of(Cell(1, 0), Side.W) == Edge(1, 0, Axis.V)
    assert Edge.between(Cell(0, 0), Cell(0, 1)) == Edge(0, 1, Axis.H)


def test_between_rejects_non_adjacent_cells() -> None:
    with pytest.raises(ValueError, match="not adjacent"):
        Edge.between(Cell(0, 0), Cell(1, 1))


def test_next_along_follows_the_line() -> None:
    assert Edge(2, 3, Axis.H).next_along() == Edge(3, 3, Axis.H)
    assert Edge(2, 3, Axis.V).next_along() == Edge(2, 4, Axis.V)


@pytest.mark.parametrize(("meters", "cells"), [(0.5, 1), (1.0, 2), (1.2, 2), (1.25, 3), (30, 60)])
def test_meters_snap_to_half_meter_grid(meters: float, cells: int) -> None:
    assert meters_to_cells(meters) == cells


def test_rectangle_boundary_is_its_perimeter() -> None:
    assert len(boundary_edges(rectangle(0, 0, 4, 3))) == 2 * (4 + 3)
