import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

import bot_discord_recovery as recovery


class FakeHistory:
    def __init__(self, messages):
        self.messages = messages

    def __aiter__(self):
        async def iterate():
            for message in self.messages:
                yield message
        return iterate()


class FakeMessage:
    def __init__(self, message_id, embed=None):
        self.id = message_id
        self.embeds = [embed] if embed else []
        self.edit = AsyncMock()


class FakeChannel:
    def __init__(self, channel_id=100, message=None):
        self.id = channel_id
        self._message = message
        self.sent = []

    async def fetch_message(self, message_id):
        if self._message and self._message.id == message_id:
            return self._message
        raise AssertionError("unexpected message fetch")

    def history(self, limit=50):
        return FakeHistory([])

    async def send(self, **kwargs):
        message = FakeMessage(999, kwargs.get("embed"))
        self.sent.append((message, kwargs))
        self._message = message
        return message


class FakeConnection:
    def __init__(self):
        self.executed = []

    async def execute(self, query, *args):
        self.executed.append((query, args))
        return "UPDATE 1"


class FakeAcquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakePool:
    def __init__(self):
        self.connection = FakeConnection()

    def acquire(self):
        return FakeAcquire(self.connection)


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    def row(self, **overrides):
        row = {
            "guild_id": 1,
            "incident_number": 42,
            "channel_id": 100,
            "requester_id": 200,
            "callsign": "Test Pilot",
            "service": "medical",
            "location": "Daymar",
            "situation": "Recovery test",
            "priority": "urgent",
            "status": "en_route",
            "primary_responder_id": 300,
            "closed_by_id": None,
            "incident_message_id": 555,
            "closed_at": None,
        }
        row.update(overrides)
        return row

    async def test_missing_incident_card_is_recreated_and_persisted(self):
        channel = FakeChannel()
        bot = SimpleNamespace(db_pool=FakePool())
        row = self.row(incident_message_id=None)

        message = await recovery._find_or_recreate_card(bot, channel, row)

        self.assertEqual(message.id, 999)
        self.assertEqual(len(channel.sent), 1)
        self.assertIn("RESCUE-0042", channel.sent[0][1]["embed"].title)
        self.assertEqual(len(bot.db_pool.connection.executed), 1)
        query, args = bot.db_pool.connection.executed[0]
        self.assertIn("incident_message_id", query)
        self.assertEqual(args, (100, 999))

    async def test_stale_card_is_reconciled_to_database_state(self):
        stale = discord.Embed(title="🚨 RESCUE-0042 — ACTIVE RESCUE REQUEST", color=discord.Color.green())
        stale.add_field(name="Priority", value="P3 Standard", inline=True)
        stale.add_field(name="Status", value="🔴 Awaiting Responder", inline=True)
        stale.add_field(name="Primary Responder", value="Unassigned", inline=True)
        message = FakeMessage(555, stale)
        channel = FakeChannel(message=message)
        bot = SimpleNamespace(db_pool=FakePool())
        row = self.row(priority="critical", status="on_scene", primary_responder_id=300)

        changed = await recovery._repair_card(bot, channel, row)

        self.assertTrue(changed)
        message.edit.assert_awaited_once()
        edited = message.edit.await_args.kwargs["embed"]
        fields = {field.name: field.value for field in edited.fields}
        self.assertEqual(fields["Priority"], recovery.core.PRIORITY_DISPLAY["critical"])
        self.assertEqual(fields["Status"], "🟢 On Scene — <@300>")
        self.assertEqual(fields["Primary Responder"], "<@300>")
        self.assertEqual(edited.color, discord.Color.green())

    async def test_closed_channel_permissions_name_and_topic_are_repaired(self):
        requester = object()
        responder_role = object()

        class ClosedChannel:
            def __init__(self):
                self.name = "rescue-0042-test"
                self.topic = "RESCUE-0042 | P2 Urgent"
                self.guild = SimpleNamespace(get_member=lambda user_id: requester if user_id == 200 else None)
                self.permission_calls = []
                self.edit = AsyncMock()

            def overwrites_for(self, target):
                return SimpleNamespace(view_channel=None, send_messages=True, read_message_history=None)

            async def set_permissions(self, target, **kwargs):
                self.permission_calls.append((target, kwargs))

        channel = ClosedChannel()
        row = self.row(status="closed", closed_by_id=400)

        with patch.object(recovery.core, "all_responder_roles", return_value=[responder_role]):
            changed = await recovery._repair_closed_channel(channel, row)

        self.assertTrue(changed)
        self.assertEqual(len(channel.permission_calls), 2)
        for _target, kwargs in channel.permission_calls:
            self.assertTrue(kwargs["view_channel"])
            self.assertFalse(kwargs["send_messages"])
            self.assertTrue(kwargs["read_message_history"])
        channel.edit.assert_awaited_once_with(
            name="closed-rescue-0042-test",
            topic="CLOSED | RESCUE-0042 | P2 Urgent",
        )

    def test_recovered_embed_matches_closed_database_state(self):
        row = self.row(status="closed", priority="critical", closed_by_id=400)
        embed = recovery._new_embed(row)
        fields = {field.name: field.value for field in embed.fields}

        self.assertIn("CLOSED RESCUE REQUEST", embed.title)
        self.assertEqual(fields["Priority"], recovery.core.PRIORITY_DISPLAY["critical"])
        self.assertEqual(fields["Status"], "⚫ Closed — <@400>")
        self.assertEqual(embed.color, discord.Color.dark_grey())


if __name__ == "__main__":
    unittest.main()
