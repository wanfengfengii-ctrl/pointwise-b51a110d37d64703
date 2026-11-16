class ArchiveError(Exception):
    """Base class for public archive errors."""


class ValidationError(ArchiveError, ValueError):
    pass


class NotFound(ArchiveError, LookupError):
    pass


class RequestConflict(ArchiveError):
    pass


class ResultNotReady(ArchiveError):
    pass
