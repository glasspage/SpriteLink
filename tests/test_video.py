import io
import json
import shutil
import subprocess
from types import SimpleNamespace
import unittest
from unittest import mock

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from test_security import SPRITELINK as S
import spritelink_video as V


def trusted(url):
    return S.analyze_link_url(url).trusted


class VideoLinksTests(unittest.TestCase):
    def test_provider_urls_and_start_times(self):
        examples = (
            ("https://youtu.be/M7lc1UVf-VE?t=1m30s", "youtube", "M7lc1UVf-VE", 90),
            ("https://www.youtube.com/watch?v=M7lc1UVf-VE&start=24", "youtube", "M7lc1UVf-VE", 24),
            ("https://youtube.com/shorts/M7lc1UVf-VE", "youtube", "M7lc1UVf-VE", 0),
            ("https://www.youtube-nocookie.com/embed/M7lc1UVf-VE", "youtube", "M7lc1UVf-VE", 0),
            ("https://vimeo.com/123456/abcd", "vimeo", "123456", 0),
            ("https://player.vimeo.com/video/123456?h=abcd", "vimeo", "123456", 0),
            ("https://www.dailymotion.com/video/x9abc_title", "dailymotion", "x9abc", 0),
            ("https://dai.ly/x9abc", "dailymotion", "x9abc", 0),
            ("https://streamable.com/abc123", "streamable", "abc123", 0),
            ("https://cdn.discordapp.com/attachments/a/clip.mp4?signature=x", "direct", "", 0),
        )
        for url, provider, identifier, start in examples:
            with self.subTest(url=url):
                link = V.video_link(url, trusted)
                self.assertIsNotNone(link)
                self.assertEqual((link.provider, link.video_id, link.start_seconds), (provider, identifier, start))
                self.assertTrue(S.is_embeddable_media_url(url))
                self.assertIn(url, S.direct_image_urls_in_message("watch " + url))
                self.assertEqual(S.message_text_without_image_links("watch " + url), "watch ")
                self.assertTrue(S.is_image_url_trusted_for_sender(url, "stranger", False, set()))

    def test_spoofs_nonvideo_pages_and_untrusted_hosts_never_embed(self):
        for url in (
            "http://youtu.be/M7lc1UVf-VE", "https://youtube.com.evil.test/watch?v=M7lc1UVf-VE",
            "https://youtube.com@evil.test/watch?v=M7lc1UVf-VE",
            "https://someone@youtube.com/watch?v=M7lc1UVf-VE",
            "https://youtube.com:8443/watch?v=M7lc1UVf-VE",
            "https://youtube.com/watch?v=short", "https://youtube.com/@profile",
            "https://youtube.com/playlist?list=example", "https://vimeo.com/profile",
            "https://untrusted.test/clip.mp4", "https://streamable.com/settings/account",
            "https://youtu.be/M7lc1UVf-VE/extra",
        ):
            with self.subTest(url=url):
                self.assertIsNone(V.video_link(url, trusted))
                self.assertFalse(S.is_click_to_play_video_url(url))

    def test_looping_media_preserves_existing_behavior(self):
        for url in ("https://media.tenor.com/example/clip.mp4",
                    "https://media.giphy.com/media/example/clip.mp4",
                    "https://tenor.com/view/example-123"):
            self.assertTrue(S.is_embeddable_media_url(url))
            self.assertFalse(S.is_click_to_play_video_url(url))

    def test_hover_escapes_metadata_and_omits_missing_fields(self):
        link = V.VideoLink("youtube", "M7lc1UVf-VE", "https://youtu.be/M7lc1UVf-VE")
        info = V.VideoInfo(link, "youtu.be", "<script>title</script>", "A & B", 3661)
        self.assertEqual(info.tooltip(), "youtu.be<br>&lt;script&gt;title&lt;/script&gt;<br>A &amp; B<br>1:01:01")
        self.assertEqual(V.VideoInfo(link, "youtu.be").tooltip(), "youtu.be")

    def test_metadata_failure_keeps_a_playable_fallback(self):
        link = V.video_link("https://youtu.be/M7lc1UVf-VE", trusted)
        with mock.patch.object(V, "_read_response", side_effect=ValueError("offline")):
            info, data = V.fetch_video_info(link, trusted)
        self.assertEqual(info.link, link)
        self.assertEqual(data, b"")
        self.assertIn("i.ytimg.com", info.thumbnail_url)

    def test_oembed_thumbnail_and_duration(self):
        link = V.video_link("https://vimeo.com/123456", trusted)
        png = io.BytesIO()
        Image.new("RGB", (640, 360), "blue").save(png, "PNG")
        metadata = json.dumps({"title": "Clip", "author_name": "Alex", "duration": 61,
                               "thumbnail_url": "https://i.vimeocdn.com/video/example.jpg"}).encode()
        with mock.patch.object(V, "_read_response", side_effect=[metadata, png.getvalue()]):
            info, data = V.fetch_video_info(link, trusted)
        self.assertEqual((info.title, info.author, info.duration), ("Clip", "Alex", 61))
        self.assertEqual(data, png.getvalue())

    def test_untrusted_redirect_is_rejected_before_request(self):
        response = mock.MagicMock()
        response.status_code = 302
        response.headers = {"Location": "https://evil.test/thumbnail.png"}
        response.__enter__.return_value = response
        with mock.patch.object(V.requests, "get", return_value=response) as get:
            with self.assertRaises(ValueError):
                V._read_response("https://i.ytimg.com/thumbnail.jpg", trusted, limit=100, accept="image/*")
        self.assertEqual(get.call_count, 1)
        self.assertFalse(get.call_args.kwargs["allow_redirects"])

    def test_oversized_metadata_is_not_read(self):
        response = mock.MagicMock()
        response.status_code = 200
        response.headers = {"Content-Length": "101"}
        response.__enter__.return_value = response
        with mock.patch.object(V.requests, "get", return_value=response):
            with self.assertRaises(ValueError):
                V._read_response("https://youtube.com/oembed", trusted, limit=100, accept="application/json")
        response.iter_content.assert_not_called()


class VideoControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_thumbnail_has_size_limit_black_border_and_play_symbol(self):
        frame = QImage(1920, 1080, QImage.Format.Format_RGB32)
        frame.fill(QColor("white"))
        result = V.video_thumbnail(frame, S.EMBEDDED_IMAGE_MAX_WIDTH, S.EMBEDDED_IMAGE_MAX_HEIGHT)
        self.assertLessEqual(result.width(), 256)
        self.assertLessEqual(result.height(), 96)
        self.assertEqual(result.pixelColor(0, 0), QColor("black"))
        self.assertEqual(result.pixelColor(result.width() - 1, 0), QColor("black"))
        self.assertNotEqual(result.pixelColor(result.width() // 2 - 12, result.height() // 2), QColor("white"))
        self.assertEqual(result.pixelColor(15, 15), QColor("white"))

    def test_each_theme_preserves_order_and_fits_tiny_width(self):
        player = V.VideoPlayer(slider_factory=S.ThemeSlider)
        player._update_state({"ready": True, "playing": True, "duration": 125, "position": 12})
        widgets = [player.play_button, player.seek, player.time_label,
                   player.mute_button, player.volume, player.fullscreen_button]
        sheets = set()
        for theme in ("Classic", "Glassy", "Modern"):
            player.set_theme(theme, tiny=True)
            sheets.add(player.controls.styleSheet())
            player.resize(330, 180)
            player.show()
            self.app.processEvents()
            row = player.controls.layout()
            self.assertEqual([row.itemAt(i).widget() for i in range(row.count())], widgets)
            self.assertLessEqual(row.minimumSize().width(), 330)
            self.assertEqual(player.play_button.accessibleName(), "Pause")
            self.assertFalse(player.play_button.icon().isNull())
        self.assertEqual(len(sheets), 3)
        player.stop()
        player.close()

    def test_controls_dispatch_and_poll_does_not_seek(self):
        player = V.VideoPlayer()
        player.info = V.VideoInfo(V.VideoLink("youtube", "M7lc1UVf-VE", ""), "youtube.com")
        player.web = mock.Mock()
        player._update_state({"ready": True, "playing": True, "position": 30, "duration": 120,
                              "volume": 60, "muted": False})
        player.web.page().runJavaScript.assert_not_called()
        player.toggle_play()
        player.seek.setValue(5000)
        player.toggle_mute()
        player.volume.setValue(40)
        scripts = [call.args[0] for call in player.web.page().runJavaScript.call_args_list]
        self.assertIn('"pause",0', scripts[0])
        self.assertIn('"seek",60.0', scripts[1])
        self.assertIn('"mute",true', scripts[2])
        self.assertIn('"volume",40', scripts[3])
        player.web = None
        player.stop()

    def test_fullscreen_restores_parent_size_and_stop_invalidates_callbacks(self):
        parent = QWidget()
        layout = QVBoxLayout(parent)
        player = V.VideoPlayer(parent)
        layout.addWidget(player)
        player.setMaximumHeight(200)
        generation = player._generation
        player.toggle_fullscreen()
        self.assertGreater(player.maximumHeight(), 200)
        player.toggle_fullscreen()
        self.assertIs(player.parentWidget(), parent)
        self.assertEqual(player.maximumHeight(), 200)
        self.assertIsNone(player._fullscreen)
        player.stop()
        self.assertGreater(player._generation, generation)
        self.assertFalse(player.timer.isActive())
        parent.close()

    def test_portrait_first_frame_updates_inline_object_dimensions(self):
        url = "https://cdn.discordapp.com/clip.mp4"
        info = V.VideoInfo(V.video_link(url, trusted), "cdn.discordapp.com")
        media = S.RemoteMediaPreview(url, b"", "video", None, info)
        client = SimpleNamespace(
            image_preview_cache={url: media}, animated_media_controllers={},
            current_image_preview_url=None, rendered_loaded_image_urls={url},
            rendered_image_positions={url: [1]},
            _scaled_inline_media_frame=S.EncryptedChatClient._scaled_inline_media_frame,
            _set_rendered_inline_image=mock.Mock(),
        )
        frame = QImage(1080, 1920, QImage.Format.Format_RGB32)
        frame.fill(QColor("white"))
        S.EncryptedChatClient._on_animated_media_frame(client, url, frame)
        update = client._set_rendered_inline_image.call_args
        self.assertEqual((update.args[1].width(), update.args[1].height()), (54, 96))
        self.assertTrue(update.kwargs["loaded"])

    def test_small_window_popout_can_fullscreen_and_dismiss(self):
        parent = QWidget()
        layout = QVBoxLayout(parent)
        player = V.VideoPlayer(parent)
        layout.addWidget(player)
        player.setMinimumHeight(100)
        player.setMaximumHeight(100)
        closed = mock.Mock()
        player.popup_closed.connect(closed)
        player.pop_out()
        dialog = player._fullscreen
        self.assertEqual(dialog.minimumWidth(), 480)
        self.assertFalse(dialog.isFullScreen())
        player.toggle_fullscreen()
        self.assertTrue(dialog.isFullScreen())
        player.toggle_fullscreen()
        self.assertFalse(dialog.isFullScreen())
        dialog.close()
        self.assertIs(player.parentWidget(), parent)
        self.assertEqual(player.minimumHeight(), 100)
        self.assertEqual(player.maximumHeight(), 100)
        closed.assert_called_once()
        parent.close()

    @unittest.skipUnless(shutil.which("node"), "Node is needed to exercise the provider bridge")
    def test_youtube_bridge_runs_with_controls_hidden_and_native_commands(self):
        info = V.VideoInfo(V.VideoLink("youtube", "M7lc1UVf-VE", "", 42), "youtube.com")
        # Execute the generated production JS against a fake official SDK.
        runner = r"""
const fs=require('fs'), vm=require('vm');
const html=fs.readFileSync(0,'utf8'), calls=[], intervals=[];
let options;
const c={window:{},location:{origin:'https://github.com'},setInterval:f=>intervals.push(f)};
c.YT={Player:function(id,o){options=o; return {
  playVideo:()=>calls.push(['play']), pauseVideo:()=>calls.push(['pause']),
  seekTo:(v)=>calls.push(['seek',v]), setVolume:v=>calls.push(['volume',v]),
  mute:()=>calls.push(['mute']), unMute:()=>calls.push(['unmute']),
  getCurrentTime:()=>12,getDuration:()=>120,getVolume:()=>60,isMuted:()=>false
};}};
vm.createContext(c);
for(const match of html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)) vm.runInContext(match[1],c);
c.onYouTubeIframeAPIReady(); options.events.onReady();
options.events.onStateChange({data:1});
c.window.spriteCommand('pause'); c.window.spriteCommand('seek',60);
c.window.spriteCommand('volume',40); c.window.spriteCommand('mute',true);
intervals.forEach(f=>f());
process.stdout.write(JSON.stringify({options,calls,state:c.window.spriteState()}));
"""
        result = subprocess.run([shutil.which("node"), "-e", runner],
                                input=V.provider_player_html(info), text=True,
                                capture_output=True, check=True)
        output = json.loads(result.stdout)
        self.assertEqual(output["options"]["playerVars"]["controls"], 0)
        self.assertEqual(output["options"]["playerVars"]["disablekb"], 1)
        self.assertEqual(output["options"]["playerVars"]["start"], 42)
        self.assertEqual(output["calls"], [["play"], ["pause"], ["seek", 60], ["volume", 40], ["mute"]])
        self.assertEqual(output["state"]["duration"], 120)
        self.assertTrue(output["state"]["playing"])

    def test_vimeo_unlisted_hash_and_provider_content_are_safe(self):
        info = V.VideoInfo(V.video_link("https://vimeo.com/123456/abc123", trusted),
                           "vimeo.com", "</script><script>evil()</script>")
        page = V.provider_player_html(info)
        self.assertIn("controls=0", page)
        self.assertIn("h=abc123", page)
        self.assertNotIn("evil()", page)


if __name__ == "__main__":
    unittest.main()
