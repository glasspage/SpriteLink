from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
import copy
import inspect
import sys
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "SpriteLink.pyw"
LOADER = SourceFileLoader("spritelink_security_test_module", str(MODULE_PATH))
SPEC = spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
SPRITELINK = module_from_spec(SPEC)
sys.modules[SPEC.name] = SPRITELINK
LOADER.exec_module(SPRITELINK)


class BehaviorSettingsTests(unittest.TestCase):
    def test_behavior_defaults_and_history_options(self) -> None:
        config = SPRITELINK.default_config()
        self.assertEqual(
            SPRITELINK.MESSAGE_HISTORY_OPTIONS,
            (100, 500, 1000, 10000),
        )
        self.assertEqual(config["message_history_limit"], 1000)
        self.assertFalse(config["minimize_to_tray"])

    def test_history_pruning_is_per_room_and_can_skip_active_room(self) -> None:
        histories = {
            "active": list(range(1200)),
            "background": list(range(700)),
        }
        changed = SPRITELINK.prune_local_history_map(
            histories,
            500,
            excluded_scope_id="active",
        )
        self.assertTrue(changed)
        self.assertEqual(len(histories["active"]), 1200)
        self.assertEqual(histories["background"], list(range(200, 700)))

        SPRITELINK.prune_local_history_map(histories, 500)
        self.assertEqual(histories["active"], list(range(700, 1200)))

    def test_history_limit_is_normalized_to_presets(self) -> None:
        for value in SPRITELINK.MESSAGE_HISTORY_OPTIONS:
            with self.subTest(value=value):
                self.assertEqual(
                    SPRITELINK.normalize_message_history_limit(str(value)),
                    value,
                )
        self.assertEqual(
            SPRITELINK.normalize_message_history_limit(999),
            SPRITELINK.DEFAULT_MESSAGE_HISTORY_LIMIT,
        )

    def test_behavior_controls_are_present_in_config(self) -> None:
        source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._build_config_tab
        )
        self.assertIn('self._heading("Behavior")', source)
        self.assertNotIn("Notifications and rendering", source)
        self.assertIn('QLabel("Message History")', source)
        self.assertIn('QCheckBox("Minimize to Tray")', source)
        self.assertIn("MESSAGE_HISTORY_OPTIONS", source)

    def test_background_history_pruning_runs_hourly(self) -> None:
        self.assertEqual(
            SPRITELINK.BACKGROUND_HISTORY_PRUNE_INTERVAL_MS,
            60 * 60 * 1000,
        )
        init_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient.__init__
        )
        self.assertIn("background_history_prune_timer.start()", init_source)
        prune_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._prune_background_local_histories
        )
        self.assertIn("excluded_scope_id=active_scope_id", prune_source)
        background_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._accept_background_messages
        )
        self.assertNotIn("history[-1000:]", background_source)

    def test_history_persistence_uses_selected_limit(self) -> None:
        load_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._load_saved_history_for_current_room
        )
        persist_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._persist_local_history
        )
        add_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._add_message_to_log
        )
        self.assertIn("_message_history_limit()", load_source)
        self.assertIn("_message_history_limit()", persist_source)
        self.assertIn("_message_history_limit()", add_source)
        self.assertNotIn("1000", persist_source)

    def test_close_can_hide_to_tray_or_exit(self) -> None:
        close_event_source = inspect.getsource(
            SPRITELINK.MainWindow.closeEvent
        )
        self.assertIn("event.ignore()", close_event_source)
        close_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._on_close
        )
        self.assertIn("_can_minimize_to_tray()", close_source)
        self.assertIn("_hide_to_tray()", close_source)
        self.assertIn("return False", close_source)
        tray_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._build_tray_icon
        )
        self.assertIn('"Show SpriteLink"', tray_source)
        self.assertIn('"Exit"', tray_source)

    def test_tray_notification_uses_orange_outline(self) -> None:
        self.assertEqual(
            SPRITELINK.TRAY_NOTIFICATION_OUTLINE_COLOR,
            "#ff7a00",
        )
        icon_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._build_tray_notification_icon
        )
        self.assertIn("offset_x", icon_source)
        self.assertIn("offset_y", icon_source)
        self.assertIn("setPixelColor", icon_source)
        mark_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._mark_tray_notification
        )
        self.assertIn("_tray_notification_icon", mark_source)
        restore_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._restore_from_tray
        )
        self.assertIn("_tray_normal_icon", restore_source)


class ProfileIconTests(unittest.TestCase):
    def test_jpeg_profile_icons_are_optimized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "profile.jpg"
            SPRITELINK.Image.new(
                "RGB",
                (48, 32),
                (20, 100, 180),
            ).save(source_path, format="JPEG")
            gif_data = SPRITELINK.optimize_profile_icon(str(source_path))

        self.assertIn(gif_data[:6], (b"GIF87a", b"GIF89a"))
        SPRITELINK.decode_profile_icon(
            SPRITELINK.encode_profile_icon(gif_data)
        )

    def test_profile_icon_picker_uses_native_thumbnail_dialog(self) -> None:
        source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._choose_profile_icon
        )
        self.assertNotIn("DontUseNativeDialog", source)
        self.assertIn("*.png *.jpg *.jpeg", source)
        self.assertIn("_exec_themed_file_dialog", source)

    def test_custom_chime_picker_uses_native_themed_dialog(self) -> None:
        source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._on_message_sound_selected
        )
        self.assertNotIn("DontUseNativeDialog", source)
        self.assertIn("_exec_themed_file_dialog", source)
        themed_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._exec_themed_file_dialog
        )
        self.assertIn("_apply_window_titlebar_theme", themed_source)
        self.assertIn("QTimer.singleShot", themed_source)


class ImageTrustTests(unittest.TestCase):
    def test_requested_and_common_cdn_hosts_are_trusted(self) -> None:
        trusted_urls = (
            "https://cdn.discordapp.com/attachments/1/2/example.png",
            "https://upload.wikimedia.org/wikipedia/commons/a/a0/example.jpg",
            "https://cdn.donmai.us/original/example.webp",
            "https://cdn.donmai.moe/original/example.webp",
            "https://pbs.twimg.com/media/example?format=jpg&name=large",
            "https://i.imgur.com/example.png",
            "https://images.unsplash.com/photo-example",
            "https://cdn.bsky.app/img/feed_fullsize/plain/example",
            "https://media.tenor.com/example/tenor.gif",
            "https://media2.giphy.com/media/example/giphy.gif",
            "https://cdn.klipy.com/example.gif",
            "https://res.cloudinary.com/example/image/upload/sample",
            "https://images.ctfassets.net/example/image.jpg",
            "https://cdn.sanity.io/images/example/image.webp",
            "https://live.staticflickr.com/example/image.jpg",
            "https://cdn.pixabay.com/photo/example.jpg",
            "https://static1.e621.net/data/example.png",
            "https://img3.gelbooru.com/images/example.jpg",
            "https://i.nhentai.net/galleries/example/1.jpg",
            "https://thumbs2.redgifs.com/example.jpg",
            "https://wimg.rule34.xxx/images/example.jpeg",
            "https://static.wikia.nocookie.net/example/image.png",
            "https://raw.githubusercontent.com/owner/repo/main/example.png",
        )
        for url in trusted_urls:
            with self.subTest(url=url):
                self.assertTrue(SPRITELINK.is_direct_image_url(url))
                self.assertTrue(SPRITELINK.is_trusted_image_url(url))

    def test_trusted_hosts_require_https_and_exact_host_matches(self) -> None:
        untrusted_urls = (
            "http://cdn.discordapp.com/attachments/1/2/example.png",
            "https://cdn.discordapp.com.evil.example/example.png",
            "https://evilcdn.discordapp.com/example.png",
            "https://cdn.donmai.evil.example/example.png",
            "https://static1.e621.net.evil.example/example.png",
            "https://cdn.klipy.com.evil.example/example.gif",
            "https://example.com/example.png",
        )
        for url in untrusted_urls:
            with self.subTest(url=url):
                self.assertFalse(SPRITELINK.is_trusted_image_url(url))

    def test_sender_trust_overrides_domain_trust(self) -> None:
        url = "https://example.com/example.png"
        self.assertFalse(
            SPRITELINK.is_image_url_trusted_for_sender(
                url,
                "sender",
                False,
                set(),
            )
        )
        self.assertTrue(
            SPRITELINK.is_image_url_trusted_for_sender(
                url,
                "sender",
                False,
                {"sender"},
            )
        )
        self.assertTrue(
            SPRITELINK.is_image_url_trusted_for_sender(
                url,
                "sender",
                True,
                set(),
            )
        )


class ImageEmbeddingTests(unittest.TestCase):
    def test_extensionless_provider_pages_are_embeddable(self) -> None:
        urls = (
            "https://tenor.com/view/shower-head-shower-random-dacing-taking-shower-gif-3237625789977090490",
            "https://klipy.com/gifs/christian-bale-me-when-the",
            "https://giphy.com/gifs/example-slug",
            "https://imgur.com/example",
            "https://redgifs.com/watch/example",
        )
        for url in urls:
            with self.subTest(url=url):
                self.assertTrue(
                    SPRITELINK.is_supported_media_page_url(url)
                )
                self.assertTrue(
                    SPRITELINK.is_embeddable_media_url(url)
                )
                self.assertTrue(SPRITELINK.is_trusted_image_url(url))
                self.assertEqual(
                    SPRITELINK.direct_image_urls_in_message(url),
                    [url],
                )

    def test_provider_metadata_prefers_looping_video(self) -> None:
        page_url = "https://tenor.com/view/example"
        html = """
        <html><head>
        <meta property="og:image"
              content="https://media.tenor.com/example/tenor.gif">
        <meta property="og:video:secure_url"
              content="https://media.tenor.com/example/tenor.mp4">
        </head></html>
        """
        self.assertEqual(
            SPRITELINK.resolve_media_url_from_page(page_url, html),
            (
                "https://media.tenor.com/example/tenor.mp4",
                "video",
            ),
        )

    def test_klipy_metadata_accepts_extensionless_cdn_media(self) -> None:
        page_url = "https://klipy.com/gifs/example"
        html = """
        <meta property="og:image"
              content="//cdn.klipy.com/media/example">
        """
        self.assertEqual(
            SPRITELINK.resolve_media_url_from_page(page_url, html),
            ("https://cdn.klipy.com/media/example", "image"),
        )
        fetch_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._fetch_remote_image_preview
        )
        self.assertIn("resolve_media_url_from_page", fetch_source)

    def test_remote_media_download_limit_is_50_mb(self) -> None:
        self.assertEqual(
            SPRITELINK.MAX_REMOTE_IMAGE_BYTES,
            50 * 1024 * 1024,
        )

    def test_nsfw_hosts_are_trusted_but_remain_masked(self) -> None:
        urls = (
            "https://static1.e621.net/data/example.png",
            "https://img3.gelbooru.com/images/example.jpg",
            "https://i.nhentai.net/galleries/example/1.jpg",
            "https://thumbs2.redgifs.com/example.jpg",
            "https://rule34-data-001.paheal.net/example.jpg",
            "https://wimg.rule34.xxx/images/example.jpeg",
            "https://img.xbooru.com/images/example.png",
            "https://files.yande.re/image/example.jpg",
        )
        for url in urls:
            with self.subTest(url=url):
                self.assertTrue(SPRITELINK.is_trusted_image_url(url))
                self.assertTrue(SPRITELINK.is_likely_nsfw_image_url(url))

    def test_tenor_videos_are_embeddable_looping_media(self) -> None:
        urls = (
            "https://media.tenor.com/example/tenor.mp4",
            "https://c.tenor.com/example/tenor.webm",
            "https://media.tenor.com/example/tenor?format=mp4",
            "https://cdn.klipy.com/example.mp4",
            "https://media2.giphy.com/example.webm",
        )
        for url in urls:
            with self.subTest(url=url):
                if "tenor.com" in url:
                    self.assertTrue(SPRITELINK.is_tenor_video_url(url))
                self.assertTrue(
                    SPRITELINK.is_trusted_looping_video_url(url)
                )
                self.assertTrue(SPRITELINK.is_embeddable_media_url(url))
                self.assertTrue(SPRITELINK.is_trusted_image_url(url))
        self.assertFalse(
            SPRITELINK.is_tenor_video_url(
                "https://example.com/not-tenor.mp4"
            )
        )
        controller_source = inspect.getsource(
            SPRITELINK.AnimatedMediaController
        )
        self.assertIn("QVideoSink", controller_source)
        self.assertIn("QMediaPlayer.Loops.Infinite", controller_source)
        self.assertIn("player.play()", controller_source)

    def test_gifs_use_the_animated_media_controller(self) -> None:
        controller_source = inspect.getsource(
            SPRITELINK.AnimatedMediaController
        )
        self.assertIn("QMovie", controller_source)
        self.assertIn("movie.frameChanged.connect", controller_source)
        preview_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._on_animated_media_frame
        )
        self.assertIn("_set_large_image_preview_frame", preview_source)
        self.assertIn("document.addResource", preview_source)

    def test_animated_frames_keep_native_playback_timing(self) -> None:
        controller_source = inspect.getsource(
            SPRITELINK.AnimatedMediaController._emit_frame
        )
        self.assertIn("emitted_frame = frame.copy()", controller_source)
        self.assertNotIn("IMAGE_PREVIEW_MAX_WIDTH", controller_source)
        inline_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._on_animated_media_frame
        )
        self.assertNotIn("MIN_INLINE_ANIMATION_FRAME_MS", inline_source)
        self.assertNotIn("time.monotonic()", inline_source)

    def test_small_inline_media_is_not_upscaled(self) -> None:
        frame = SPRITELINK.QImage(
            48,
            32,
            SPRITELINK.QImage.Format.Format_ARGB32,
        )
        media = SPRITELINK.RemoteMediaPreview(
            source_url="https://example.com/small.gif",
            data=b"",
            kind="animated_gif",
            frame=frame,
        )
        preview = SPRITELINK.EncryptedChatClient._scaled_inline_media_frame(
            media,
            frame,
        )
        self.assertEqual((preview.width(), preview.height()), (48, 32))

        video_media = SPRITELINK.RemoteMediaPreview(
            source_url="https://example.com/small.mp4",
            data=b"",
            kind="looping_video",
            frame=frame,
        )
        video_preview = (
            SPRITELINK.EncryptedChatClient._scaled_inline_media_frame(
                video_media,
                frame,
            )
        )
        self.assertEqual(
            (video_preview.width(), video_preview.height()),
            (48, 32),
        )

    def test_large_preview_contains_media_within_visible_overlay(self) -> None:
        source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._set_large_image_preview_frame
        )
        self.assertIn("self.image_preview_overlay.height()", source)
        self.assertIn("self.image_preview_button_row.sizeHint()", source)
        self.assertIn("available_width", source)
        self.assertIn("available_height", source)
        self.assertIn("Qt.AspectRatioMode.KeepAspectRatio", source)
        self.assertNotIn(
            "EMBEDDED_IMAGE_MAX_EDGE",
            source,
        )

    def test_untrusted_image_link_is_omitted_from_display_text(self) -> None:
        url = "https://example.com/private-image.png"
        display_text = (
            SPRITELINK.message_text_with_untrusted_images_hidden(
                f"look at {url} please",
                {url},
            )
        )
        self.assertEqual(
            display_text,
            "[untrusted image] look at please",
        )
        self.assertNotIn(url, display_text)
        render_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._insert_message_item
        )
        self.assertIn(
            "message_text_with_untrusted_images_hidden",
            render_source,
        )
        self.assertEqual(
            SPRITELINK.message_text_with_untrusted_images_hidden(
                url,
                {url},
            ),
            "[untrusted image]",
        )


class RoomKeyTests(unittest.TestCase):
    def test_generated_room_keys_are_exactly_32_url_safe_characters(self) -> None:
        keys = {SPRITELINK.generate_chatroom_key() for _ in range(16)}
        self.assertEqual(len(keys), 16)
        for key in keys:
            self.assertEqual(len(key), 32)
            self.assertRegex(key, r"^[A-Za-z0-9_-]{32}$")


class PacketLimitTests(unittest.TestCase):
    PASSPHRASE = "correct horse battery staple with extra entropy"

    @staticmethod
    def _message(text: str) -> dict[str, object]:
        return {
            "v": SPRITELINK.APP_VERSION,
            "i": "message-id",
            "c": "client-id",
            "u": "User",
            "k": "#123456",
            "t": 1_700_000_000,
            "m": text,
        }

    def test_normal_packet_round_trip(self) -> None:
        message = self._message("hello")
        packet = SPRITELINK.make_opaque_packet(message, self.PASSPHRASE)
        self.assertEqual(
            SPRITELINK.open_opaque_packet(packet, self.PASSPHRASE),
            message,
        )

    def test_oversized_encoded_packet_is_rejected_before_decryption(self) -> None:
        packet = "A" * (SPRITELINK.MAX_ENCRYPTED_PACKET_CHARS + 1)
        with self.assertRaisesRegex(ValueError, "too large"):
            SPRITELINK.open_opaque_packet(packet, self.PASSPHRASE)

    def test_over_32_kib_decompressed_packet_is_rejected(self) -> None:
        message = self._message(
            "A" * (SPRITELINK.MAX_UNCOMPRESSED_MESSAGE_BYTES + 1)
        )
        packet = SPRITELINK.make_opaque_packet(message, self.PASSPHRASE)
        self.assertLessEqual(
            len(packet),
            SPRITELINK.MAX_ENCRYPTED_PACKET_CHARS,
        )
        with self.assertRaisesRegex(ValueError, "too large"):
            SPRITELINK.open_opaque_packet(packet, self.PASSPHRASE)


class SignedClientIdentityTests(unittest.TestCase):
    ROOM_ONE = "first room encryption key"
    ROOM_TWO = "second room encryption key"

    def setUp(self) -> None:
        self.private_key = SPRITELINK.generate_identity_private_key()

    @staticmethod
    def _message(text: str = "hello") -> dict[str, object]:
        return {
            "v": SPRITELINK.APP_VERSION,
            "i": "0123456789abcdef0123456789abcdef",
            "u": "User",
            "k": "#123456",
            "f": SPRITELINK.DEFAULT_MESSAGE_FONT,
            "o": SPRITELINK.DEFAULT_MESSAGE_TEXT_COLOR,
            "t": 1_700_000_000,
            "m": text,
        }

    def _signed_message(
        self,
        room_key: str | None = None,
    ) -> dict[str, object]:
        return SPRITELINK.sign_message_identity(
            self._message(),
            room_key or self.ROOM_ONE,
            self.private_key,
        )

    def test_one_installation_keeps_the_same_id_across_rooms(self) -> None:
        first_room_message = self._signed_message(self.ROOM_ONE)
        second_room_message = self._signed_message(self.ROOM_TWO)

        self.assertEqual(
            first_room_message["c"],
            second_room_message["c"],
        )
        self.assertRegex(first_room_message["c"], r"^[0-9a-f]{64}$")
        SPRITELINK.verify_message_identity(
            first_room_message,
            self.ROOM_ONE,
        )
        SPRITELINK.verify_message_identity(
            second_room_message,
            self.ROOM_TWO,
        )

    def test_visible_user_id_is_eight_uppercase_characters(self) -> None:
        message = self._signed_message()
        visible_id = SPRITELINK.visible_user_id(str(message["c"]))
        self.assertEqual(len(visible_id), 8)
        self.assertRegex(visible_id, r"^[0-9A-F]{8}$")

    def test_message_round_trip_requires_a_valid_signature(self) -> None:
        message = self._signed_message()
        packet = SPRITELINK.make_opaque_packet(message, self.ROOM_ONE)
        opened = SPRITELINK.open_opaque_packet(packet, self.ROOM_ONE)
        SPRITELINK.EncryptedChatClient._validate_decrypted_message(
            opened,
            self.ROOM_ONE,
        )
        self.assertEqual(opened, message)

    def test_changing_signed_message_content_is_rejected(self) -> None:
        message = self._signed_message()
        forged = copy.deepcopy(message)
        forged["m"] = "forged text"

        with self.assertRaises(Exception):
            SPRITELINK.verify_message_identity(forged, self.ROOM_ONE)

    def test_copying_a_client_id_to_another_key_is_rejected(self) -> None:
        message = self._signed_message()
        other_private_key = SPRITELINK.generate_identity_private_key()
        forged = SPRITELINK.sign_message_identity(
            self._message("forged text"),
            self.ROOM_ONE,
            other_private_key,
        )
        forged["c"] = message["c"]

        with self.assertRaisesRegex(ValueError, "does not match"):
            SPRITELINK.verify_message_identity(forged, self.ROOM_ONE)

    def test_signature_is_bound_to_its_chatroom(self) -> None:
        message = self._signed_message(self.ROOM_ONE)

        with self.assertRaises(Exception):
            SPRITELINK.verify_message_identity(message, self.ROOM_TWO)

    def test_unsigned_messages_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            SPRITELINK.EncryptedChatClient._validate_decrypted_message(
                self._message(),
                self.ROOM_ONE,
            )

    def test_generated_private_keys_are_unique_and_well_formed(self) -> None:
        keys = {
            SPRITELINK.generate_identity_private_key()
            for _ in range(16)
        }
        self.assertEqual(len(keys), 16)
        for key in keys:
            normalized = SPRITELINK.normalize_identity_private_key(key)
            self.assertEqual(normalized, key)
            self.assertEqual(
                len(SPRITELINK.identity_public_key_bytes(key)),
                SPRITELINK.ED25519_PUBLIC_KEY_BYTES,
            )


class _FakeUpdateWidget:
    def __init__(self) -> None:
        self.text = ""
        self.enabled = True
        self.tooltip = ""
        self.focused = False
        self.stylesheet = ""

    def setText(self, text: str) -> None:
        self.text = text

    def setEnabled(self, enabled: bool) -> None:
        self.enabled = enabled

    def setToolTip(self, tooltip: str) -> None:
        self.tooltip = tooltip

    def setFocus(self) -> None:
        self.focused = True

    def isEnabled(self) -> bool:
        return self.enabled

    def setStyleSheet(self, stylesheet: str) -> None:
        self.stylesheet = stylesheet


class _FakeConfigOverlay:
    def isVisible(self) -> bool:
        return False


class _FakeUpdateClient:
    def __init__(self) -> None:
        self.available_update = object()
        self._update_check_in_progress = True
        self.latest_version_label = _FakeUpdateWidget()
        self.update_button = _FakeUpdateWidget()
        self.config_overlay = _FakeConfigOverlay()
        self.config_style_updates = 0

    def _update_config_toggle_update_style(self) -> None:
        self.config_style_updates += 1

    def _update_update_button_style(self) -> None:
        SPRITELINK.EncryptedChatClient._update_update_button_style(self)

    def _show_no_available_update(self) -> None:
        SPRITELINK.EncryptedChatClient._show_no_available_update(self)


class UpdateConfigTests(unittest.TestCase):
    @staticmethod
    def _release(version: str) -> object:
        return SPRITELINK.ReleaseInfo(
            version=version,
            tag_name=f"v{version}",
            installer_url=f"https://example.com/SpriteLink-{version}.exe",
            checksum_url=f"https://example.com/SpriteLink-{version}.exe.sha256",
            page_url=f"https://example.com/releases/{version}",
            notes="Release notes",
        )

    def test_update_checks_are_forced_at_startup_and_every_30_minutes(
        self,
    ) -> None:
        self.assertEqual(
            SPRITELINK.UPDATE_CHECK_INTERVAL_MS,
            30 * 60 * 1000,
        )
        self.assertNotIn(
            "automatic_update_checks",
            SPRITELINK.default_config(),
        )
        init_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient.__init__
        )
        self.assertIn(
            "QTimer.singleShot(0, self._check_for_updates)",
            init_source,
        )
        self.assertIn("self.update_check_timer.start()", init_source)

    def test_version_controls_use_the_compact_left_aligned_format(
        self,
    ) -> None:
        ui_source = inspect.getsource(
            SPRITELINK.EncryptedChatClient._build_config_tab
        )
        self.assertIn("Current Version: {RUNNING_VERSION}", ui_source)
        self.assertIn('QLabel("Latest Version: ---")', ui_source)
        self.assertIn('QPushButton("Up-to-date")', ui_source)
        self.assertIn("Qt.AlignmentFlag.AlignLeft", ui_source)
        self.assertNotIn("Check for Updates", ui_source)
        self.assertNotIn("Check automatically", ui_source)

    def test_failed_check_uses_silent_unknown_up_to_date_state(self) -> None:
        client = _FakeUpdateClient()
        SPRITELINK.EncryptedChatClient._handle_update_check_result(
            client,
            {"error": True},
        )
        self.assertEqual(client.latest_version_label.text, "Latest Version: ---")
        self.assertEqual(client.latest_version_label.tooltip, "")
        self.assertEqual(client.update_button.text, "Up-to-date")
        self.assertFalse(client.update_button.enabled)
        self.assertEqual(client.update_button.tooltip, "")
        self.assertIsNone(client.available_update)
        self.assertFalse(client._update_check_in_progress)

    def test_available_update_enables_update_button(self) -> None:
        client = _FakeUpdateClient()
        release = self._release("2.0.0")
        with mock.patch.object(
            SPRITELINK,
            "release_is_newer",
            return_value=True,
        ):
            SPRITELINK.EncryptedChatClient._handle_update_check_result(
                client,
                {"release": release},
            )
        self.assertEqual(client.latest_version_label.text, "Latest Version: 2.0.0")
        self.assertEqual(client.update_button.text, "Update")
        self.assertTrue(client.update_button.enabled)
        self.assertEqual(
            client.update_button.stylesheet,
            SPRITELINK.NOTIFICATION_BUTTON_STYLESHEET,
        )
        self.assertIs(client.available_update, release)

    def test_current_release_disables_up_to_date_button(self) -> None:
        client = _FakeUpdateClient()
        release = self._release("1.0.0")
        with mock.patch.object(
            SPRITELINK,
            "release_is_newer",
            return_value=False,
        ):
            SPRITELINK.EncryptedChatClient._handle_update_check_result(
                client,
                {"release": release},
            )
        self.assertEqual(client.latest_version_label.text, "Latest Version: 1.0.0")
        self.assertEqual(client.update_button.text, "Up-to-date")
        self.assertFalse(client.update_button.enabled)
        self.assertEqual(client.update_button.stylesheet, "")
        self.assertIsNone(client.available_update)


if __name__ == "__main__":
    unittest.main()
