"""Typed live validation findings with backwards-compatible display strings."""


class ValidationIssue(str):
    """Strings remain usable by existing reports; control flow uses attributes.

    Persisted strings are revalidated from the contract before execution.
    """

    def __new__(cls, message: str, code: str = "STRUCTURE", path: str = "contract", blocking: bool = True):
        issue = super().__new__(cls, message)
        issue.code = code
        issue.path = path
        issue.blocking = blocking
        return issue
