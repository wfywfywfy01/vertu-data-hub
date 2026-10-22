from datetime import datetime, timedelta, timezone
import unittest
import uuid

import jwt

from app.api.auth import require_service_claims
from app.api.errors import ApiError


SECRET = "pytest-service-token-secret-at-least-32-bytes"


def _token(*, role="viewer", scope="none", capabilities=()):
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "iss": "pdca-workbench",
            "aud": "dealer-knowledge-hub",
            "sub": "pytest-reader",
            "user_id": "pytest-reader",
            "role": role,
            "scope": scope,
            "dealer_ids": [],
            "team_keys": [],
            "capabilities": list(capabilities),
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "jti": uuid.uuid4().hex,
        },
        SECRET,
        algorithm="HS256",
    )


class ServiceAuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app.api import auth

        self.settings = auth.settings
        self.old_secret = self.settings.service_token_secret
        self.old_key_file = self.settings.service_token_key_file
        self.settings.service_token_secret = SECRET
        self.settings.service_token_key_file = ""

    async def asyncTearDown(self):
        self.settings.service_token_secret = self.old_secret
        self.settings.service_token_key_file = self.old_key_file

    async def test_read_all_capability_does_not_grant_write_scope(self):
        claims = await require_service_claims(
            f"Bearer {_token(capabilities=['knowledge:read:all'])}"
        )
        dealer_id = uuid.uuid4()

        self.assertTrue(claims.read_unrestricted)
        self.assertFalse(claims.unrestricted)
        claims.require_read_dealer(dealer_id)
        with self.assertRaises(ApiError):
            claims.require_dealer(dealer_id)
        with self.assertRaises(ApiError):
            claims.require_role("sales", "manager", "admin")

    async def test_read_all_capability_rejects_non_viewer(self):
        with self.assertRaises(ApiError) as caught:
            await require_service_claims(
                f"Bearer {_token(role='sales', scope='self', capabilities=['knowledge:read:all'])}"
            )

        self.assertEqual(caught.exception.status_code, 401)
        self.assertEqual(caught.exception.code, "invalid_service_token")


if __name__ == "__main__":
    unittest.main()
