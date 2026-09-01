"""Stable Agent runtime failures exposed to CLI and job handlers."""


class AgentRuntimeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
