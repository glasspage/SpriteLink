"""Import behavior regression tests: no real user data is touched."""
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from spritelink_import_merge import merge_emoji_data, merge_identity_data
from test_security import SPRITELINK as S


class _Client:
    def __init__(self):
        self.config_data = S.default_config()
        self.config_data["chatrooms"] = [{
            "id": "local-room", "nickname": "Local Name", "key": "shared-secret-key",
        }]
        self.config_data["room_settings"] = {
            "local-room": {"message_sound": "Blip"},
        }

    def _chatroom_definitions(self):
        return [
            {"id": S.GLOBAL_CHATROOM_ID,
             "nickname": S.GLOBAL_CHATROOM_NICKNAME, "key": S.GLOBAL_CHATROOM_KEY},
            *self.config_data["chatrooms"],
        ]


def icon(color):
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), color).save(buffer, format="GIF")
    return S.encode_profile_icon(buffer.getvalue())


def emoji(index):
    return S.encode_drawing(S.EMOJI_SIZE, bytes([index]) * 256)


class EmojiImportTests(unittest.TestCase):
    def test_merge_suffixes_conflicts_and_skips_exact_reimports(self):
        first, second, third = (emoji(n) for n in (1, 2, 3))
        local = {"Cat": first, "Cat_2": second, "Bird": third}
        imported = {"cat": second, "bird": third, "Dog": third}
        merged, count = merge_emoji_data(local, imported, S.valid_emoji)
        self.assertEqual(count, 1)
        self.assertEqual(merged["Dog"], third)
        self.assertEqual(merged["Cat_2"], second)
        self.assertEqual(merged["Cat"], first)
        merged, count = merge_emoji_data(merged, {"Cat": third}, S.valid_emoji)
        self.assertEqual(count, 1)
        self.assertEqual(merged["Cat_3"], third)
        self.assertEqual(merge_emoji_data(merged, {"Cat": third}, S.valid_emoji),
                         (merged, 0))
        self.assertEqual(local, {"Cat": first, "Cat_2": second, "Bird": third})

    def test_maximum_length_name_is_idempotent(self):
        original = "a" * 32
        first, second = emoji(1), emoji(2)
        merged, count = merge_emoji_data(
            {original: first}, {original: second}, S.valid_emoji,
        )
        self.assertEqual(count, 1)
        self.assertEqual(merged["a" * 30 + "_2"], second)
        self.assertEqual(
            merge_emoji_data(merged, {original: second}, S.valid_emoji),
            (merged, 0),
        )

    def test_suffix_trims_dangling_punctuation_at_character_limit(self):
        original = "a" * 29 + "-bc"
        first, second = emoji(1), emoji(2)
        merged, count = merge_emoji_data(
            {original: first}, {original: second}, S.valid_emoji,
        )
        renamed = "a" * 29 + "_2"
        self.assertEqual(count, 1)
        self.assertEqual(merged[renamed], second)
        self.assertEqual(
            merge_emoji_data(merged, {original: second}, S.valid_emoji),
            (merged, 0),
        )

    def test_name_and_image_both_matter(self):
        data = emoji(1)
        merged, count = merge_emoji_data(
            {"First": data}, {"Second": data}, S.valid_emoji,
        )
        self.assertEqual(count, 1)
        self.assertIn("Second", merged)
        with self.assertRaisesRegex(ValueError, "Invalid emoji"):
            merge_emoji_data(merged, {"bad name": data}, S.valid_emoji)

    def test_client_emoji_modes_and_idempotence(self):
        client = _Client()
        first, second = emoji(1), emoji(2)
        client.config_data["saved_emojis"] = {"Cat": first, "Keep": first}
        imported = {"Emoji": {"Cat": second}}
        staged, jobs, counts = S.EncryptedChatClient._prepare_data_import(
            client, imported, ("Emoji",),
        )
        self.assertEqual(staged["saved_emojis"],
                         {"Cat": first, "Keep": first, "Cat_2": second})
        self.assertEqual(counts["emojis"], 1)
        client.config_data = staged
        staged, _, counts = S.EncryptedChatClient._prepare_data_import(
            client, imported, ("Emoji",),
        )
        self.assertEqual(counts["emojis"], 0)
        staged, _, counts = S.EncryptedChatClient._prepare_data_import(
            client, imported, ("Emoji",), behavior="overwrite",
        )
        self.assertEqual(staged["saved_emojis"], {"Cat": second})
        self.assertEqual(counts["emojis"], 1)


class IdentityImportTests(unittest.TestCase):
    def test_merge_matches_name_not_id_and_updates_colors_icons(self):
        client = _Client()
        old_icon, imported_icon = icon("red"), icon("blue")
        existing = {
            "id": "local-id", "username": "Sam",
            "username_color": "#ff0000", "profile_icon": old_icon,
        }
        client.config_data["identity_presets"] = [existing]
        S.apply_identity_preset_to_profile(
            client.config_data["room_profiles"][S.GLOBAL_CHATROOM_ID], existing,
        )
        client.config_data["room_profiles"]["local-room"] = dict(
            client.config_data["room_profiles"][S.GLOBAL_CHATROOM_ID]
        )
        before_key = client.config_data["identity_private_key"]
        alternate_key = S.generate_identity_private_key()
        imported = {
            "Identity": {
                "private_key": alternate_key,
                "presets": [
                    {"id": "remote-id", "username": "sam",
                     "username_color": "#00ff00", "profile_icon": imported_icon},
                    {"id": "brand-new", "username": "NewUser",
                     "username_color": "#0000ff", "profile_icon": ""},
                ],
                "active_preset_id": "brand-new",
            },
        }
        staged, _, counts = S.EncryptedChatClient._prepare_data_import(
            client, imported, ("Identity",),
        )
        self.assertEqual(staged["identity_private_key"], before_key)
        self.assertEqual(counts["identities"], 2)
        matched = staged["identity_presets"][0]
        self.assertEqual(matched["id"], "local-id")
        self.assertEqual(matched["username"], "Sam")
        self.assertEqual(matched["username_color"], "#00ff00")
        self.assertEqual(matched["profile_icon"], imported_icon)
        self.assertEqual(staged["identity_presets"][1]["username"], "NewUser")
        self.assertEqual(
            staged["room_profiles"][S.GLOBAL_CHATROOM_ID]["identity_preset_id"],
            "local-id",
        )
        self.assertEqual(
            staged["room_profiles"]["local-room"]["profile_icon"], imported_icon,
        )

        client.config_data = staged
        again, _, counts = S.EncryptedChatClient._prepare_data_import(
            client, imported, ("Identity",),
        )
        self.assertEqual(counts["identities"], 0)
        self.assertEqual(again["identity_presets"], staged["identity_presets"])

    def test_same_id_different_name_gets_a_new_unique_id(self):
        existing = [{"id": "shared", "username": "Alice",
                     "username_color": "#ffffff", "profile_icon": ""}]
        new = [{"id": "shared", "username": "Bob",
                "username_color": "#000000", "profile_icon": ""}]
        merged, added, updated = merge_identity_data(
            existing, new, S.normalize_identity_preset,
            lambda: "new-unique", S.MAX_IDENTITY_PRESETS,
        )
        self.assertEqual((added, updated), (1, 0))
        self.assertEqual(merged[0], existing[0])
        self.assertEqual(merged[1]["id"], "new-unique")

    def test_duplicate_named_presets_do_not_update_an_arbitrary_identity(self):
        local = [
            {"id": "a", "username": "User",
             "username_color": "#ff0000", "profile_icon": ""},
            {"id": "b", "username": "User",
             "username_color": "#00ff00", "profile_icon": ""},
        ]
        imported = [
            {"id": "remote", "username": "user",
             "username_color": "#0000ff", "profile_icon": ""},
        ]
        merged, added, updated = merge_identity_data(
            local, imported, S.normalize_identity_preset,
            lambda: "unique", S.MAX_IDENTITY_PRESETS,
        )
        self.assertEqual((added, updated), (1, 0))
        self.assertEqual(merged[:2], local)
        self.assertEqual(merged[2]["id"], "remote")
        again, added, updated = merge_identity_data(
            merged, imported, S.normalize_identity_preset,
            lambda: "unique", S.MAX_IDENTITY_PRESETS,
        )
        self.assertEqual((added, updated), (0, 0))
        self.assertEqual(again, merged)

    def test_duplicate_named_identities_use_id_to_select_exact_preset(self):
        local = [
            {"id": "a", "username": "User",
             "username_color": "#ff0000", "profile_icon": ""},
            {"id": "b", "username": "User",
             "username_color": "#00ff00", "profile_icon": ""},
        ]
        imported = [
            {"id": "b", "username": "USER",
             "username_color": "#0000ff", "profile_icon": ""},
        ]
        merged, added, updated = merge_identity_data(
            local, imported, S.normalize_identity_preset,
            lambda: "unique", S.MAX_IDENTITY_PRESETS,
        )
        self.assertEqual((added, updated), (0, 1))
        self.assertEqual(merged[0], local[0])
        self.assertEqual(merged[1]["username_color"], "#0000ff")

    def test_overwrite_replaces_key_presets_and_active_global_profile(self):
        client = _Client()
        client.config_data["identity_presets"] = [{
            "id": "old-id", "username": "Old",
            "username_color": "#ffffff", "profile_icon": "",
        }]
        client.config_data["room_profiles"][S.GLOBAL_CHATROOM_ID][
            "identity_preset_id"
        ] = "old-id"
        new_key = S.generate_identity_private_key()
        payload = {"Identity": {
            "private_key": new_key,
            "presets": [
                {"id": "new-id", "username": "New",
                 "username_color": "#123456", "profile_icon": ""},
            ],
            "active_preset_id": "new-id",
        }}
        staged, _, counts = S.EncryptedChatClient._prepare_data_import(
            client, payload, ("Identity",), behavior="overwrite",
        )
        self.assertEqual(counts["identities"], 1)
        self.assertEqual(staged["identity_private_key"], new_key)
        self.assertEqual([p["id"] for p in staged["identity_presets"]], ["new-id"])
        self.assertEqual(
            staged["room_profiles"][S.GLOBAL_CHATROOM_ID]["identity_preset_id"],
            "new-id",
        )


class ChatroomOverwriteTests(unittest.TestCase):
    def test_overwrite_changes_selected_name_not_local_preferences(self):
        client = _Client()
        payload = {"Chatrooms": [{
            "nickname": "Imported Name", "key": "shared-secret-key",
            "history": [],
        }]}
        staged, jobs, _ = S.EncryptedChatClient._prepare_data_import(
            client, payload, ("Chatrooms",),
        )
        self.assertEqual(staged["chatrooms"][0]["nickname"], "Local Name")
        overwritten, jobs, _ = S.EncryptedChatClient._prepare_data_import(
            client, payload, ("Chatrooms",), behavior="overwrite",
        )
        self.assertEqual(overwritten["chatrooms"][0]["nickname"], "Imported Name")
        self.assertEqual(overwritten["chatrooms"][0]["id"], "local-room")
        self.assertEqual(
            overwritten["room_settings"]["local-room"], {"message_sound": "Blip"},
        )

    def test_history_overwrite_replaces_including_empty_and_rolls_back(self):
        server, key = "https://ntfy.sh", "history-overwrite-test"
        existing = {"message": {"i": "old", "c": "u", "t": 1, "m": "old"}}
        replacement = {"message": {"i": "new", "c": "u", "t": 2, "m": "new"}}
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.object(S, "CHATROOM_HISTORY_DIRECTORY", Path(tmp)),
                mock.patch.object(S, "dpapi_encrypt", side_effect=lambda raw, e: raw),
                mock.patch.object(S, "dpapi_decrypt", side_effect=lambda raw, e: raw),
            ):
                S.save_chatroom_history(server, key, [existing])
                self.assertEqual(S.overwrite_imported_chatroom_history(
                    server, key, [replacement],
                ), 1)
                self.assertEqual(S.load_chatroom_history(server, key), [replacement])
                with mock.patch.object(
                    S, "dpapi_encrypt", side_effect=ValueError("write failed"),
                ):
                    with self.assertRaisesRegex(ValueError, "write failed"):
                        S.overwrite_imported_chatroom_history(
                            server, key, [existing],
                        )
                self.assertEqual(S.load_chatroom_history(server, key), [replacement])
                self.assertEqual(S.overwrite_imported_chatroom_history(
                    server, key, [],
                ), 0)
                self.assertEqual(S.load_chatroom_history(server, key), [])


if __name__ == "__main__":
    unittest.main()
