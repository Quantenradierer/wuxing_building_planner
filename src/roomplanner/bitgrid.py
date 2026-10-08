"""Cell sets as integer bitmasks, for the geometry the room assigner repeats thousands of times.

A `BitGrid` covers one bounding box; bit `(y - y0) * stride + (x - x0)` is the cell (x, y). The
stride is one wider than the box, so a shift along x never carries a cell into the next row.
Python ints are arbitrary size, so a shift, an AND or a popcount works on the whole floor at
once instead of walking cells one by one.
"""

from __future__ import annotations

from collections.abc import Iterable

from roomplanner.geometry import Cell, Side


class BitGrid:
    def __init__(self, x0: int, y0: int, x1: int, y1: int) -> None:
        self.x0, self.y0 = x0, y0
        self.width, self.height = x1 - x0, y1 - y0
        self.stride = self.width + 1
        self.rows = [((1 << self.width) - 1) << (r * self.stride) for r in range(self.height)]
        column = sum(1 << (r * self.stride) for r in range(self.height))
        self.columns = [column << c for c in range(self.width)]
        self.box = sum(self.rows)
        self._cells: dict[int, Cell] = {}

    # --- conversion -------------------------------------------------------------------

    def mask(self, cells: Iterable[Cell]) -> int:
        stride, x0, y0 = self.stride, self.x0, self.y0
        bits = 0
        for c in cells:
            x, y = c[0] - x0, c[1] - y0
            if 0 <= x < self.width and 0 <= y < self.height:
                bits |= 1 << (y * stride + x)
        return bits

    def cells(self, mask: int) -> frozenset[Cell]:
        found: list[Cell] = []
        stride, x0, y0 = self.stride, self.x0, self.y0
        for row, bits in enumerate(self.rows):
            line = (mask & bits) >> (row * stride)
            while line:
                low = line & -line
                found.append(Cell(x0 + low.bit_length() - 1, y0 + row))
                line ^= low
        return frozenset(found)

    def rect(self, x: int, y: int, width: int, height: int) -> int:
        line = ((1 << width) - 1) << (x - self.x0)
        bits = 0
        for r in range(y - self.y0, y - self.y0 + height):
            bits |= line << (r * self.stride)
        return bits

    # --- measures ---------------------------------------------------------------------

    def bbox(self, mask: int) -> tuple[int, int, int, int]:
        """(x0, y0, x1, y1) of a non-empty mask, like `regions.bbox`."""
        low, high = (mask & -mask).bit_length() - 1, mask.bit_length() - 1
        folded, step = mask, 1
        while step < self.height:
            folded |= folded >> (step * self.stride)
            step *= 2
        folded &= self.rows[0]
        return (
            self.x0 + (folded & -folded).bit_length() - 1,
            self.y0 + low // self.stride,
            self.x0 + folded.bit_length(),
            self.y0 + high // self.stride + 1,
        )

    def min_cell(self, mask: int) -> tuple[int, int]:
        """The smallest cell in (x, y) order, like `min(cells)`."""
        x0 = self.bbox(mask)[0]
        column = mask & self.columns[x0 - self.x0]
        return x0, self.y0 + ((column & -column).bit_length() - 1) // self.stride

    def lines(self, mask: int, vertical: bool) -> list[tuple[int, int, int]]:
        """(key, cells, count) of each non-empty column (or row) of the mask, in key order."""
        found: list[tuple[int, int, int]] = []
        for key, bits in enumerate(self.columns if vertical else self.rows):
            line = mask & bits
            if line:
                found.append((key, line, line.bit_count()))
        return found

    # --- connectivity -----------------------------------------------------------------

    def _grow(self, bits: int, within: int) -> int:
        stride = self.stride
        return (bits | bits << 1 | bits >> 1 | bits << stride | bits >> stride) & within

    def connected(self, mask: int) -> bool:
        if not mask:
            return False
        seen = mask & -mask
        while True:
            more = self._grow(seen, mask)
            if more == seen:
                return seen == mask
            seen = more

    def components(self, mask: int) -> list[int]:
        """4-connected pieces, largest first (ties by position, as `regions.components`)."""
        found: list[int] = []
        left = mask
        while left:
            seen = left & -left
            while True:
                more = self._grow(seen, left)
                if more == seen:
                    break
                seen = more
            found.append(seen)
            left ^= seen
        if len(found) > 1:
            found.sort(key=lambda piece: (-piece.bit_count(), self.min_cell(piece)))
        return found

    # --- shape ------------------------------------------------------------------------

    def thick(self, cells: int, space: int, enough: int) -> bool:
        """Is every cell of `cells` on a straight stretch of `space` (across and along) of at
        least `enough` cells? The mask version of `thinnest_extent(...) >= enough`."""
        if enough <= 1:
            return True
        for unit in (1, self.stride):
            starts = space
            for i in range(1, enough):
                starts &= space >> (i * unit)
            covered = 0
            for i in range(enough):
                covered |= starts << (i * unit)
            if cells & ~covered:
                return False
        return True

    def corners(self, mask: int) -> int:
        """Corners of the outline, like `geometry.corners`. Lattice point p has the cells
        around it at bits p, p + 1, p + stride and p + stride + 1 of the mask shifted up by a
        cell, so the left and top edges have a lattice point too."""
        stride = self.stride
        tl = mask << (stride + 1)
        tr, bl, br = tl >> 1, tl >> stride, tl >> (stride + 1)
        odd = tl ^ tr ^ bl ^ br
        diagonal = (tl & br & ~(tr | bl)) | (tr & bl & ~(tl | br))
        return odd.bit_count() + 2 * diagonal.bit_count()

    def blind(self, rest: int, border: int) -> int:
        """Cells of `rest` with no straight line through `rest` to `border`."""
        seen = rest & border
        for side in Side:
            dx, dy = side.delta
            shift = dx + dy * self.stride
            reach = rest & border
            while True:
                # a cell reaches if the neighbour towards `side` does
                more = reach | (rest & (reach >> shift if shift > 0 else reach << -shift))
                if more == reach:
                    break
                reach = more
            seen |= reach
        return rest.bit_count() - seen.bit_count()
