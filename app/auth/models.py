"""User model — Google OAuth identity only, no password material."""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, TimestampMixin, UUIDPrimaryKeyMixin

ROLES = ("admin", "user")


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'user')", name="ck_users_role"),
    )

    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    google_id: Mapped[str] = mapped_column(
        Text, nullable=False, unique=True, index=True
    )
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default="user")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<User {self.email} role={self.role}>"
