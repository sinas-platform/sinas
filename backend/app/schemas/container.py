"""Container schemas."""

from pydantic import BaseModel


class ScaleRequest(BaseModel):
    """Request to scale sandbox containers."""

    target: int
