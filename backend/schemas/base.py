"""Shared base model."""
from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict


class UTCModel(BaseModel):
    """Base model: serializes naive datetime as UTC ('Z' suffix), supports ORM mode."""
    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={
            datetime: lambda v: (
                v.replace(tzinfo=timezone.utc).isoformat().replace('+00:00', 'Z')
                if v.tzinfo is None
                else v.isoformat().replace('+00:00', 'Z')
            )
        }
    )
