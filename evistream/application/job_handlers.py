"""Stage 0 job handlers."""

from evistream.application.types import JobRequest


class DemoJobHandler:
    async def handle(self, request: JobRequest) -> dict[str, str]:
        message = request.payload.get("message")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("payload.message must be a non-empty string")
        normalized = message.strip()
        return {"message": normalized, "uppercase": normalized.upper()}
