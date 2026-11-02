class AutoBenchError(Exception):
    """A user-facing failure which must not disclose authentication material."""


class ConfigurationFieldError(ValueError):
    """A configuration problem with a stable location for visual editors."""

    def __init__(self, message: str, path: list, code: str = "configuration"):
        super().__init__(message)
        self.path = path
        self.code = code


class ConnectionLost(AutoBenchError):
    pass


class AuthenticationFailed(AutoBenchError):
    pass


class RemoteError(AutoBenchError):
    pass


class UncertainSubmission(AutoBenchError):
    pass


class Paused(AutoBenchError):
    pass


class ChecksumError(AutoBenchError):
    pass
