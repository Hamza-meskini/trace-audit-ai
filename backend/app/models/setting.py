"""AppSetting model — persisted runtime configuration (AI model selection)."""

from datetime import datetime, timezone

from sqlalchemy import String, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AppSetting(Base):
    """Key/value store for settings that survive restarts.

    Currently stores the active AI model, provider, and thinking level so a
    UI change to the model configuration is not lost when the server restarts.
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Text] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
