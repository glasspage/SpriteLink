import copy
import json
import queue
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

from test_security import SPRITELINK as S


class PresenceTests(unittest.TestCase):
    def setUp(self):
        self.now = 30_000
        self.server = "https://ntfy.example"
        self.client = SimpleNamespace(
            config_data={"server_url": self.server, "recently_online_state": {}},
            active_chatroom_id=S.GLOBAL_CHATROOM_ID,
            recently_online_send_attempts={},
            recently_online_poll_attempts={},
            recently_online_refresh_event=threading.Event(),
            _minimized_to_tray=False,
            session=mock.Mock(),
            _queue_ui_event=mock.Mock(),
        )
        self.clock = mock.patch.object(S.time, "time", side_effect=lambda: self.now)
        self.save = mock.patch.object(S, "save_config")
        self.clock.start()
        self.saved = self.save.start()
        self.addCleanup(self.clock.stop)
        self.addCleanup(self.save.stop)

    def step(self, now):
        return S.EncryptedChatClient._network_recently_online_step(self.client, now=now)

    def record(self, token, timestamp=None):
        return {"event": "message", "time": self.now if timestamp is None else timestamp,
                "message": S.make_recently_online_packet(token)}

    def test_packets_are_randomized_and_deduplicate_without_chat_identity(self):
        token = "ab" * 16
        first, second = self.record(token), self.record(token)
        self.assertNotEqual(first["message"], second["message"])
        self.assertNotIn(token, first["message"])
        topic, key = S.recently_online_transport()
        self.assertNotEqual(topic, S.derive_ntfy_topic(S.GLOBAL_CHATROOM_KEY))
        raw = S.base64.b64decode(first["message"])
        plaintext = S.ChaCha20Poly1305(key).decrypt(
            raw[1:13], raw[13:], b"SpriteLink-recently-online-v1"
        )
        self.assertEqual(plaintext, bytes.fromhex(token))
        self.assertEqual(S.recently_online_pings([first, second], now=self.now), {token: self.now})

    def test_tally_uses_exact_six_hour_window_and_rejects_invalid_packets(self):
        cutoff = self.now - S.RECENTLY_ONLINE_INTERVAL_SECONDS
        valid = self.record("aa" * 16, cutoff + 1)
        damaged = self.record("bb" * 16)
        damaged["message"] = "A" * 60
        records = [valid, self.record("cc" * 16, cutoff), self.record("dd" * 16, self.now + 1),
                   damaged, {"time": True, "message": valid["message"]},
                   {"time": self.now, "message": None},
                   {"time": self.now, "message": "A" * 100_000}]
        self.assertEqual(S.recently_online_pings(records, now=self.now), {"aa" * 16: cutoff + 1})

    def test_one_request_per_step_and_one_ping_per_six_hours_across_restart(self):
        self.assertTrue(self.step(100))
        self.client.session.post.assert_called_once()
        self.client.session.get.assert_not_called()
        first = self.client.session.post.call_args.kwargs["data"].decode("ascii")
        self.client.session.get.return_value.text = json.dumps({
            "event": "message", "time": self.now, "message": first,
        })
        self.assertTrue(self.step(106))
        self.assertEqual(self.client.session.get.call_args.kwargs["params"], {"poll": "1", "since": "6h"})
        self.assertEqual(self.client._queue_ui_event.call_args.args[0][0], "recently_online")
        self.assertEqual(len(self.client._queue_ui_event.call_args.args[0][1][1]), 1)
        self.assertFalse(self.step(112))
        self.client.recently_online_send_attempts.clear()  # Simulate restart.
        self.now += S.RECENTLY_ONLINE_INTERVAL_SECONDS - 1
        self.step(400)
        self.assertEqual(self.client.session.post.call_count, 1)
        self.now += 1
        self.assertTrue(self.step(406))
        self.assertEqual(self.client.session.post.call_count, 2)
        token1 = S.recently_online_pings([{"time": self.now, "message": first}], now=self.now)
        second = self.client.session.post.call_args.kwargs["data"].decode("ascii")
        token2 = S.recently_online_pings([{"time": self.now, "message": second}], now=self.now)
        self.assertNotEqual(token1, token2)

    def test_failed_post_reuses_persisted_token_on_restart_with_backoff(self):
        disk = {}
        self.saved.side_effect = lambda config: disk.update(copy.deepcopy(config))
        self.client.session.post.side_effect = S.requests.Timeout()
        self.step(100)
        pending = disk["recently_online_state"][self.server]["pending_token"]
        # Before the retry is due, polling may still run, but no second POST.
        self.client.session.get.side_effect = S.requests.Timeout()
        self.step(106)
        self.assertEqual(self.client.session.post.call_count, 1)
        self.client.config_data = copy.deepcopy(disk)
        self.client.recently_online_send_attempts.clear()
        self.client.session.post.side_effect = None
        self.step(200)
        packet = self.client.session.post.call_args.kwargs["data"].decode("ascii")
        self.assertEqual(S.recently_online_pings([{"time": self.now, "message": packet}], now=self.now),
                         {pending: self.now})
        self.assertNotIn("pending_token", disk["recently_online_state"][self.server])
        self.client._queue_ui_event.assert_called_once_with(("recently_online_sent", None))

    def test_tallies_every_five_minutes_even_in_private_view(self):
        self.client.active_chatroom_id = "private"
        self.assertTrue(self.step(100))
        self.client.session.get.return_value.text = ""
        self.assertTrue(self.step(106))
        self.assertEqual(self.client._queue_ui_event.call_args.args[0], ("recently_online", (self.server, {})))
        self.assertFalse(self.step(405))
        self.assertTrue(self.step(406))
        self.assertEqual(self.client.session.get.call_count, 2)

    def test_tray_pauses_tallies_and_restore_refreshes_before_five_minutes(self):
        self.step(100)
        self.client.session.get.return_value.text = ""
        self.step(106)
        self.client._minimized_to_tray = True
        self.assertFalse(self.step(112))
        self.assertFalse(self.step(406))
        self.assertEqual(self.client.session.get.call_count, 1)
        # Resume after a short stay as well as a long one.
        for resumed_at in (407, 419):
            self.client._minimized_to_tray = False
            self.client.recently_online_refresh_event.set()
            self.assertTrue(self.step(resumed_at))
            self.assertFalse(self.client.recently_online_refresh_event.is_set())
            self.assertFalse(self.step(resumed_at + 6))
            self.client._minimized_to_tray = True
        self.assertEqual(self.client.session.get.call_count, 3)

    def test_six_hour_publishing_continues_in_tray_without_tallying(self):
        self.client._minimized_to_tray = True
        self.assertTrue(self.step(100))
        self.assertFalse(self.step(400))
        self.now += S.RECENTLY_ONLINE_INTERVAL_SECONDS
        self.assertTrue(self.step(500))
        self.assertEqual(self.client.session.post.call_count, 2)
        self.client.session.get.assert_not_called()

    def test_server_state_is_separate_and_failed_tally_does_not_report_zero(self):
        self.step(100)
        self.client._queue_ui_event.reset_mock()
        self.client.session.get.side_effect = S.requests.Timeout()
        self.assertTrue(self.step(106))
        self.client._queue_ui_event.assert_not_called()
        self.assertFalse(self.step(112))
        self.assertTrue(self.step(166))
        self.client.config_data["server_url"] = "https://another.example/"
        self.assertTrue(self.step(172))
        self.assertEqual(self.client.session.post.call_count, 2)
        self.assertIn("https://another.example", self.client.config_data["recently_online_state"])

    def test_does_not_publish_until_retry_token_is_persisted(self):
        self.saved.side_effect = OSError("Disk unavailable")
        self.assertTrue(self.step(100))
        self.client.session.post.assert_not_called()

    def test_invalid_persisted_state_is_repaired(self):
        self.client.config_data["recently_online_state"][self.server] = {
            "sent_at": "yesterday", "pending_token": "not a token",
        }
        self.assertTrue(self.step(100))
        self.assertEqual(self.client.session.post.call_count, 1)
        self.assertEqual(self.client.config_data["recently_online_state"][self.server], {"sent_at": self.now})

    def _loop_requests(self, *, restore_during_wait=False):
        ticks = [0.0]
        requests = []
        self.client.stop_event = mock.Mock()
        self.client.stop_event.is_set.side_effect = lambda: len(requests) >= 4
        self.client.network_control_queue = queue.Queue()
        self.client.subscription_room_queue = queue.Queue()
        self.client.send_queue = queue.Queue()
        self.client.window_on_screen_event = threading.Event()
        self.client.window_on_screen_event.set()
        self.client._chatroom_definitions = lambda: [{"id": S.GLOBAL_CHATROOM_ID}]
        self.client._muted_chatroom_ids = lambda: set()
        self.client._network_poll = lambda *args, **kwargs: requests.append(("chat", ticks[0]))
        self.client._network_recently_online_step = lambda **kwargs: self.step(kwargs["now"])
        self.client.network_wakeup_event = mock.Mock()
        def wait(seconds):
            if restore_during_wait and len(requests) == 1:
                ticks[0] += 1
                self.client.recently_online_refresh_event.set()
            else:
                ticks[0] += seconds
        self.client.network_wakeup_event.wait.side_effect = wait
        self.client.session.post.side_effect = lambda *args, **kwargs: (
            requests.append(("ping", ticks[0])) or mock.Mock()
        )
        self.client.session.get.side_effect = lambda *args, **kwargs: (
            requests.append(("tally", ticks[0])) or mock.Mock(text="")
        )
        with mock.patch.object(S.time, "monotonic", side_effect=lambda: ticks[0]):
            S.EncryptedChatClient._network_loop(self.client)
        return requests

    def test_presence_shares_chat_cadence_without_bursting_or_starving_chat(self):
        requests = self._loop_requests()
        interval = S.ON_SCREEN_POLL_INTERVAL_SECONDS
        self.assertEqual(requests, [
            ("chat", 0), ("ping", interval), ("tally", interval * 2), ("chat", interval * 3),
        ])

    def test_restored_tally_wakes_worker_and_bypasses_chat_poll_wait(self):
        self.client.config_data["recently_online_state"][self.server] = {"sent_at": self.now}
        self.client.recently_online_poll_attempts[self.server] = 0
        requests = self._loop_requests(restore_during_wait=True)
        interval = S.ON_SCREEN_POLL_INTERVAL_SECONDS
        self.assertEqual(requests, [
            ("chat", 0), ("tally", 1), ("chat", 1 + interval), ("chat", 1 + interval * 2),
        ])


if __name__ == "__main__":
    unittest.main()
