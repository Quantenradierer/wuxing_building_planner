class RoomplannerError(Exception):
    """Base class for all expected, user-facing errors."""


class InfeasibleError(RoomplannerError):
    """The parameters can never produce a valid building (e.g. footprint too small)."""


class NotSupportedError(RoomplannerError):
    """The parameters are valid but not implemented yet."""


class SchemaError(RoomplannerError):
    """A JSON document does not match the expected schema version or structure."""


class RulesError(RoomplannerError):
    """A rules data file (room catalog or building program) is invalid."""
