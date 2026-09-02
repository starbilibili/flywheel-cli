"""User-facing error types for the CLI."""


class FlywheelError(Exception):
    """Base error for failures that should be shown without a traceback."""


class ConfigError(FlywheelError):
    """Raised when an evaluation configuration is missing or invalid."""


class ResourceError(FlywheelError):
    """Raised when a resource reference cannot be resolved."""


class EvaluationError(FlywheelError):
    """Raised when an evaluation cannot be planned or executed."""
