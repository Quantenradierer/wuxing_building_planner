"""Generation pipeline. Importing this package registers the built-in strategies."""

from roomplanner.pipeline import footprint, furnishing, openings
from roomplanner.pipeline.layout import corridor, hall
from roomplanner.pipeline.run import run

# Imported for their @register side effects.
BUILTIN_STRATEGY_MODULES = (footprint, corridor, hall, openings, furnishing)

__all__ = ["run"]
