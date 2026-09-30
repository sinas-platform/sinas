"""Admin password-reset token creation.

Field report from a fresh rc.6 install: POST /users/{id}/password-reset 500'd
with NotNullViolationError on password_reset_tokens.created_at. The model maps
created_at via the shared alias (server_default=func.now()), so SQLAlchemy
omits it from the INSERT and expects the DB to supply it — but the creating
migration declared the column NOT NULL with no default. Any deployment that
had never issued a reset link hit it the first time an admin tried.

These tests exercise the ORM insert path, so they fail against a schema whose
default is missing regardless of how the column got that way.
"""

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import consume_password_reset_token, create_password_reset_token
from app.models.user import PasswordResetToken, User


class TestPasswordResetToken:
    async def test_create_populates_created_at(self, db: AsyncSession, test_user: User):
        plain, record = await create_password_reset_token(db, str(test_user.id))
        assert plain
        # The actual regression: this INSERT raised NotNullViolationError
        assert record.created_at is not None

    async def test_schema_supplies_the_default(self, db: AsyncSession):
        """Guards the migration itself, not just the ORM behaviour."""
        default = (
            await db.execute(
                text(
                    "SELECT column_default FROM information_schema.columns "
                    "WHERE table_name = 'password_reset_tokens' "
                    "AND column_name = 'created_at'"
                )
            )
        ).scalar_one()
        assert default is not None, (
            "password_reset_tokens.created_at has no DB default; the model omits "
            "it from INSERTs, so every password-reset token creation will fail"
        )

    async def test_round_trip_consume(self, db: AsyncSession, test_user: User):
        plain, record = await create_password_reset_token(db, str(test_user.id))
        consumed = await consume_password_reset_token(db, plain)
        assert consumed is not None
        assert str(consumed.user_id) == str(test_user.id)
        assert consumed.used_at is not None
        # One-time: a second consume must fail
        assert await consume_password_reset_token(db, plain) is None
