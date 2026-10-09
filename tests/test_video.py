import io
from contextlib import ExitStack
import json
import os
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest import mock

from PIL import Image
from PySide6.QtCore import QPoint, QPointF, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QImage, QWheelEvent
from PySide6.QtTest import QTest
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

    def test_serialized_paused_state_enables_controls_and_preserves_duration(self):
        player = V.VideoPlayer()
        player.info = V.VideoInfo(V.VideoLink("youtube", "M7lc1UVf-VE", ""), "youtube.com")
        player.web = mock.Mock()
        player.web.page().runJavaScript.side_effect = lambda script, callback: callback(json.dumps({
            "ready": True, "playerState": 2, "playing": False, "duration": 245,
            "position": 0, "volume": 80, "muted": False,
        }))
        player._poll()
        self.assertIn("JSON.stringify", player.web.page().runJavaScript.call_args.args[0])
        self.assertTrue(player.play_button.isEnabled())
        self.assertTrue(player.seek.isEnabled())
        self.assertEqual(player.time_label.text(), "0:00 / 4:05")
        player._update_state(json.dumps({"ready": False, "playerState": 2, "duration": 0}))
        self.assertTrue(player._ready)
        self.assertEqual(player._duration, 245)
        with mock.patch.object(player, "_error") as error:
            player._check_ready(player._generation)
            error.assert_not_called()
        player.web = None
        player.stop()

    def test_volume_wheel_changes_ten_percent_and_seek_ignores_wheel(self):
        player = V.VideoPlayer(slider_factory=S.ThemeSlider)
        player.info = V.VideoInfo(V.VideoLink("youtube", "M7lc1UVf-VE", ""), "youtube.com")
        player._update_state({"ready": True, "duration": 120, "position": 30, "volume": 50})
        player._command = mock.Mock()

        def wheel(widget, delta, modifiers=Qt.KeyboardModifier.NoModifier):
            event = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(), QPoint(0, delta),
                                Qt.MouseButton.NoButton, modifiers, Qt.ScrollPhase.NoScrollPhase, False)
            QApplication.sendEvent(widget, event)

        for theme in ("Classic", "Glassy", "Modern"):
            player.set_theme(theme, tiny=True)
            player.volume.setValue(50)
            player._command.reset_mock()
            wheel(player.volume, 120)
            self.assertEqual(player.volume.value(), 60)
            wheel(player.mute_button, -120)
            self.assertEqual(player.volume.value(), 50)
            wheel(player.mute_button, 240, Qt.KeyboardModifier.ControlModifier)
            self.assertEqual(player.volume.value(), 70)
            wheel(player.volume, 60)
            self.assertEqual(player.volume.value(), 70)
            wheel(player.mute_button, 60)
            self.assertEqual(player.volume.value(), 80)
            wheel(player.mute_button, 600)
            self.assertEqual(player.volume.value(), 100)
            wheel(player.volume, -1200)
            self.assertEqual(player.volume.value(), 0)
            self.assertTrue(all(call.args[0] == "volume" for call in player._command.call_args_list))
            player._command.reset_mock()
            position = player.seek.value()
            wheel(player.seek, 120)
            wheel(player.seek, -120, Qt.KeyboardModifier.ShiftModifier)
            self.assertEqual(player.seek.value(), position)
            player._command.assert_not_called()
        player.stop()

    def test_last_valid_video_frame_stays_centered_over_black_during_resize(self):
        from PySide6.QtMultimedia import QVideoFrame
        view = V.VideoFrameView()
        image = QImage(160, 90, QImage.Format.Format_RGB32)
        image.fill(QColor("blue"))
        view.set_frame(image)
        view.show()
        for width, height in ((320, 240), (480, 150), (100, 250), (640, 360)):
            view.resize(width, height)
            view._receive_frame(QVideoFrame())
            result = view.grab().toImage()
            self.assertEqual(result.pixelColor(width // 2, height // 2), QColor("blue"))
            self.assertEqual(view.frame, image)
            fitted = image.size().scaled(view.size(), Qt.AspectRatioMode.KeepAspectRatio)
            left, top = (width - fitted.width()) // 2, (height - fitted.height()) // 2
            if left:
                self.assertEqual(result.pixelColor(0, height // 2), QColor("black"))
                self.assertEqual(result.pixelColor(width - 1, height // 2), QColor("black"))
            if top:
                self.assertEqual(result.pixelColor(width // 2, 0), QColor("black"))
                self.assertEqual(result.pixelColor(width // 2, height - 1), QColor("black"))
        view.close()
        view.deleteLater()

    def test_browser_frames_stay_centered_and_gpu_host_is_separate_during_resize(self):
        root, browser = QWidget(), QWidget()
        root.resize(320, 240)
        layout = QVBoxLayout(root)
        browser.setStyleSheet("background:blue")
        mirror = V.BrowserFrameView(browser)
        layout.addWidget(mirror)
        root.show()
        QTest.qWait(30)
        mirror._capture_frame()
        self.assertFalse(root.isAncestorOf(browser))
        self.assertTrue(browser.testAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen))
        self.assertFalse(browser.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
        original = mirror.frame.copy()
        self.assertFalse(original.isNull())
        browser_size = browser.size()
        for size in ((480, 240), (100, 250), (640, 360)):
            root.resize(*size)
            layout.activate()
            # Only the raster display resizes; Chromium's render surface and
            # the last valid frame remain stable throughout the drag.
            self.assertEqual(mirror.frame, original)
            self.assertEqual(browser.size(), browser_size)
            result = mirror.grab().toImage()
            self.assertEqual(result.pixelColor(mirror.width()//2, mirror.height()//2), QColor("blue"))
            fitted = original.size().scaled(mirror.size(), Qt.AspectRatioMode.KeepAspectRatio)
            if fitted.width() < mirror.width():
                self.assertEqual(result.pixelColor(0, mirror.height()//2), QColor("black"))
            if fitted.height() < mirror.height():
                self.assertEqual(result.pixelColor(mirror.width()//2, 0), QColor("black"))
        wrong_size = QImage(15, 15, QImage.Format.Format_RGB32)
        wrong_size.fill(QColor("green"))
        for invalid in (QImage(), wrong_size):
            with mock.patch.object(mirror, "_snapshot", return_value=invalid):
                mirror._capture_frame()
            self.assertEqual(mirror.frame, original)
        mirror.release()
        self.assertFalse(mirror._frame_timer.isActive())
        self.assertTrue(mirror.frame.isNull())
        self.assertIsNone(mirror.browser)
        root.close(); browser.close()
        root.deleteLater(); browser.deleteLater()

    @unittest.skipUnless(shutil.which("node"), "Node is needed to exercise the browser styling script")
    def test_youtube_overlay_styling_handles_new_markup_and_mutations_only_in_embed_documents(self):
        runner = r"""
const fs=require('fs'), vm=require('vm'), source=fs.readFileSync(0,'utf8');
const output=[];
function element(kind='') {
  const e={kind, children:[], value:'', priority:'',
    style:{getPropertyValue:()=>e.value, getPropertyPriority:()=>e.priority,
           setProperty:(n,v,p)=>{e.value=v;e.priority=p;}},
    matches:s=>['error','ad'].includes(kind),
    querySelector:s=>e.children.find(x=>s==='video'?x.kind==='video':['error','ad'].includes(x.kind))||null};
  e.add=x=>{e.children.push(x);x.parentElement=e;};return e;
}
for (const [hostname,pathname] of [
  ['www.youtube.com','/embed/M7lc1UVf-VE'], ['www.youtube-nocookie.com','/embed/M7lc1UVf-VE'],
  ['www.youtube.com','/watch'], ['www.youtube.com.evil.test','/embed/M7lc1UVf-VE'],
  ['player.vimeo.com','/embed/M7lc1UVf-VE']]) {
  const player=element(),video=element('video'),ui=element('unknown-new-control');
  const error=element('error'),ad=element('ad');
  player.add(video);player.add(ui);player.add(error);player.add(ad);
  let observer;
  const c={window:{},location:{hostname,pathname},document:{getElementById:()=>player},
           MutationObserver:class {constructor(f){observer=f;} observe(){}}};
  vm.runInNewContext(source,c);
  const initial=ui.value;
  const added=element('new-caption-ui');player.add(added);
  ui.value='block';if(observer)observer();
  vm.runInNewContext(source,c); // Reinjection must be idempotent.
  output.push({initial,restored:ui.value,added:added.value,error:error.value,ad:ad.value,controls:video.controls});
}
process.stdout.write(JSON.stringify(output));
"""
        result = subprocess.run([shutil.which("node"), "-e", runner], input=V.YOUTUBE_CHROME_SCRIPT,
                                text=True, capture_output=True, check=True)
        output = json.loads(result.stdout)
        for state in output[:2]:
            self.assertEqual(state, {"initial":"none", "restored":"none", "added":"none",
                                     "error":"", "ad":"", "controls":False})
        for state in output[2:]:
            self.assertEqual(state["initial"], "")
            self.assertEqual(state["restored"], "block")
            self.assertEqual(state["added"], "")
        youtube = V.VideoInfo(V.VideoLink("youtube", "M7lc1UVf-VE", ""), "youtube.com")
        self.assertIn("pointer-events:none", V.provider_player_html(youtube))
        vimeo = V.VideoInfo(V.VideoLink("vimeo", "123456", ""), "vimeo.com")
        self.assertNotIn("pointer-events:none", V.provider_player_html(vimeo))

    def test_quick_renderer_is_isolated_from_translucent_window_and_survives_resize(self):
        runner = r"""
import sys
from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QColor
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout
from spritelink_video import BrowserFrameView
app = QApplication([])
root = QWidget(); root.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
root.resize(640, 480); QVBoxLayout(root)
backend = QWidget(); layout = QVBoxLayout(backend); layout.setContentsMargins(0, 0, 0, 0)
mirror = BrowserFrameView(backend); root.layout().addWidget(mirror); root.show()
quick = QQuickWidget(); quick.setMinimumSize(0, 0)
quick.setResizeMode(QQuickWidget.ResizeMode.SizeRootObjectToView)
quick.setSource(QUrl.fromLocalFile(sys.argv[1])); layout.addWidget(quick)
QTest.qWait(80)
assert root.findChildren(QQuickWidget) == []
assert backend.findChildren(QQuickWidget) == [quick]
mirror._capture_frame()
for size in ((480, 360), (800, 450), (400, 600)):
    old = mirror.frame.copy()
    root.resize(*size); root.layout().activate()
    assert mirror.frame == old
    QTest.qWait(140)
    assert mirror.frame.pixelColor(mirror.frame.width()//2, mirror.frame.height()//2) == QColor('blue')
    assert mirror.frame.size() == backend.size()
    assert root.findChildren(QQuickWidget) == []
mirror.release(); backend.deleteLater(); root.close(); root.deleteLater(); QTest.qWait(20)
"""
        with TemporaryDirectory() as folder:
            filename = folder + "/renderer.qml"
            with open(filename, "w", encoding="utf-8") as qml:
                qml.write('import QtQuick\nRectangle { width: 320; height: 180; color: "blue" }')
            result = subprocess.run([sys.executable, "-c", runner, filename],
                                    env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software"},
                                    text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_connecting_bridge_never_destroys_a_loaded_iframe_on_timeout(self):
        player = V.VideoPlayer()
        player.info = V.VideoInfo(V.VideoLink("youtube", "M7lc1UVf-VE", ""), "youtube.com")
        player.web = mock.Mock()
        player._check_ready(player._generation)
        player.web.setUrl.assert_not_called()
        player.web.stop.assert_not_called()
        self.assertTrue(player._poll_pending)
        # A stale callback from a dismissed player cannot resurrect controls.
        callback = player.web.page().runJavaScript.call_args.args[1]
        player.web = None
        player.stop()
        callback(json.dumps({"ready": True, "duration": 180}))
        self.assertFalse(player._ready)

    def test_missing_or_malformed_poll_keeps_initialized_player_ready(self):
        player = V.VideoPlayer()
        player._update_state({"ready": True, "duration": 180})
        for state in (None, "", "null", "not JSON", {"duration": None},
                      {"ready": False, "playerState": 3, "duration": float("nan")}):
            player._update_state(state)
            self.assertTrue(player.play_button.isEnabled())
            self.assertTrue(player.seek.isEnabled())
            self.assertEqual(player._duration, 180)
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
  getCurrentTime:()=>12,getDuration:()=>120,getVolume:()=>60,isMuted:()=>false,
  getPlayerState:()=>1
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

    @unittest.skipUnless(shutil.which("node"), "Node is needed to exercise the provider bridge")
    def test_youtube_duration_and_readiness_are_read_while_paused_without_js_timer(self):
        info = V.VideoInfo(V.VideoLink("youtube", "M7lc1UVf-VE", ""), "youtube.com", duration=90)
        runner = r"""
const fs=require('fs'),vm=require('vm');
const html=fs.readFileSync(0,'utf8'); let options, duration=120;
const c={window:{}, location:{origin:'https://github.com'}};
c.YT={Player:function(id,o){options=o;return {
  playVideo:()=>{}, getPlayerState:()=>2, getCurrentTime:()=>0,
  getDuration:()=>duration, getVolume:()=>100,isMuted:()=>false
};}};
vm.createContext(c);
for(const match of html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)) vm.runInContext(match[1],c);
c.onYouTubeIframeAPIReady();
const initialized={...c.window.spriteState()};
duration=0;
const paused=c.window.spriteState();
process.stdout.write(JSON.stringify({initialized,paused}));
"""
        result = subprocess.run([shutil.which("node"), "-e", runner],
                                input=V.provider_player_html(info), text=True,
                                capture_output=True, check=True)
        output = json.loads(result.stdout)
        self.assertTrue(output["initialized"]["ready"])
        self.assertFalse(output["paused"]["playing"])
        self.assertEqual(output["paused"]["duration"], 120)

    def test_vimeo_unlisted_hash_and_provider_content_are_safe(self):
        info = V.VideoInfo(V.video_link("https://vimeo.com/123456/abc123", trusted),
                           "vimeo.com", "</script><script>evil()</script>")
        page = V.provider_player_html(info)
        self.assertIn("controls=0", page)
        self.assertIn("h=abc123", page)
        self.assertNotIn("evil()", page)


class VideoModeIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.stack = ExitStack()
        self.stack.enter_context(mock.patch.object(S, "load_config", side_effect=S.default_config))
        self.stack.enter_context(mock.patch.object(S, "save_config"))
        for method in ("_start_network_thread", "_load_saved_history_for_current_room",
                       "_check_for_updates", "_consume_notification_activation"):
            self.stack.enter_context(mock.patch.object(S.EncryptedChatClient, method))
        self.stack.enter_context(mock.patch.object(V.VideoPlayer, "_load_provider"))
        self.root = S.MainWindow()
        self.client = S.EncryptedChatClient(self.root)
        self.client.config_data["ui_size"] = "Tiny"
        self.client._apply_theme()
        self.client._apply_ui_size()
        self.root.resize(470, 250)
        self.root.show()
        QTest.qWait(20)
        self.url = "https://youtu.be/M7lc1UVf-VE"
        info = V.VideoInfo(V.video_link(self.url, trusted), "youtu.be", duration=245)
        self.client.image_preview_cache[self.url] = S.RemoteMediaPreview(self.url, b"", "video", None, info)
        self.client._show_image_preview_popup(self.url)
        self.player = self.client.video_player
        self.player._update_state(json.dumps({"ready": True, "playerState": 2, "duration": 245, "position": 35}))
        QTest.qWait(20)

    def tearDown(self):
        self.client._closing = True
        self.player.stop()
        for timer in self.root.findChildren(QTimer):
            timer.stop()
        self.root.close_callback = None
        self.app.removeEventFilter(self.client)
        self.root.close()
        self.root.deleteLater()
        QTest.qWait(20)
        self.stack.close()

    def test_tiny_window_keeps_youtube_inside_main_window(self):
        self.assertEqual(self.root.width(), 470)
        self.assertEqual(self.root.height(), 250)
        self.assertEqual(self.player._mode, "main")
        self.assertIsNone(self.player._fullscreen)
        self.assertTrue(self.client.image_preview_overlay.isVisible())
        self.assertIs(self.player.parentWidget(), self.client.image_preview_panel)
        self.assertTrue(self.player.play_button.isEnabled())
        self.assertEqual(self.player.time_label.text(), "0:35 / 4:05")

    def test_mode_buttons_preserve_playback_remove_overlay_and_anchor_mini(self):
        info, generation, position = self.player.info, self.player._generation, self.player.seek.value()
        self.player.popout_button.click()
        dialog = self.player._fullscreen
        self.assertIsNotNone(dialog)
        self.assertEqual(dialog.windowModality(), Qt.WindowModality.NonModal)
        self.assertFalse(self.client.image_preview_overlay.isVisible())
        self.player.popout_button.click()
        self.player.mini_button.click()
        panel = self.client.video_mini_panel
        self.assertIsNone(self.player._fullscreen)
        self.assertTrue(panel.isVisible())
        self.assertIs(panel.parentWidget(), self.client.chat_display.parentWidget())
        self.assertIs(self.player.parentWidget(), panel)
        self.assertFalse(self.client.image_preview_overlay.isVisible())
        self.assertTrue(self.player.volume.isHidden())
        self.assertTrue(self.player.play_button.isEnabled())
        self.root.resize(630, 360)
        QTest.qWait(30)
        rect = self.client.chat_display.viewport().rect()
        panel_position = self.client.chat_display.viewport().mapFromGlobal(
            panel.mapToGlobal(panel.rect().topLeft()))
        self.assertEqual(rect.width() - panel_position.x() - panel.width(), 6)
        self.assertEqual(panel_position.y(), 6)
        self.assertIs(self.player.info, info)
        self.assertEqual(self.player._generation, generation)
        self.assertEqual(self.player.seek.value(), position)
        self.player.mini_button.click()
        self.assertEqual(self.player._mode, "main")
        self.assertTrue(self.client.image_preview_overlay.isVisible())
        self.assertFalse(panel.isVisible())
        self.assertEqual(self.client.current_image_preview_url, self.url)

    def test_mini_fullscreen_restores_mini_and_popout_close_stops(self):
        self.player.mini_button.click()
        self.player.toggle_fullscreen()
        self.assertFalse(self.client.video_mini_panel.isVisible())
        self.player.toggle_fullscreen()
        self.assertEqual(self.player._mode, "mini")
        self.assertTrue(self.client.video_mini_panel.isVisible())
        self.assertFalse(self.client.image_preview_overlay.isVisible())
        self.player.mini_button.click()
        self.player.popout_button.click()
        self.player._fullscreen.close()
        self.assertIsNone(self.player.info)
        self.assertIsNone(self.client.current_image_preview_url)
        self.assertFalse(self.client.image_preview_overlay.isVisible())

    def test_fullscreen_uses_entire_screen_and_square_chrome_in_each_theme(self):
        for theme in ("Classic", "Glassy", "Modern"):
            self.client.theme_var.set(theme)
            self.client._apply_theme()
            self.player.toggle_fullscreen()
            QTest.qWait(30)
            dialog = self.player._fullscreen
            self.assertTrue(dialog.isFullScreen())
            self.assertEqual(dialog.windowType(), Qt.WindowType.Window)
            self.assertTrue(dialog.windowFlags() & Qt.WindowType.FramelessWindowHint)
            self.assertEqual(dialog.geometry(), dialog.screen().geometry())
            self.assertEqual(self.player.geometry(), dialog.rect())
            self.assertTrue(dialog.property("spritelinkVideoFullscreen"))
            self.assertIn("border-radius:0px", self.player.controls.styleSheet())
            self.assertTrue(self.player.rect().contains(self.player.controls.geometry()))
            self.assertTrue(self.player.rect().contains(self.player.mode_controls.geometry()))
            QTest.keyClick(dialog, Qt.Key.Key_Escape)
            self.assertEqual(self.player._mode, "main")
            self.assertIsNone(self.player._fullscreen)
            self.assertTrue(self.client.image_preview_overlay.isVisible())

    def test_popout_fullscreen_escape_restores_its_window_and_geometry(self):
        self.player.popout_button.click()
        dialog = self.player._fullscreen
        QTest.qWait(20)
        geometry, flags = dialog.geometry(), dialog.windowFlags()
        for theme in ("Glassy", "Modern", "Classic"):
            self.client.theme_var.set(theme)
            self.client._apply_theme()
            self.player.toggle_fullscreen()
            QTest.qWait(20)
            self.assertEqual(dialog.geometry(), dialog.screen().geometry())
            self.assertTrue(dialog.windowFlags() & Qt.WindowType.FramelessWindowHint)
            self.assertEqual(self.player.mode_controls.layout().contentsMargins().bottom(), 0)
            QTest.keyClick(dialog, Qt.Key.Key_Escape)
            QTest.qWait(20)
            self.assertFalse(dialog.isFullScreen())
            self.assertFalse(dialog.property("spritelinkVideoFullscreen"))
            self.assertEqual(dialog.geometry(), geometry)
            self.assertEqual(dialog.windowFlags(), flags)
            self.assertEqual(self.player._mode, "popout")
            self.assertIsNotNone(self.player.info)
            self.assertFalse(self.client.image_preview_overlay.isVisible())
            self.assertEqual(self.player.mode_controls.layout().contentsMargins().bottom(), 8)

    def test_tray_suspension_stops_mini_when_main_overlay_is_hidden(self):
        self.player.mini_button.click()
        self.client._suspend_for_tray()
        self.assertIsNone(self.player.info)
        self.assertIsNone(self.client.current_image_preview_url)
        self.assertFalse(self.client.video_mini_panel.isVisible())

    def test_footer_buttons_share_row_and_follow_mode_visibility(self):
        for theme in ("Classic", "Glassy", "Modern"):
            self.client.theme_var.set(theme)
            self.client._apply_theme()
            QTest.qWait(20)
            buttons = (self.player.popout_button, self.player.mini_button,
                       self.player.browser_button, self.player.dismiss_button)
            self.assertTrue(all(button.isVisible() for button in buttons))
            self.assertEqual(len({button.geometry().center().y() for button in buttons}), 1)
            self.assertTrue(all(self.player.mode_controls.rect().contains(button.geometry()) for button in buttons))
            self.assertFalse(self.client.image_preview_footer.isVisible())
            self.player.popout_button.click()
            QTest.qWait(20)
            self.assertFalse(self.player.mini_button.isVisible())
            self.assertTrue(self.player.source_label.isVisible())
            self.assertEqual(self.player.source_label.toolTip(), self.url)
            self.assertTrue(self.player.source_label.text())
            self.player.popout_button.click()
            self.player.mini_button.click()
            self.assertFalse(self.player.popout_button.isVisible())
            self.assertTrue(self.player.mini_button.isVisible())
            self.assertFalse(self.player.browser_button.isVisible())
            self.assertFalse(self.player.dismiss_button.isVisible())
            self.player.mini_button.click()
        with mock.patch.object(self.client, "_open_url_in_browser") as open_browser:
            self.player.popout_button.click()
            self.player.browser_button.click()
            open_browser.assert_called_once_with(self.url, None)
        self.player.dismiss_button.click()
        self.assertIsNone(self.player.info)
        self.assertFalse(self.client.image_preview_overlay.isVisible())

    def test_mini_video_surface_stays_sixteen_by_nine_across_sizes_and_themes(self):
        self.player.mini_button.click()
        for theme in ("Classic", "Glassy", "Modern"):
            self.client.theme_var.set(theme)
            self.client._apply_theme()
            for width, height in ((470, 250), (630, 360), (900, 700)):
                self.root.resize(width, height)
                QTest.qWait(30)
                surface = self.player.surface
                self.assertLessEqual(abs(surface.height() - surface.width() * 9 / 16), 1)
                panel = self.client.video_mini_panel
                self.assertLessEqual(panel.geometry().bottom(), panel.parentWidget().height())
                self.assertTrue(self.player.rect().contains(self.player.controls.geometry()))
                self.assertTrue(self.player.rect().contains(self.player.mode_controls.geometry()))

    def test_mini_persists_across_room_switches_and_restores_original_video(self):
        self.player.mini_button.click()
        info, generation, position = self.player.info, self.player._generation, self.player.seek.value()
        with mock.patch.object(self.client, "_request_network_refresh"), \
             mock.patch.object(self.client, "_request_subscription_refresh"):
            for room in ("other-room", "global"):
                self.client.active_chatroom_id = room
                self.client._switch_active_chatroom()
                QTest.qWait(25)
                self.assertTrue(self.client.video_mini_panel.isVisible())
                self.assertFalse(self.client.image_preview_overlay.isVisible())
                self.assertEqual(self.client.current_image_preview_url, self.url)
                self.assertIs(self.player.info, info)
                self.assertEqual(self.player._generation, generation)
                self.assertEqual(self.player.seek.value(), position)
        self.player.mini_button.click()
        self.assertTrue(self.client.image_preview_overlay.isVisible())
        self.assertEqual(self.player.source_label.toolTip(), self.url)
        self.player.dismiss_button.click()
        self.assertIsNone(self.client.current_image_preview_url)
        self.assertIsNone(self.player.info)

    def test_popout_persists_across_room_switches_in_normal_and_fullscreen_modes(self):
        self.player.popout_button.click()
        dialog = self.player._fullscreen
        info, generation, position = self.player.info, self.player._generation, self.player.seek.value()
        with mock.patch.object(self.client, "_request_network_refresh"), \
             mock.patch.object(self.client, "_request_subscription_refresh"):
            for fullscreen in (False, True):
                if fullscreen:
                    self.player.toggle_fullscreen()
                for room in ("other-room", "global"):
                    self.client.active_chatroom_id = room
                    self.client._switch_active_chatroom()
                    QTest.qWait(20)
                    self.assertIs(self.player._fullscreen, dialog)
                    self.assertTrue(dialog.isVisible())
                    self.assertEqual(dialog.isFullScreen(), fullscreen)
                    self.assertFalse(self.client.image_preview_overlay.isVisible())
                    self.assertEqual(self.client.current_image_preview_url, self.url)
                    self.assertIs(self.player.info, info)
                    self.assertEqual(self.player._generation, generation)
                    self.assertEqual(self.player.seek.value(), position)
        self.player.popout_button.click()
        self.assertEqual(self.player._mode, "main")
        self.assertTrue(self.client.image_preview_overlay.isVisible())
        self.assertEqual(self.player.source_label.toolTip(), self.url)
        self.player.dismiss_button.click()
        self.assertIsNone(self.player.info)
        self.assertIsNone(self.client.current_image_preview_url)

    def test_clearing_room_for_reconnect_still_stops_popout_playback(self):
        self.player.popout_button.click()
        self.client._clear_visible_room()
        self.assertIsNone(self.player.info)
        self.assertIsNone(self.player._fullscreen)
        self.assertIsNone(self.client.current_image_preview_url)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is needed to create a native playback fixture")
    def test_native_video_survives_mini_popout_and_window_cleanup(self):
        with TemporaryDirectory() as folder:
            filename = folder + "/clip.avi"
            subprocess.run([shutil.which("ffmpeg"), "-loglevel", "error", "-f", "lavfi",
                            "-i", "color=c=blue:s=64x36:d=2:r=10", "-c:v", "mpeg4", filename],
                           capture_output=True, check=True)
            info = V.VideoInfo(V.VideoLink("direct", "", QUrl.fromLocalFile(filename).toString()), "local")
            self.player.load(info)
            for _ in range(40):
                QTest.qWait(25)
                if self.player._ready and self.player.video_widget.videoSink().videoFrame().isValid():
                    break
            self.assertTrue(self.player._ready)
            self.assertTrue(self.player.video_widget.videoSink().videoFrame().isValid())
            backend, generation = self.player.media_player, self.player._generation
            backend.pause()
            for theme in ("Glassy", "Modern", "Classic", "Glassy"):
                self.client.theme_var.set(theme)
                self.client._apply_theme()
                for size in ((630, 360), (470, 250), (720, 500)):
                    self.root.resize(*size)
                    QTest.qWait(20)
                    frame = self.player.video_widget.grab().toImage()
                    color = frame.pixelColor(frame.width() // 2, frame.height() // 2)
                    self.assertGreater(color.blue(), 200)
                    self.assertLess(color.red(), 25)
                    self.assertLess(color.green(), 25)
                    self.assertIs(self.player.media_player, backend)
                    self.assertEqual(self.player._generation, generation)
            position = backend.position()
            for mode in ("mini", "popout", "main"):
                self.client._set_video_player_mode(mode)
                QTest.qWait(25)
                self.assertIs(self.player.media_player, backend)
                self.assertEqual(self.player._generation, generation)
                self.assertEqual(backend.position(), position)
            self.player.stop()
            QTest.qWait(25)


if __name__ == "__main__":
    unittest.main()
