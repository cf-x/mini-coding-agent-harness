"""Domain-specific exceptions."""


class HarnessError(Exception):
    """Base exception for harness failures."""


class ConfigurationError(HarnessError):
    """Raised when configuration is invalid."""


class PathOutsideWorkspaceError(HarnessError):
    """Raised when a tool path escapes the configured workspace."""


class ReplayExhaustedError(HarnessError):
    """Raised when a replay has no model responses left."""


class TraceFormatError(HarnessError):
    """Raised when a trace line cannot be parsed."""
