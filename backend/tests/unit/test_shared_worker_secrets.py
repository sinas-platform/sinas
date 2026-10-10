"""A shared worker loads the secrets a function may use. One that can't be
decrypted is skipped with a warning; it used to raise NameError (the module
had no logger), failing the whole function run."""

import logging
import uuid

from app.core.encryption import encryption_service
from app.models.secret import Secret
from app.services.shared_worker_manager import SharedWorkerManager


async def test_an_unreadable_secret_is_skipped_not_fatal(db, admin_user, caplog):
    good, bad = f"OK_{uuid.uuid4().hex[:6]}", f"BAD_{uuid.uuid4().hex[:6]}"
    db.add_all([
        Secret(user_id=admin_user.id, name=good, encrypted_value=encryption_service.encrypt("v"), visibility="shared"),
        Secret(user_id=admin_user.id, name=bad, encrypted_value="not-a-fernet-token", visibility="shared"),
    ])
    await db.flush()
    with caplog.at_level(logging.WARNING):
        secrets = await SharedWorkerManager()._load_secrets(db)
    assert secrets[good] == "v" and bad not in secrets
    assert any(bad in r.getMessage() for r in caplog.records)
