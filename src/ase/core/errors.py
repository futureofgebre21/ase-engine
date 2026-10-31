"""Structured errors. Every error carries a stable machine-readable code."""

from __future__ import annotations


class AseError(Exception):
    """Base class for all expected ASE errors."""

    code: str = "ase_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ConfigurationError(AseError):
    code = "configuration_error"


class PathSafetyError(AseError):
    """A path escaped (or tried to escape) the allowed workspace."""

    code = "path_safety_error"


class UnsafeUrlError(AseError):
    """A repository URL failed validation."""

    code = "unsafe_url"


class InvalidTransitionError(AseError):
    """A run tried to move between states that are not allowed."""

    code = "invalid_state_transition"


class PolicyViolationError(AseError):
    """A proposed action was blocked by the deterministic policy layer."""

    code = "policy_violation"


class BudgetExceededError(AseError):
    """A configured limit (calls, repairs, time, size) was reached."""

    code = "budget_exceeded"


class RunNotFoundError(AseError):
    code = "run_not_found"


class RepositoryImportError(AseError):
    code = "repository_import_error"


class LLMTransientError(AseError):
    """Timeouts, rate limits, 5xx: worth retrying with backoff."""

    code = "llm_transient_error"


class LLMResponseError(AseError):
    """The model answered, but not in the required structure."""

    code = "llm_response_error"


class LLMRequestError(AseError):
    """The model server refused the request (bad request, unknown model, ...)."""

    code = "llm_request_error"


class ChangeRejected(AseError):
    """A generated change failed a deterministic check and was not applied."""

    code = "change_rejected"
