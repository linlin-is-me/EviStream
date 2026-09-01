"""Stable governance and replay failures."""


class GovernanceError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
