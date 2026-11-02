class AutoBenchError(Exception):
    """A user-facing failure which must not disclose authentication material."""


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
