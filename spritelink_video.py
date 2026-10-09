"""Video link metadata and click-to-play players for SpriteLink.

Provider HTML is never executed. Only canonical player URLs are embedded, and
the caller's trusted-link check also gates metadata and thumbnail requests.
"""

from dataclasses import dataclass
from html import escape
import io
import json
import math
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from PIL import Image
from spritelink_http import requests
from PySide6.QtCore import QEvent, QObject, QRect, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPalette, QPen, QPixmap, QPolygonF
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import (
    QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QSlider,
    QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)


VIDEO_EXTENSIONS = (".mp4", ".webm", ".mov", ".m4v")
PLAYER_BASE_URL = "https://github.com/glasspage/SpriteLink/"
MAX_METADATA_BYTES = 1024 * 1024
MAX_THUMBNAIL_BYTES = 8 * 1024 * 1024
MAX_THUMBNAIL_PIXELS = 16 * 1024 * 1024
YOUTUBE_ZOOM_FACTOR = 0.25
DEFAULT_VIDEO_VOLUME = 20
VIDEO_EMBED_BORDER_COLOR = "#0000ff"


YOUTUBE_CHROME_SCRIPT = r"""
(() => {
  if (!['youtube.com', 'www.youtube.com', 'youtube-nocookie.com', 'www.youtube-nocookie.com'].includes(location.hostname)
      || !location.pathname.startsWith('/embed/')) return;
  const key = '__spritelinkChromeV2';
  if (window[key]) { window[key](); return; }
  const hide = element => {
    if (element.style.getPropertyValue('display') !== 'none'
        || element.style.getPropertyPriority('display') !== 'important') {
      element.style.setProperty('display', 'none', 'important');
    }
  };
  const refresh = () => {
    const player = document.getElementById('movie_player');
    if (!player) return;
    // Follow the actual video layer instead of depending on YouTube's
    // changing control class names. Keep error messages and ad UI intact.
    const video = player.querySelector('video');
    if (!video) return;
    for (let layer = video; layer && layer !== player; layer = layer.parentElement) {
      for (const sibling of layer.parentElement.children) {
        if (sibling !== layer && !sibling.matches('.ytp-error,.ytp-ad-module')
            && !sibling.querySelector('.ytp-error,.ytp-ad-module')) hide(sibling);
      }
    }
    video.controls = false;
  };
  window[key] = refresh;
  // Direct style properties also work when a site's CSP rejects a new
  // inline stylesheet. Reapply after controls are inserted or restyled.
  new MutationObserver(refresh).observe(document, {
    subtree:true, childList:true, attributes:true, attributeFilter:['class','style']
  });
  refresh();
})();
"""


def install_youtube_chrome(profile) -> None:
    from PySide6.QtWebEngineCore import QWebEngineScript
    script = QWebEngineScript()
    script.setName("SpriteLink YouTube controls")
    script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
    script.setWorldId(QWebEngineScript.ScriptWorldId.ApplicationWorld)
    script.setRunsOnSubFrames(True)
    script.setSourceCode(YOUTUBE_CHROME_SCRIPT)
    profile.scripts().insert(script)


@dataclass(frozen=True)
class VideoLink:
    provider: str
    video_id: str
    source_url: str
    start_seconds: int = 0


@dataclass(frozen=True)
class VideoInfo:
    link: VideoLink
    hostname: str
    title: str = ""
    author: str = ""
    duration: int = 0
    thumbnail_url: str = ""

    def tooltip(self) -> str:
        lines = [self.hostname, self.title, self.author]
        if self.duration > 0:
            lines.append(format_video_time(self.duration))
        return "<br>".join(escape(line) for line in lines if line)


def format_video_time(seconds: float) -> str:
    value = max(0, int(seconds))
    hours, remaining = divmod(value, 3600)
    minutes, seconds = divmod(remaining, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _start_time(query: dict) -> int:
    raw = str(query.get("t", query.get("start", ["0"]))[0])
    if raw.isdigit():
        return min(int(raw), 7 * 24 * 3600)
    match = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", raw)
    if not match:
        return 0
    return min(sum(int(value or 0) * scale for value, scale in
                   zip(match.groups(), (3600, 60, 1))), 7 * 24 * 3600)


def video_link(url: str, trusted) -> VideoLink | None:
    """Recognize videos only after the shared trusted-link gate accepts them."""
    if not trusted(url):
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.username or parsed.port not in (None, 443):
            return None
        host = (parsed.hostname or "").lower().rstrip(".")
        path = parsed.path.strip("/").split("/")
        query = parse_qs(parsed.query)
    except ValueError:
        return None
    identifier = ""
    provider = ""
    if host in ("youtube.com", "www.youtube.com", "m.youtube.com",
                "music.youtube.com", "youtube-nocookie.com", "www.youtube-nocookie.com"):
        if path[0] == "watch":
            identifier = query.get("v", [""])[0]
        elif len(path) == 2 and path[0] in ("embed", "shorts", "live"):
            identifier = path[1]
        provider = "youtube"
    elif host in ("youtu.be", "www.youtu.be") and len(path) == 1:
        identifier, provider = path[0], "youtube"
    elif host in ("vimeo.com", "www.vimeo.com", "player.vimeo.com"):
        # Public videos and unlisted links (ID/hash), excluding profiles.
        if path[0].isdigit():
            identifier, provider = path[0], "vimeo"
        elif len(path) >= 2 and path[0] == "video" and path[1].isdigit():
            identifier, provider = path[1], "vimeo"
    elif host in ("dailymotion.com", "www.dailymotion.com"):
        if len(path) >= 2 and path[0] == "video":
            identifier, provider = path[1].split("_", 1)[0], "dailymotion"
    elif host == "dai.ly" and len(path) == 1:
        identifier, provider = path[0], "dailymotion"
    elif host in ("streamable.com", "www.streamable.com"):
        if len(path) == 1:
            identifier, provider = path[0], "streamable"
        elif len(path) == 2 and path[0] in ("e", "o"):
            identifier, provider = path[1], "streamable"
    elif parsed.path.lower().endswith(VIDEO_EXTENSIONS):
        return VideoLink("direct", "", url)
    if provider == "youtube" and not re.fullmatch(r"[A-Za-z0-9_-]{11}", identifier):
        return None
    if not provider or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identifier):
        return None
    return VideoLink(provider, identifier, url, _start_time(query))


def _read_response(url: str, trusted, *, limit: int, accept: str) -> bytes:
    # Validate BEFORE each request, including redirects; never follow arbitrary
    # oEmbed thumbnail URLs or redirects with requests' automatic redirecting.
    for _ in range(4):
        if not trusted(url):
            raise ValueError("Untrusted video metadata URL")
        with requests.get(url, headers={"User-Agent": "SpriteLink", "Accept": accept},
                          stream=True, timeout=(5, 10), allow_redirects=False) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                url = urljoin(url, response.headers.get("Location", ""))
                continue
            response.raise_for_status()
            if int(response.headers.get("Content-Length", "0")) > limit:
                raise ValueError("Video metadata is too large")
            content = bytearray()
            for chunk in response.iter_content(65536):
                content.extend(chunk)
                if len(content) > limit:
                    raise ValueError("Video metadata is too large")
            return bytes(content)
    raise ValueError("Too many video metadata redirects")


def fetch_video_info(link: VideoLink, trusted) -> tuple[VideoInfo, bytes]:
    host = urlsplit(link.source_url).hostname or ""
    title = ""
    author = ""
    duration = 0
    thumbnail = ""
    endpoints = {
        "youtube": "https://www.youtube.com/oembed",
        "vimeo": "https://vimeo.com/api/oembed.json",
        "dailymotion": "https://www.dailymotion.com/services/oembed",
        "streamable": "https://api.streamable.com/oembed.json",
    }
    if link.provider == "direct":
        title = link.source_url.split("?", 1)[0].rsplit("/", 1)[-1]
    else:
        try:
            endpoint = endpoints[link.provider] + "?" + urlencode({
                "url": link.source_url, "format": "json",
            })
            data = json.loads(_read_response(endpoint, trusted, limit=MAX_METADATA_BYTES,
                                            accept="application/json"))
            if isinstance(data, dict):
                title = str(data.get("title") or "")[:512]
                author = str(data.get("author_name") or "")[:256]
                duration = max(0, int(float(data.get("duration") or 0)))
                thumbnail = str(data.get("thumbnail_url") or "")
                if thumbnail.startswith("//"):
                    thumbnail = "https:" + thumbnail
        except (requests.RequestException, ValueError, TypeError, KeyError, OverflowError):
            pass
        if link.provider == "youtube":
            if not thumbnail:
                thumbnail = f"https://i.ytimg.com/vi/{link.video_id}/hqdefault.jpg"
            # YouTube oEmbed omits duration. Read only bounded public metadata;
            # playback still uses the official player, never an extracted URL.
            try:
                page = _read_response(
                    f"https://www.youtube.com/watch?v={link.video_id}", trusted,
                    limit=MAX_METADATA_BYTES, accept="text/html",
                ).decode("utf-8", errors="replace")
                match = re.search(r'"lengthSeconds"\s*:\s*"?(\d+)', page)
                if match:
                    duration = int(match.group(1))
            except (requests.RequestException, ValueError):
                pass
    info = VideoInfo(link, host, title, author, duration, thumbnail)
    thumbnail_data = b""
    if thumbnail:
        try:
            candidate = _read_response(thumbnail, trusted, limit=MAX_THUMBNAIL_BYTES,
                                       accept="image/*")
            with Image.open(io.BytesIO(candidate)) as image:
                if image.width * image.height > MAX_THUMBNAIL_PIXELS:
                    raise ValueError("Video thumbnail dimensions are too large")
                image.verify()
            thumbnail_data = candidate
        except (requests.RequestException, ValueError, OSError, SyntaxError, Image.DecompressionBombError):
            pass
    return info, thumbnail_data


def video_thumbnail(frame: QImage | None, width: int, height: int) -> QImage:
    if frame is not None and not frame.isNull():
        result = frame.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation).convertToFormat(
                                  QImage.Format.Format_ARGB32_Premultiplied)
    else:
        result = QImage(min(width, round(height * 16 / 9)), height,
                        QImage.Format.Format_ARGB32_Premultiplied)
        result.fill(QColor("#30343a"))
    painter = QPainter(result)
    painter.setPen(QPen(QColor(VIDEO_EMBED_BORDER_COLOR), 1))
    painter.drawRect(result.rect().adjusted(0, 0, -1, -1))
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    center = QPointF(result.width() / 2, result.height() / 2)
    radius = min(17, result.height() / 3)
    painter.setBrush(QColor(0, 0, 0, 175))
    painter.setPen(QPen(QColor(255, 255, 255, 210), 1))
    painter.drawEllipse(center, radius, radius)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("white"))
    painter.drawPolygon(QPolygonF([
        center + QPointF(-radius * .25, -radius * .5),
        center + QPointF(-radius * .25, radius * .5),
        center + QPointF(radius * .55, 0),
    ]))
    painter.end()
    return result


def provider_player_html(info: VideoInfo, volume: int = DEFAULT_VIDEO_VOLUME) -> str:
    """Only constants and JSON-encoded IDs enter executable JavaScript."""
    link = info.link
    if link.provider == "youtube":
        script = """
let player;
// The SDK can report its default before the iframe applies our command.
// Preserve the requested slider value until it settles, or until a failed
// attempt has had two seconds to leave a persistent different value.
let pendingVolume={value:initialVolume, sentAt:null, reported:null, stableAt:null};
function setYouTubeVolume(value) {
  pendingVolume={value:value, sentAt:Date.now(), reported:null, stableAt:null};
  state.volume=value;
  player.setVolume(value);
}
function readYouTubeVolume() {
  const actual=player.getVolume();
  if (!Number.isFinite(actual)) return;
  if (!pendingVolume) { state.volume=actual; return; }
  state.volume=pendingVolume.value;
  if (pendingVolume.sentAt===null) return;
  const now=Date.now();
  if (pendingVolume.reported!==actual) {
    pendingVolume.reported=actual;
    pendingVolume.stableAt=now;
  }
  if (now-pendingVolume.sentAt<2000 || now-pendingVolume.stableAt<500) return;
  state.volume=actual;
  pendingVolume=null;
}
function onYouTubeIframeAPIReady() {
  player = new YT.Player('player', {
    width:'100%', height:'100%', videoId:VIDEO_ID,
    host:'https://www.youtube-nocookie.com',
    playerVars:{controls:0, disablekb:1, fs:0, playsinline:1, rel:0,
                origin:location.origin, start:START_SECONDS},
    events:{onReady:() => { setYouTubeVolume(initialVolume); state.ready=true; readYouTubeState(); player.playVideo(); },
      onStateChange:e => {
        state.playerState=e.data;
        if ([0,1,2,3,5].includes(e.data)) state.ready=true;
        if (e.data!==3) state.playing=e.data===1;
        state.ended=e.data===0;
        readYouTubeState();
      },
      onAutoplayBlocked:() => { state.playing=false; },
      onError:e => { state.error='YouTube playback unavailable ('+e.data+')'; }}
  });
}
window.spriteCommand = (command, value) => {
  if (!state.ready) return;
  if(command==='play') player.playVideo();
  if(command==='pause') player.pauseVideo();
  if(command==='seek') player.seekTo(value, true);
  if(command==='volume') setYouTubeVolume(value);
  if(command==='mute') value ? player.mute() : player.unMute();
};
function readYouTubeState() {
  if (!player) return state;
  try {
    const status=player.getPlayerState();
    state.playerState=status;
    if ([0,1,2,3,5].includes(status)) state.ready=true;
    if (status!==3) state.playing=status===1;
    state.ended=status===0;
    if (state.ready) {
      state.position=player.getCurrentTime();
      const duration=player.getDuration();
      if (Number.isFinite(duration) && duration>0) state.duration=duration;
      readYouTubeVolume(); state.muted=player.isMuted();
    }
  } catch (_) { /* The iframe may be between initialization messages. */ }
  return state;
}
window.spriteState=readYouTubeState;
"""
        loader = '<script async src="https://www.youtube.com/iframe_api"></script>'
        content = '<div id="player"></div>'
    elif link.provider == "vimeo":
        # Vimeo's player API supports external controls too.
        params = parse_qs(urlsplit(link.source_url).query)
        path = urlsplit(link.source_url).path.strip("/").split("/")
        video_hash = params.get("h", [""])[0]
        if len(path) == 2 and path[0] == link.video_id:
            video_hash = path[1]
        query = {"controls": "0", "autoplay": "0"}
        if re.fullmatch(r"[A-Za-z0-9]+", video_hash):
            query["h"] = video_hash
        src = f"https://player.vimeo.com/video/{link.video_id}?" + urlencode(query)
        content = f'<iframe id="player" src="{escape(src, quote=True)}" allow="autoplay; fullscreen"></iframe>'
        loader = '<script src="https://player.vimeo.com/api/player.js"></script>'
        script = """
const player = new Vimeo.Player(document.getElementById('player'));
player.ready().then(() => {
  state.ready=true;
  return player.setVolume(initialVolume/100).catch(() => {});
}).then(() => player.play()).catch(() => {state.error='Vimeo playback unavailable';});
player.on('play', () => {state.playing=true; state.ended=false;});
player.on('pause', () => {state.playing=false;});
player.on('ended', () => {state.playing=false; state.ended=true;});
player.on('timeupdate', e => {state.position=e.seconds; state.duration=e.duration;});
player.on('volumechange', e => {state.volume=e.volume*100; state.muted=e.volume===0;});
player.on('error', () => {state.error='Vimeo playback unavailable';});
window.spriteCommand = (command, value) => {
  if (!state.ready) return;
  let result;
  if(command==='play') result=player.play();
  if(command==='pause') result=player.pause();
  if(command==='seek') result=player.setCurrentTime(value);
  if(command==='volume') result=player.setVolume(value/100);
  if(command==='mute') result=player.setMuted(Boolean(value));
  if(result) result.catch(() => {});
};
"""
    else:
        src = (f"https://www.dailymotion.com/embed/video/{link.video_id}?autoplay=1"
               if link.provider == "dailymotion" else
               f"https://streamable.com/e/{link.video_id}?autoplay=1")
        content = f'<iframe id="player" src="{escape(src, quote=True)}" allow="autoplay; fullscreen" allowfullscreen></iframe>'
        loader = ""
        script = "window.spriteCommand=()=>{};"
    script = script.replace("VIDEO_ID", json.dumps(link.video_id)).replace(
        "START_SECONDS", str(link.start_seconds))
    # YouTube chooses quality automatically: setPlaybackQuality and
    # suggestedQuality are no-ops. Do not offer a nonfunctional quality control.
    pointer_style = "#player{pointer-events:none}" if link.provider == "youtube" else ""
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            "<meta name='referrer' content='strict-origin-when-cross-origin'>"
            "<style>html,body{margin:0;width:100%;height:100%;background:#000;overflow:hidden}"
            "#player{display:block;width:100%;height:100%;border:0}" + pointer_style + "</style></head><body>"
            + content + "<script>const initialVolume=" + str(max(0, min(100, int(volume))))
            + ";const state={ready:false,playing:false,ended:false,"
            "position:0,duration:" + str(max(0, int(info.duration)))
            + ",volume:initialVolume,muted:false,error:''};"
            "window.spriteState=()=>state;</script>" + loader + "<script>" + script
            + "</script></body></html>")


class DirectVideoThumbnail(QObject):
    """Decode one silent frame, then release the stream and decoder."""
    frame_ready = Signal(str, object)

    def __init__(self, url, parent):
        super().__init__(parent)
        self.url = url
        self.player = None
        self.sink = None
        self.duration = 0
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(self.stop)

    def start(self):
        from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
        self.sink = QVideoSink(self)
        self.sink.videoFrameChanged.connect(self._frame)
        self.player = QMediaPlayer(self)
        self.player.setVideoSink(self.sink)
        # No audio output: preview decoding cannot make sound.
        self.player.errorOccurred.connect(self.stop)
        self.player.setSource(QUrl(self.url))
        self.timeout.start(10000)
        self.player.play()

    def _frame(self, frame):
        if self.player is None:
            return
        image = frame.toImage()
        if image.isNull() or image.width() * image.height() > MAX_THUMBNAIL_PIXELS:
            return
        self.duration = int(self.player.duration() / 1000) if self.player else 0
        self.frame_ready.emit(self.url, image.copy())
        self.stop()

    def stop(self, *args):
        self.timeout.stop()
        if self.player is not None:
            self.player.stop()
            self.player.setVideoSink(None)
            self.player.setSource(QUrl())
            self.player.deleteLater()
            self.player = None
        if self.sink is not None:
            self.sink.deleteLater()
            self.sink = None


class VideoFrameView(QWidget):
    """An opaque raster surface that retains the last valid decoded frame."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.frame = QImage()
        self._sink = None
        self.setMinimumSize(0, 0)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)

    def videoSink(self):
        if self._sink is None:
            from PySide6.QtMultimedia import QVideoSink
            self._sink = QVideoSink(self)
            self._sink.videoFrameChanged.connect(self._receive_frame)
        return self._sink

    def _receive_frame(self, frame) -> None:
        if frame.isValid():
            self.set_frame(frame.toImage())

    def set_frame(self, image: QImage) -> None:
        if not image.isNull():
            self.frame = image.copy()
            self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        if not self.frame.isNull():
            size = self.frame.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
            target = QRect((self.width() - size.width()) // 2,
                           (self.height() - size.height()) // 2, size.width(), size.height())
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(target, self.frame)
        painter.end()


class BrowserFrameView(VideoFrameView):
    """Display browser frames without a GPU child in the chat window."""
    def __init__(self, browser, parent=None):
        super().__init__(parent)
        self.browser = browser
        self._capturing = False
        # Qt Quick changes its top-level window's compositor even after the
        # player is removed. Keep Chromium in a separate, opaque render host.
        browser.setParent(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        browser.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        browser.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        browser.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        # A fixed 1080p render surface also avoids Chromium's partially
        # repainted frames during resize. Only the raster display is resized.
        ratio = browser.devicePixelRatioF()
        browser.resize(round(1920 / ratio), round(1080 / ratio))
        browser.show()
        self._frame_timer = QTimer(self)
        self._frame_timer.setInterval(16)
        self._frame_timer.timeout.connect(self._capture_frame)
        self._frame_timer.start()
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def _snapshot(self) -> QImage:
        from PySide6.QtQuickWidgets import QQuickWidget
        widgets = self.browser.findChildren(QQuickWidget)
        return widgets[0].grabFramebuffer() if widgets else self.browser.grab().toImage()

    def _capture_frame(self) -> None:
        if (self.browser is None or self._capturing or not self.isVisible()
                or self.window().isMinimized()):
            return
        self._capturing = True
        try:
            image = self._snapshot()
            expected = self.browser.size() * self.browser.devicePixelRatioF()
            if not image.isNull() and image.size() == expected:
                self.set_frame(image)
        finally:
            self._capturing = False

    def release(self) -> None:
        self._frame_timer.stop()
        if self.browser is not None:
            self.browser.hide()
        self.browser = None
        self.frame = QImage()

    def set_playing(self, playing: bool) -> None:
        self._frame_timer.setInterval(16 if playing else 100)


class InteractiveVideoFrame(VideoFrameView):
    """Forward native provider input without hosting Chromium in chat."""
    def __init__(self, send_input):
        super().__init__()
        self._send_input = send_input
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _mouse(self, event, kind):
        if self.frame.isNull():
            return
        size = self.frame.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        left, top = (self.width() - size.width()) / 2, (self.height() - size.height()) / 2
        self._send_input({"kind": kind, "x": (event.position().x() - left) / max(1, size.width()),
                          "y": (event.position().y() - top) / max(1, size.height()),
                          "button": event.button().value, "buttons": event.buttons().value,
                          "modifiers": event.modifiers().value})
        event.accept()

    def wheelEvent(self, event):
        if self.frame.isNull():
            return
        size = self.frame.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        self._send_input({"kind": "wheel",
                          "x": (event.position().x() - (self.width() - size.width()) / 2) / max(1, size.width()),
                          "y": (event.position().y() - (self.height() - size.height()) / 2) / max(1, size.height()),
                          "pixel": [event.pixelDelta().x(), event.pixelDelta().y()],
                          "angle": [event.angleDelta().x(), event.angleDelta().y()],
                          "buttons": event.buttons().value, "modifiers": event.modifiers().value,
                          "phase": event.phase().value, "inverted": event.inverted()})
        event.accept()

    def mousePressEvent(self, event):
        self.setFocus()
        self._mouse(event, "press")

    def mouseReleaseEvent(self, event):
        self._mouse(event, "release")

    def mouseMoveEvent(self, event):
        self._mouse(event, "move")

    def mouseDoubleClickEvent(self, event):
        self._mouse(event, "double")

    def keyPressEvent(self, event):
        # Escape still belongs to SpriteLink's fullscreen window.
        if event.key() == Qt.Key.Key_Escape:
            super().keyPressEvent(event)
            return
        self._send_input({"kind": "key", "key": event.key(), "text": event.text(),
                          "modifiers": event.modifiers().value, "pressed": True})
        event.accept()

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            super().keyReleaseEvent(event)
            return
        self._send_input({"kind": "key", "key": event.key(), "text": event.text(),
                          "modifiers": event.modifiers().value, "pressed": False})
        event.accept()


class VideoCanvas(QWidget):
    """Center the video over black in every player mode."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(0, 0)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.backend = None

    def set_backend(self, backend) -> None:
        self.clear_backend()
        self.backend = backend
        backend.setParent(self)
        self._layout_backend()
        backend.show()

    def _layout_backend(self) -> None:
        if self.backend is None:
            return
        if isinstance(self.backend, VideoFrameView):
            rect = self.rect()
        else:
            size = QSize(16, 9).scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
            rect = QRect((self.width() - size.width()) // 2,
                         (self.height() - size.height()) // 2, size.width(), size.height())
        self.backend.setGeometry(rect)

    def clear_backend(self) -> None:
        if isinstance(self.backend, BrowserFrameView):
            self.backend.release()
        if self.backend is not None:
            self.backend.hide()
        self.backend = None

    def resizeEvent(self, event) -> None:
        self._layout_backend()
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        painter.end()


class VideoWindow(QDialog):
    exit_fullscreen = Signal()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape and self.isFullScreen():
            self.exit_fullscreen.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class VideoPlayer(QWidget):
    """Native theme-aware controls shared by direct, YouTube and Vimeo videos."""
    duration_available = Signal(int)
    popup_closed = Signal()
    open_in_browser = Signal()
    mode_requested = Signal(str)
    mode_changed = Signal(str)

    def __init__(self, parent=None, slider_factory=QSlider, *, use_process=True, mirror_native=False):
        super().__init__(parent)
        self._use_process = use_process
        self._mirror_native = mirror_native
        self._remote_engine = None
        self.info = None
        self.media_player = None
        self.audio = None
        self.video_widget = None
        self.web = None
        self._generation = 0
        self._poll_pending = False
        self._ready = False
        self._playing = False
        self._muted = False
        self._ended = False
        self._duration = 0.0
        self._volume_wheel_delta = 0
        self._fullscreen = None
        self._popout_mode = False
        self._embedded_parent = parent
        self._embedded_index = 0
        self._embedded_minimum_height = 0
        self._embedded_maximum_height = 16777215
        self._mini_host = None
        self._mode = "main"
        self._fullscreen_restore_mode = None
        self._tiny = False
        self._theme = "Modern"
        self._theme_transform = lambda text: text
        self.setMinimumWidth(0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.surface = QStackedWidget()
        self.surface.setObjectName("videoSurface")
        self.surface.setStyleSheet("QStackedWidget#videoSurface {background:#000; border:0;}")
        self.surface.setMinimumSize(0, 0)
        self.surface.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.status = QLabel("Loading...")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setWordWrap(True)
        self.status.setStyleSheet("background: black; color: white;")
        self.surface.addWidget(self.status)
        self.canvas = VideoCanvas()
        self.surface.addWidget(self.canvas)
        layout.addWidget(self.surface, 1)
        self.controls = QFrame()
        self.controls.setObjectName("videoControls")
        row = QHBoxLayout(self.controls)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(5)
        self.play_button = QPushButton()
        self.play_button.setAccessibleName("Play")
        self.play_button.clicked.connect(self.toggle_play)
        self.seek = slider_factory(Qt.Orientation.Horizontal)
        self.seek.setAccessibleName("Video position")
        self.seek.setRange(0, 10000)
        self.seek.setMinimumWidth(35)
        self.seek.setTracking(False)
        self.seek.valueChanged.connect(self._seek_changed)
        self.time_label = QLabel("0:00 / 0:00")
        self.mute_button = QPushButton()
        self.mute_button.setAccessibleName("Mute")
        self.mute_button.clicked.connect(self.toggle_mute)
        self.volume = slider_factory(Qt.Orientation.Horizontal)
        self.volume.setAccessibleName("Volume")
        self.volume.setRange(0, 100)
        self.volume.setSingleStep(10)
        self.volume.setPageStep(10)
        # The player lives for the app session; stop/load retain this value.
        self.volume.setValue(DEFAULT_VIDEO_VOLUME)
        self.volume.setFixedWidth(55)
        self.volume.valueChanged.connect(lambda value: self._command("volume", value))
        for widget in (self.seek, self.volume, self.mute_button):
            widget.installEventFilter(self)
        self.fullscreen_button = QPushButton()
        self.fullscreen_button.setAccessibleName("Fullscreen")
        self.fullscreen_button.clicked.connect(self.toggle_fullscreen)
        # The order remains identical for every theme and playback backend.
        for widget in (self.play_button, self.seek, self.time_label,
                       self.mute_button, self.volume, self.fullscreen_button):
            row.addWidget(widget, 1 if widget is self.seek else 0)
        for button in (self.play_button, self.mute_button, self.fullscreen_button):
            button.setMinimumWidth(0)
            button.setFixedWidth(32)
        layout.addWidget(self.controls)
        self.mode_controls = QWidget()
        modes = QHBoxLayout(self.mode_controls)
        modes.setContentsMargins(0, 0, 0, 0)
        modes.setSpacing(5)
        self.mini_spacer = QWidget()
        modes.addWidget(self.mini_spacer, 1)
        self.mini_spacer.hide()
        self.source_label = QLabel()
        self.source_label.setTextFormat(Qt.TextFormat.PlainText)
        self.source_label.setMinimumWidth(0)
        self.source_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        modes.addWidget(self.source_label, 1)
        self.source_label.installEventFilter(self)
        self.popout_button = QPushButton("Pop-out")
        self.popout_button.clicked.connect(lambda: self.mode_requested.emit(
            "main" if self._mode == "popout" else "popout"))
        self.mini_button = QPushButton("Mini player")
        self.mini_button.clicked.connect(lambda: self.mode_requested.emit(
            "main" if self._mode == "mini" else "mini"))
        self.mini_close = QPushButton("×")
        self.mini_close.setAccessibleName("Close video")
        self.mini_close.setFixedWidth(24)
        self.mini_close.clicked.connect(self.popup_closed.emit)
        self.mini_close.hide()
        self.browser_button = QPushButton("Open in Browser")
        self.browser_button.clicked.connect(self.open_in_browser.emit)
        self.dismiss_button = QPushButton("Dismiss")
        self.dismiss_button.clicked.connect(self._dismiss)
        for button in (self.popout_button, self.mini_button, self.browser_button,
                       self.dismiss_button, self.mini_close):
            modes.addWidget(button)
        layout.addWidget(self.mode_controls)
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._poll)
        self._set_enabled(False)
        self._refresh_icons()

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Resize and watched is getattr(self, "source_label", None):
            self._update_source_label()
        if event.type() == QEvent.Type.Wheel:
            if watched is self.seek:
                event.accept()
                return True
            if watched in (self.volume, self.mute_button):
                if self._ready:
                    delta = event.angleDelta().y() or event.angleDelta().x()
                    if delta:
                        self._volume_wheel_delta += delta
                        steps = int(self._volume_wheel_delta / 120)
                        self._volume_wheel_delta -= steps * 120
                    else:
                        pixels = event.pixelDelta().y() or event.pixelDelta().x()
                        steps = (1 if pixels > 0 else -1) if pixels else 0
                    self.volume.setValue(self.volume.value() + steps * 10)
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def _dismiss(self) -> None:
        if self._mode == "popout" and self._fullscreen is not None:
            self._fullscreen.close()
        else:
            self.popup_closed.emit()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_source_label()

    def _update_source_label(self) -> None:
        url = self.info.link.source_url if self.info else ""
        self.source_label.setText(self.source_label.fontMetrics().elidedText(
            url, Qt.TextElideMode.ElideMiddle, max(1, self.source_label.width())))

    def _refresh_icons(self) -> None:
        for button, kind in (
            (self.play_button, "pause" if self._playing else "play"),
            (self.mute_button, "muted" if self._muted else "volume"),
            (self.fullscreen_button, "fullscreen"),
        ):
            image = QPixmap(16, 16)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            color = button.palette().color(QPalette.ColorRole.ButtonText)
            painter.setPen(QPen(color, 1.5))
            painter.setBrush(color)
            if kind == "play":
                painter.drawPolygon(QPolygonF([QPointF(4, 2), QPointF(4, 14), QPointF(13, 8)]))
            elif kind == "pause":
                painter.drawRect(3, 2, 3, 12)
                painter.drawRect(10, 2, 3, 12)
            elif kind in ("volume", "muted"):
                painter.drawPolygon(QPolygonF([QPointF(1, 6), QPointF(4, 6), QPointF(8, 2),
                                              QPointF(8, 14), QPointF(4, 10), QPointF(1, 10)]))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                if kind == "muted":
                    painter.drawLine(11, 5, 15, 11)
                    painter.drawLine(15, 5, 11, 11)
                else:
                    painter.drawArc(5, 2, 10, 12, -65 * 16, 130 * 16)
            else:
                for x, y, dx, dy in ((2, 2, 1, 1), (14, 2, -1, 1),
                                     (2, 14, 1, -1), (14, 14, -1, -1)):
                    painter.drawLine(x, y, x + dx * 4, y)
                    painter.drawLine(x, y, x, y + dy * 4)
            painter.end()
            button.setIcon(QIcon(image))

    def set_theme(self, theme: str, transform=lambda text: text, tiny=False) -> None:
        self._theme, self._theme_transform, self._tiny = theme, transform, tiny
        mini = self._mode == "mini" and self._fullscreen_restore_mode is None
        compact = tiny or mini
        styles = {
            "Classic": "QFrame#videoControls {background:#c0c0c0; border-top:2px solid #808080;"
                       "border-left:2px solid #808080; border-bottom:2px solid #ffffff;"
                       "border-right:2px solid #ffffff; border-radius:0px;}",
            "Glassy": "QFrame#videoControls {background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                      "stop:0 rgba(255,255,255,215),stop:0.48 rgba(220,241,255,205),"
                      "stop:0.52 rgba(180,214,238,210),stop:1 rgba(157,200,228,215));"
                      "border:1px solid #648fa9; border-radius:5px;}",
            "Modern": "QFrame#videoControls {background:#f3f3f3; border:1px solid #dedede;"
                      "border-radius:7px;}",
        }
        # Buttons and sliders inherit the app's separate Classic, Glassy and
        # Modern styles, including bevel painting, color variants and density.
        stylesheet = styles.get(theme, styles["Modern"])
        if self._fullscreen is not None and self._fullscreen.property("spritelinkVideoFullscreen"):
            stylesheet += "QFrame#videoControls {border-radius:0px;}"
        if mini:
            stylesheet += "QFrame#videoControls QPushButton {padding:2px; min-height:14px;}"
        self.controls.setStyleSheet(transform(stylesheet))
        self.controls.layout().setContentsMargins(4 if compact else 6, 3 if compact else 4,
                                                  4 if compact else 6, 3 if compact else 4)
        self.controls.layout().setSpacing(3 if compact else 5)
        self.volume.setFixedWidth(40 if compact else 55)
        for button in (self.play_button, self.mute_button, self.fullscreen_button):
            button.setFixedWidth(26 if compact else 32)
        self._refresh_icons()

    def _set_enabled(self, ready: bool) -> None:
        for widget in (self.play_button, self.mute_button, self.volume):
            widget.setEnabled(ready)
        self.seek.setEnabled(ready and self._duration > 0)

    def load(self, info: VideoInfo) -> None:
        self.stop()
        self.info = info
        self.source_label.setToolTip(info.link.source_url)
        self._update_source_label()
        self._ready = self._playing = self._muted = self._ended = False
        self._duration = max(0, info.duration)
        self._set_enabled(False)
        self.seek.setValue(0)
        self._refresh_icons()
        self.time_label.setText("0:00 / " + format_video_time(info.duration))
        self.status.setText("Loading...")
        self.surface.setCurrentWidget(self.status)
        self.controls.show()
        for widget in (self.play_button, self.seek, self.time_label, self.mute_button, self.volume):
            widget.show()
        if info.link.provider == "direct":
            self._load_direct(info)
        else:
            self._load_provider(info)

    def _load_direct(self, info: VideoInfo) -> None:
        if self._use_process:
            self._load_remote(info)
            return
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        self.video_widget = VideoFrameView()
        self.canvas.set_backend(self.video_widget)
        self.surface.setCurrentWidget(self.canvas)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(self.volume.value() / 100)
        self.media_player = QMediaPlayer(self)
        self.media_player.setAudioOutput(self.audio)
        self.media_player.setVideoSink(self.video_widget.videoSink())
        self.media_player.errorOccurred.connect(self._direct_error)
        self.media_player.setSource(QUrl(info.link.source_url))
        self.media_player.play()
        self.timer.start()

    def _load_provider(self, info: VideoInfo) -> None:
        if self._use_process:
            self._load_remote(info)
            return
        # Lazy: loading chat or creating a thumbnail never starts Chromium.
        try:
            from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
            from PySide6.QtWebEngineWidgets import QWebEngineView
        except ImportError:
            self.status.setText("Video player unavailable. Open in Browser to watch.")
            return

        class PlayerPage(QWebEnginePage):
            def acceptNavigationRequest(page, url, navigation_type, is_main_frame):
                if is_main_frame:
                    return url.toString() in ("about:blank", PLAYER_BASE_URL) or url.scheme() == "data"
                return url.scheme() == "https"

            def createWindow(page, window_type):
                return None

        self.web = QWebEngineView()
        self.web.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        # An off-the-record profile isolates embedded sites from other windows.
        self.profile = QWebEngineProfile(self)
        page = PlayerPage(self.profile, self.web)
        self.web.setPage(page)
        page.setBackgroundColor(QColor("black"))
        page.settings().setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        if info.link.provider == "youtube":
            install_youtube_chrome(self.profile)
            # Expand the logical viewport so remaining fixed-size YouTube
            # chrome is four times smaller, without changing video geometry.
            self.web.setZoomFactor(YOUTUBE_ZOOM_FACTOR)
        if info.link.provider in ("youtube", "vimeo") or self._mirror_native:
            self.video_widget = BrowserFrameView(self.web)
            self.canvas.set_backend(self.video_widget)
        else:
            # These providers need genuine pointer input for their native UI.
            self.canvas.set_backend(self.web)
        self.surface.setCurrentWidget(self.canvas)
        self.web.loadFinished.connect(self._hide_youtube_chrome)
        self.web.setHtml(provider_player_html(info, self.volume.value()), QUrl(PLAYER_BASE_URL))
        if info.link.provider in ("youtube", "vimeo"):
            self.timer.start()
            generation = self._generation
            QTimer.singleShot(20000, lambda: self._check_ready(generation))
        else:
            # Providers without a reliable control API retain their own bar.
            for widget in (self.play_button, self.seek, self.time_label, self.mute_button, self.volume):
                widget.hide()

    def _load_remote(self, info: VideoInfo) -> None:
        from spritelink_video_process import VideoProcess
        if self._remote_engine is None:
            self._remote_engine = VideoProcess(self)
            self._remote_engine.frame_ready.connect(self._remote_frame)
            self._remote_engine.state_ready.connect(self._update_state)
            self._remote_engine.failed.connect(self._error)
            self.destroyed.connect(self._remote_engine.shutdown)
            QApplication.instance().aboutToQuit.connect(self._remote_engine.shutdown)
        if info.link.provider in ("dailymotion", "streamable"):
            self.video_widget = InteractiveVideoFrame(self._remote_input)
            for widget in (self.play_button, self.seek, self.time_label, self.mute_button, self.volume):
                widget.hide()
        else:
            self.video_widget = VideoFrameView()
        self.canvas.set_backend(self.video_widget)
        # Keep Loading... visible until the first complete frame arrives.
        try:
            self._remote_engine.load(info, self.volume.value(), self._generation)
        except (OSError, RuntimeError):
            self._error("Video player unavailable. Open in Browser to watch.")

    def _remote_frame(self, image) -> None:
        if self.info is not None and self.video_widget is not None:
            self.video_widget.set_frame(image)
            self.surface.setCurrentWidget(self.canvas)

    def _remote_input(self, value) -> None:
        if self.info is not None and self._remote_engine is not None:
            self._remote_engine.send({"type": "input", "value": value, "generation": self._generation})

    def _check_ready(self, generation: int) -> None:
        if generation == self._generation and self.info and not self._ready:
            # A slow control bridge is not proof that the video failed. Never
            # destroy an iframe on a readiness timer; real SDK/page errors are
            # reported separately, and polling can recover without a reload.
            self._poll()

    def _direct_error(self, error, message: str) -> None:
        self._error("Video playback unavailable. Open in Browser to watch.")

    def _error(self, text: str) -> None:
        # Prevent a late SDK ready callback from autoplaying behind an error.
        if self._remote_engine is not None:
            self._remote_engine.stop()
        if self.web is not None:
            self.web.stop()
            self.web.setUrl(QUrl("about:blank"))
        if self.media_player is not None:
            self.media_player.stop()
        self.status.setText(text)
        self.surface.setCurrentWidget(self.status)
        self._ready = self._playing = False
        self._set_enabled(False)
        self.timer.stop()

    def _poll(self) -> None:
        if self.media_player is not None:
            from PySide6.QtMultimedia import QMediaPlayer
            self._update_state({
                "ready": self.media_player.mediaStatus() not in (
                    QMediaPlayer.MediaStatus.NoMedia, QMediaPlayer.MediaStatus.InvalidMedia),
                "playing": self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState,
                "ended": self.media_player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia,
                "duration": self.media_player.duration() / 1000,
                "position": self.media_player.position() / 1000,
                "volume": self.audio.volume() * 100, "muted": self.audio.isMuted(),
            })
        elif self.web is not None and not self._poll_pending:
            self._hide_youtube_chrome()
            self._poll_pending = True
            generation = self._generation
            def result(state):
                if generation != self._generation:
                    return
                self._poll_pending = False
                self._update_state(state)
            # Explicit JSON avoids QVariant/JS-object conversion differences
            # between PySide versions. Read live SDK state even while paused.
            self.web.page().runJavaScript(
                "JSON.stringify(window.spriteState ? window.spriteState() : null)", result)

    def _hide_youtube_chrome(self) -> None:
        if self.web is None or self.info is None or self.info.link.provider != "youtube":
            return
        page = self.web.page()
        # Qt 6.8+ exposes frames directly. Refresh the actual embed as well as
        # installing the profile script, covering late iframe creation.
        if not isinstance(page, QObject) or not hasattr(page, "mainFrame"):
            return
        pending = [page.mainFrame()]
        while pending:
            frame = pending.pop()
            if not frame.isValid():
                continue
            url = frame.url()
            if (url.scheme() == "https" and url.host() in (
                    "youtube.com", "www.youtube.com", "youtube-nocookie.com", "www.youtube-nocookie.com")
                    and url.path().startswith("/embed/")):
                frame.runJavaScript(YOUTUBE_CHROME_SCRIPT, 1)
            pending.extend(frame.children())

    def _update_state(self, state) -> None:
        if isinstance(state, str):
            try:
                state = json.loads(state)
            except (ValueError, TypeError):
                return
        if not isinstance(state, dict):
            return
        if state.get("error"):
            self._error(str(state["error"]))
            return
        self._ready = (self._ready or bool(state.get("ready"))
                       or state.get("playerState") in (0, 1, 2, 3, 5))
        icons_changed = self._playing != bool(state.get("playing")) or self._muted != bool(state.get("muted"))
        self._playing = bool(state.get("playing"))
        if isinstance(self.video_widget, BrowserFrameView):
            self.video_widget.set_playing(self._playing)
        self._muted = bool(state.get("muted"))
        self._ended = bool(state.get("ended"))
        def number(key, fallback=0):
            try:
                raw = state.get(key)
                value = float(raw) if raw is not None else fallback
                return max(0, value) if math.isfinite(value) else fallback
            except (TypeError, ValueError):
                return fallback

        duration = number("duration", self._duration) or self._duration
        if duration > 0 and int(duration) != int(self._duration):
            self.duration_available.emit(int(duration))
        self._duration = duration
        position = number("position")
        self.play_button.setAccessibleName("Pause" if self._playing else "Play")
        self.mute_button.setAccessibleName("Unmute" if self._muted else "Mute")
        if icons_changed:
            self._refresh_icons()
        self.time_label.setText(format_video_time(position) + " / " + format_video_time(self._duration))
        self._set_enabled(self._ready)
        if not self.seek.isSliderDown():
            self.seek.blockSignals(True)
            self.seek.setValue(round(position / self._duration * 10000) if self._duration else 0)
            self.seek.blockSignals(False)
        if not self.volume.isSliderDown():
            self.volume.blockSignals(True)
            self.volume.setValue(round(number("volume", self.volume.value())))
            self.volume.blockSignals(False)

    def _command(self, command: str, value=0) -> None:
        if not self.info or not self._ready:
            return
        if self._remote_engine is not None and self._remote_engine.generation == self._generation:
            self._remote_engine.command(command, value)
        elif self.media_player is not None:
            if command == "play":
                self.media_player.play()
            elif command == "pause":
                self.media_player.pause()
            elif command == "seek":
                self.media_player.setPosition(round(value * 1000))
            elif command == "volume":
                self.audio.setVolume(value / 100)
            elif command == "mute":
                self.audio.setMuted(bool(value))
        elif self.web is not None:
            self.web.page().runJavaScript(
                "window.spriteCommand && window.spriteCommand(" + json.dumps(command)
                + "," + json.dumps(value) + ");")

    def toggle_play(self) -> None:
        if self._ended:
            self._command("seek", 0)
        self._command("pause" if self._playing else "play")

    def toggle_mute(self) -> None:
        self._command("mute", not self._muted)

    def _seek_changed(self, value: int) -> None:
        if self._duration:
            self._command("seek", value / 10000 * self._duration)

    def toggle_fullscreen(self) -> None:
        if self._fullscreen is not None:
            if self._mode == "popout":
                self._set_window_fullscreen(self._fullscreen, not self._fullscreen.isFullScreen())
                self._update_mode_controls()
            else:
                self._fullscreen.close()
            return
        self._remember_embedded_geometry()
        self._fullscreen_restore_mode = self._mode
        self._create_window(fullscreen=True)
        self._update_mode_controls()
        self.mode_changed.emit("fullscreen")

    def pop_out(self) -> None:
        self.set_mode("popout")

    def _remember_embedded_geometry(self) -> None:
        if self._mode == "main" and self._fullscreen is None:
            parent = self.parentWidget()
            if parent is not None and parent.layout() is not None:
                self._embedded_parent = parent
                self._embedded_index = parent.layout().indexOf(self)
                self._embedded_maximum_height = self.maximumHeight()
                self._embedded_minimum_height = self.minimumHeight()

    def _move_to_host(self, host: QWidget) -> None:
        self.surface.setMinimumHeight(0)
        self.surface.setMaximumHeight(16777215)
        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)
        self.setParent(host)
        if host is self._embedded_parent:
            host.layout().insertWidget(self._embedded_index, self, 1)
            self.setMinimumHeight(self._embedded_minimum_height)
            self.setMaximumHeight(self._embedded_maximum_height)
        else:
            host.layout().addWidget(self, 1)
        self.show()

    def _remove_window(self) -> None:
        dialog = self._fullscreen
        self._fullscreen = None
        self._fullscreen_restore_mode = None
        if dialog is not None:
            # Move the live backend out before destroying its old container.
            self.setParent(self._embedded_parent)
            dialog.blockSignals(True)
            dialog.close()
            dialog.deleteLater()

    def set_mode(self, mode: str, mini_host: QWidget | None = None) -> None:
        if mode not in ("main", "mini", "popout"):
            return
        if mini_host is not None:
            self._mini_host = mini_host
        if mode == "mini" and self._mini_host is None:
            return
        self._remember_embedded_geometry()
        self._remove_window()
        if self._mini_host is not None:
            self._mini_host.hide()
        self._mode = mode
        self._popout_mode = mode == "popout"
        if mode == "popout":
            self._create_window(fullscreen=False)
        elif mode == "mini":
            self._move_to_host(self._mini_host)
            self._mini_host.show()
            self._mini_host.raise_()
        elif self._embedded_parent is not None:
            self._move_to_host(self._embedded_parent)
        self._update_mode_controls()
        self.mode_changed.emit(mode)

    def _update_mode_controls(self) -> None:
        mini = self._mode == "mini" and self._fullscreen_restore_mode is None
        fullscreen = self._fullscreen is not None and bool(self._fullscreen.property("spritelinkVideoFullscreen"))
        margins = (8, 4, 8, 8) if self._mode == "popout" and not fullscreen else (0, 0, 0, 0)
        self.mode_controls.layout().setContentsMargins(*margins)
        self.popout_button.setText("Main window" if self._mode == "popout" else "Pop-out")
        self.mini_button.setText("Restore" if self._mode == "mini" else "Mini player")
        self.popout_button.setVisible(self._mode != "mini")
        self.mini_button.setVisible(self._mode != "popout")
        self.source_label.setVisible(not mini)
        self.mini_spacer.setVisible(mini)
        self.browser_button.setVisible(not mini)
        self.dismiss_button.setVisible(not mini)
        self.mini_close.setVisible(mini)
        supported = self.info is None or self.info.link.provider in ("direct", "youtube", "vimeo")
        self.volume.setVisible(supported and not mini)
        self.set_theme(self._theme, self._theme_transform, self._tiny)
        self._update_source_label()

    def _create_window(self, *, fullscreen: bool) -> None:
        # Parent to the chat's window, not the previous detached window.
        owner = self._embedded_parent.window() if self._embedded_parent else None
        dialog = VideoWindow(owner)
        dialog.setProperty("spritelinkVideoWindow", True)
        dialog.setObjectName("videoWindow")
        dialog.exit_fullscreen.connect(self.toggle_fullscreen)
        dialog.setWindowTitle(self.info.title if self.info and self.info.title else "SpriteLink Video")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(0, 0, 0, 0)
        self._move_to_host(dialog)
        if not fullscreen:
            dialog.setMinimumSize(480, 360)
            dialog.resize(640, 420)
            dialog.setWindowModality(Qt.WindowModality.NonModal)
            self.mode_controls.layout().setContentsMargins(8, 4, 8, 8)
        else:
            self.mode_controls.layout().setContentsMargins(0, 0, 0, 0)
        self._fullscreen = dialog
        dialog.finished.connect(lambda: self._leave_fullscreen(dialog))
        if fullscreen:
            self._set_window_fullscreen(dialog, True)
        else:
            dialog.show()

    def _set_window_fullscreen(self, dialog, fullscreen: bool) -> None:
        screen = dialog.screen()
        dialog.setProperty("spritelinkVideoFullscreen", fullscreen)
        if fullscreen:
            dialog._video_normal_geometry = dialog.geometry()
            dialog._video_normal_flags = dialog.windowFlags()
            dialog.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
                                  | Qt.WindowType.NoDropShadowWindowHint)
            dialog.setStyleSheet("QDialog#videoWindow {background:#000; border:0; border-radius:0px;}")
            dialog.setScreen(screen)
            dialog.setGeometry(screen.geometry())
            dialog.showFullScreen()
        else:
            dialog.setWindowFlags(dialog._video_normal_flags)
            dialog.setStyleSheet("")
            dialog.showNormal()
            dialog.setGeometry(dialog._video_normal_geometry)
        dialog.raise_()
        dialog.activateWindow()

    def _leave_fullscreen(self, dialog) -> None:
        if dialog is not self._fullscreen:
            return
        self._fullscreen = None
        restore_mode = self._fullscreen_restore_mode
        self._fullscreen_restore_mode = None
        was_popout = self._mode == "popout"
        self._popout_mode = False
        self.setParent(self._embedded_parent)
        dialog.deleteLater()
        if was_popout:
            # Closing a pop-out dismisses playback without reopening the chat
            # overlay. Explicit mode buttons restore the in-window player.
            self._mode = "main"
            if self._embedded_parent is not None:
                self._move_to_host(self._embedded_parent)
            self.hide()
            self._update_mode_controls()
            self.popup_closed.emit()
        else:
            if restore_mode == "main" and self._embedded_parent is not None:
                self._move_to_host(self._embedded_parent)
            self.set_mode(restore_mode or "main")

    def fit_viewport(self, maximum_height: int, width: int) -> None:
        if self._fullscreen is not None or self._mode != "main":
            return
        bar_height = self.controls.sizeHint().height() if self.controls.isVisible() else 0
        modes_height = self.mode_controls.sizeHint().height()
        height = max(1, min(maximum_height, round(width * 9 / 16) + bar_height + modes_height + 8))
        self.setMinimumHeight(height)
        self.setMaximumHeight(height)

    def mini_size(self, maximum_width: int, maximum_height: int):
        """Reserve the controls' height in addition to a 16:9 video surface."""
        self.layout().activate()
        chrome = self.controls.sizeHint().height() + self.mode_controls.sizeHint().height()
        chrome += self.layout().spacing() * 2
        width = max(1, min(maximum_width, round(max(1, maximum_height - chrome) * 16 / 9)))
        video_height = max(1, round(width * 9 / 16))
        self.surface.setFixedHeight(video_height)
        return width, video_height + chrome

    def stop(self) -> None:
        if self._remote_engine is not None:
            self._remote_engine.stop()
        self.canvas.clear_backend()
        self._remember_embedded_geometry()
        self._remove_window()
        if self._mini_host is not None:
            self._mini_host.hide()
        self._mode = "main"
        self._popout_mode = False
        if self._embedded_parent is not None and self._embedded_parent.layout() is not None:
            self._move_to_host(self._embedded_parent)
        self._generation += 1
        self._poll_pending = False
        self.timer.stop()
        self.info = None
        self._volume_wheel_delta = 0
        self.source_label.clear()
        self.source_label.setToolTip("")
        if self.media_player is not None:
            self.media_player.stop()
            self.media_player.setVideoOutput(None)
            self.media_player.setAudioOutput(None)
            self.media_player.setSource(QUrl())
            self.media_player.deleteLater()
            self.media_player = None
            self.audio.deleteLater()
            self.audio = None
        for widget in (self.video_widget, self.web):
            if widget is not None:
                if widget is self.web:
                    widget.stop()
                    widget.setUrl(QUrl("about:blank"))
                widget.deleteLater()
        self.video_widget = self.web = None
        profile = getattr(self, "profile", None)
        if profile is not None:
            # Defer profile destruction until after its view/page are deleted.
            profile.deleteLater()
            self.profile = None
        self.surface.setCurrentWidget(self.status)
        self._ready = self._playing = False
        self._set_enabled(False)
        self._update_mode_controls()
