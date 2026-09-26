"""Subdivide a unit (apartment, …) that spans a strip from corridor to facade.

Deep enough, the hall is T-shaped and every room opens onto it:

    facade   | back[0] | back[1..] | back_fill … |
             |       hall (along the unit)       |
    corridor | front[0] | hall | front[1..]      |

Shallower: the hall only reaches in from the corridor, the first back room lies behind it
and the other back rooms are entered through that one. Too small for two zones: one room.
With `hall_in_back` the hall (as wide as the first back room's minimum side) is part of
that back room, which is then the unit's entry; there is no spine. Units too narrow for
that, or so deep that the back room would exceed its maximum area, keep their hall.
"""

from __future__ import annotations

import random

from roomplanner.pipeline.base import PlannedRoom
from roomplanner.pipeline.layout.frame import Band, Frame, Interval, LocalSide
from roomplanner.rules import Rules, UnitSpec

FRONT_SHARE = 0.4  # of the strip depth, before minimum sizes


def subdivide(
    frame: Frame,
    band: Band,
    span: Interval,
    unit: str,
    spec: UnitSpec,
    rules: Rules,
    rng: random.Random,
) -> list[PlannedRoom]:
    depth, width = band.depth, span.width
    flip = rng.random() < 0.5

    def room(kind: str, u0: int, u1: int, d0: int, d1: int, entry: bool = False) -> PlannedRoom:
        """Rectangle in unit coordinates: u from the unit's start, d from the corridor."""
        if flip:
            u0, u1 = width - u1, width - u0
        if band.corridor_at is LocalSide.V0:
            v0, v1 = band.v0 + d0, band.v0 + d1
        else:
            v0, v1 = band.v1 - d1, band.v1 - d0
        cells = frame.rect(span.u0 + u0, span.u0 + u1, v0, v1)
        return PlannedRoom(kind, cells, unit=unit, entry=entry)

    def side(kind: str) -> int:
        return rules.spec(kind).min_side

    back_min = max(side(k) for k in spec.back)
    # Too narrow for an entry leg as wide as the back room: keep a hall of its own.
    merge = spec.hall_in_back and width >= max(spec.hall_width, side(spec.back[0])) + back_min
    hall_width = max(spec.hall_width, side(spec.back[0])) if merge else spec.hall_width
    front_min = max([hall_width, *(side(k) for k in spec.front)])
    fits_spine = depth - hall_width >= front_min + back_min
    spine = hall_width if fits_spine and not merge else 0
    front_depth = round((depth - spine) * FRONT_SHARE)
    # Not so deep that a front room at its minimum width exceeds its maximum area.
    for kind in spec.front:
        front_depth = min(front_depth, rules.spec(kind).area[1] // side(kind))
    front_depth = max(front_min, front_depth)
    back_depth = depth - front_depth - spine
    first_front = spec.front[0] if spec.front else None

    if back_depth < back_min or width < hall_width + back_min:
        # Too small for two zones: one room, entered from the corridor.
        return [room(spec.back[0], 0, width, 0, depth, entry=True)]

    # Corridor side: [front[0]] hall [front[1:]], each at least its minimum side.
    rooms: list[PlannedRoom] = []
    fronts = list(spec.front)
    widths = _share(width - hall_width, [side(k) for k in fronts], fronts, rules)
    while fronts and widths is None:
        fronts.pop()
        widths = _share(width - hall_width, [side(k) for k in fronts], fronts, rules)
    widths = widths or []
    fronts, widths = _front_fill(fronts, widths, front_depth, spec, rules)
    order: list[tuple[str, int]] = []
    if fronts and fronts[0] == first_front:
        order.append((fronts[0], widths[0]))
        rest = list(zip(fronts[1:], widths[1:], strict=True))
    else:
        rest = list(zip(fronts, widths, strict=True))
    order.append((spec.hall, hall_width))
    order += rest
    position = 0
    hall_end = 0
    for kind, size in order:
        rooms.append(room(kind, position, position + size, 0, front_depth, kind == spec.hall))
        position += size
        if kind == spec.hall:
            hall_end = position
    if position < width:  # no front rooms: the hall takes the corridor side
        rooms[-1] = room(spec.hall, position - hall_width, width, 0, front_depth, True)
    if spine:
        hall = next(i for i, r in enumerate(rooms) if r.entry)
        along = room(spec.hall, 0, width, front_depth, front_depth + spine)
        rooms[hall] = PlannedRoom(spec.hall, rooms[hall].cells | along.cells, unit, entry=True)
        hall_end = 0  # every back room touches the hall

    # Facade side: back[0] covers everything up to the hall's far edge, then the others.
    backs = [spec.back[0]]
    sizes = [max(hall_end, side(spec.back[0]))]
    remaining = width - sizes[0]
    for kind in [*spec.back[1:], *([spec.back_fill] * width if spec.back_fill else [])]:
        if side(kind) > back_depth:
            continue
        low, high = rules.spec(kind).area
        target = max(side(kind), round(rng.randint(low, high) / back_depth))
        if remaining < side(kind):
            break
        size = min(target, remaining)
        backs.append(kind)
        sizes.append(size)
        remaining -= size
    # What is left goes to the rooms furthest below their maximum, not all to the last one.
    maxima = [rules.spec(k).area[1] for k in backs]
    for _ in range(remaining):
        i = min(range(len(sizes)), key=lambda i: (sizes[i] * back_depth / maxima[i], -i))
        sizes[i] += 1
    position = 0
    for kind, size in zip(backs, sizes, strict=True):
        rooms.append(room(kind, position, position + size, front_depth + spine, depth))
        position += size
    if merge:
        hall = next(i for i, r in enumerate(rooms) if r.entry)
        first = len(rooms) - len(backs)
        cells = rooms[hall].cells | rooms[first].cells
        if len(cells) > rules.spec(spec.back[0]).area[1]:  # deep strip: a hall of its own
            plain = spec.model_copy(update={"hall_in_back": False})
            return subdivide(frame, band, span, unit, plain, rules, rng)
        rooms[first] = PlannedRoom(spec.back[0], cells, unit=unit, entry=True)
        del rooms[hall]
    return rooms


def _front_fill(
    fronts: list[str], widths: list[int], depth: int, spec: UnitSpec, rules: Rules
) -> tuple[list[str], list[int]]:
    """Cap the front rooms at their maximum area; `front_fill` rooms take the spare width."""
    if spec.front_fill is None or not fronts:
        return fronts, widths

    def cap(kind: str) -> int:
        room = rules.spec(kind)
        return max(room.min_side, room.area[1] // depth)

    capped = [min(w, cap(k)) for k, w in zip(fronts, widths, strict=True)]
    spare = sum(widths) - sum(capped)
    fill_side = rules.spec(spec.front_fill).min_side
    if spare < fill_side:
        return fronts, widths
    count = max(1, min(-(-spare // cap(spec.front_fill)), spare // fill_side))
    fills = [spare // count + (i < spare % count) for i in range(count)]
    return [*fronts, *[spec.front_fill] * count], [*capped, *fills]


def _share(total: int, minimums: list[int], kinds: list[str], rules: Rules) -> list[int] | None:
    """Split `total` among rooms proportional to their typical area, respecting minimums."""
    if sum(minimums) > total:
        return None
    if not kinds:
        return []
    weights = [sum(rules.spec(k).area) for k in kinds]
    sizes = [max(m, total * w // sum(weights)) for m, w in zip(minimums, weights, strict=True)]
    while sum(sizes) > total:
        largest = max(range(len(sizes)), key=lambda i: sizes[i] - minimums[i])
        sizes[largest] -= 1
    sizes[-1] += total - sum(sizes)
    return sizes
