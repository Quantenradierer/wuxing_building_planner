"""Generation pipeline. Importing this package registers the built-in strategies."""

from roomplanner.pipeline import condition, footprint, furnishing, lights, openings, security
from roomplanner.pipeline.layout import corridor, hall, partition, room
from roomplanner.pipeline.run import run

# Imported for their @register side effects.
BUILTIN_STRATEGY_MODULES = (
    footprint,
    corridor,
    hall,
    partition,
    room,
    openings,
    furnishing,
    lights,
    security,
    condition,
)

__all__ = ["run"]
