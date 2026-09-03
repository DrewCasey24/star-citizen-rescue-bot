import unittest
from contextlib import asynccontextmanager

import incident_transitions as transitions


class FakeConn:
    def __init__(self):
        self.state = {
            "guild_id": 1,
            "incident_number": 42,
            "channel_id": 100,
            "status": "awaiting_responder",
            "priority": "standard",
            "primary_responder_id": None,
            "responded_at": None,
            "arrived_at": None,
            "backup_requested_at": None,
            "closed_at": None,
        }
        self.responders = set()
        self.events = []

    @asynccontextmanager
    async def transaction(self):
        yield self

    async def fetchrow(self, query, *args):
        if "FROM rescue_incidents" in query:
            return dict(self.state)
        return None

    async def fetchval(self, query, *args):
        if "rescue_incident_responders" in query:
            return 1 if int(args[1]) in self.responders else None
        return None

    async def execute(self, query, *args):
        compact = " ".join(query.split())
        if "INSERT INTO rescue_incident_events" in compact:
            self.events.append((args[3], args[4], args[5]))
        elif "INSERT INTO rescue_incident_responders" in compact:
            self.responders.add(int(args[1]))
        elif "DELETE FROM rescue_incident_responders" in compact:
            self.responders.discard(int(args[1]))
        elif "SET status='en_route',primary_responder_id=$2" in compact:
            self.state.update(status="en_route", primary_responder_id=int(args[1]), responded_at=object())
        elif "SET status='on_scene',arrived_at=NOW()" in compact:
            self.state.update(status="on_scene", arrived_at=object())
        elif "SET status='backup_requested',backup_requested_at=NOW()" in compact:
            self.state.update(status="backup_requested", backup_requested_at=object())
        elif "SET priority=$2" in compact:
            self.state["priority"] = args[1]
        elif "SET status='closed',closed_at=NOW(),closed_by_id=$2" in compact:
            self.state.update(status="closed", closed_at=object())
        return "UPDATE 1"


class FakePool:
    def __init__(self):
        self.conn = FakeConn()

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


class FakeBot:
    def __init__(self):
        self.db_pool = FakePool()


class LifecycleSmokeTest(unittest.IsolatedAsyncioTestCase):
    async def test_full_rescue_lifecycle(self):
        bot = FakeBot()
        channel = 100

        changed, reason = await transitions.transition_incident(bot, channel, "respond", 101)
        self.assertEqual((changed, reason), (True, "responded"))
        self.assertEqual(bot.db_pool.conn.state["primary_responder_id"], 101)

        changed, reason = await transitions.join_responder(bot, channel, 202)
        self.assertEqual((changed, reason), (True, "joined"))
        self.assertIn(202, bot.db_pool.conn.responders)

        changed, reason = await transitions.transition_incident(bot, channel, "arrived", 101)
        self.assertEqual((changed, reason), (True, "arrived"))

        changed, reason = await transitions.transition_incident(bot, channel, "backup", 101)
        self.assertEqual((changed, reason), (True, "backup_requested"))

        changed, reason = await transitions.transition_priority(
            bot, channel, "urgent", 101, expected_priority="standard"
        )
        self.assertEqual((changed, reason), (True, "priority_changed"))
        self.assertEqual(bot.db_pool.conn.state["priority"], "urgent")

        changed, reason = await transitions.transition_incident(bot, channel, "close", 101)
        self.assertEqual((changed, reason), (True, "closed_now"))
        self.assertEqual(bot.db_pool.conn.state["status"], "closed")

        changed, reason = await transitions.transition_incident(bot, channel, "backup", 101)
        self.assertEqual((changed, reason), (False, "closed"))
        self.assertGreaterEqual(len(bot.db_pool.conn.events), 5)


if __name__ == "__main__":
    unittest.main()
