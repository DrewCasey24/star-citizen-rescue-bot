import unittest
from types import SimpleNamespace

from fastapi import HTTPException

import dashboard_core as base


class PermissionsContractTests(unittest.TestCase):
    def request(self, *, user=True, can_manage=True, csrf="token"):
        return SimpleNamespace(
            session={
                "user": {"id": "123"} if user else None,
                "guilds": [{"id": "1", "name": "Test", "can_manage": can_manage}],
                "csrf": csrf,
            }
        )

    def test_dashboard_requires_manage_server(self):
        allowed = base.require_guild_access(self.request(can_manage=True), 1)
        self.assertEqual(str(allowed["id"]), "1")
        with self.assertRaises(HTTPException) as denied:
            base.require_guild_access(self.request(can_manage=False), 1)
        self.assertEqual(denied.exception.status_code, 403)

    def test_dashboard_requires_authentication(self):
        with self.assertRaises(HTTPException) as denied:
            base.require_guild_access(self.request(user=False), 1)
        self.assertEqual(denied.exception.status_code, 401)

    def test_mutations_require_matching_csrf(self):
        request = self.request(csrf="expected")
        base.require_csrf(request, "expected")
        with self.assertRaises(HTTPException) as denied:
            base.require_csrf(request, "wrong")
        self.assertEqual(denied.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
