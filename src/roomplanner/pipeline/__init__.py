"""Generation pipeline. Importing this package registers the built-in strategies."""

from roomplanner.pipeline import footprint, openings
from roomplanner.pipeline.layout import corridor
from roomplanner.pipeline.run import run

# Imported for their @register side effects.
BUILTIN_STRATEGY_MODULES = (footprint, corridor, openings)

__all__ = ["run"]
