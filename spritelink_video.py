"""Video link metadata and click-to-play players for SpriteLink.

Provider HTML is never executed. Only canonical player URLs are embedded, and
the caller's trusted-link check also gates metadata and thumbnail requests.
"""

from dataclasses import dataclass
from html import escape
import io
import json
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from PIL import Image
import requests
from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPalette, QPen, QPixmap, QPolygonF
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QSlider,
    QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)


VIDEO_EXTENSIONS = (".mp4", ".webm", ".mov", ".m4v")
PLAYER_BASE_URL = "https://github.com/glasspage/SpriteLink/"
MAX_METADATA_BYTES = 1024 * 1024
MAX_THUMBNAIL_BYTES = 8 * 1024 * 1024
MAX_THUMBNAIL_PIXELS = 16 * 1024 * 1024


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
    painter.setPen(QPen(QColor("black"), 1))
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


def provider_player_html(info: VideoInfo) -> str:
    """Only constants and JSON-encoded IDs enter executable JavaScript."""
    link = info.link
    if link.provider == "youtube":
        script = """
let player;
function onYouTubeIframeAPIReady() {
  player = new YT.Player('player', {
    width:'100%', height:'100%', videoId:VIDEO_ID,
    host:'https://www.youtube-nocookie.com',
    playerVars:{controls:0, disablekb:1, fs:0, playsinline:1, rel:0,
                origin:location.origin, start:START_SECONDS},
    events:{onReady:() => { state.ready=true; player.playVideo(); },
      onStateChange:e => { state.playing=e.data===1; state.ended=e.data===0; },
      onAutoplayBlocked:() => { state.playing=false; },
      onError:e => { state.error='YouTube playback unavailable ('+e.data+')'; }}
  });
}
window.spriteCommand = (command, value) => {
  if (!state.ready) return;
  if(command==='play') player.playVideo();
  if(command==='pause') player.pauseVideo();
  if(command==='seek') player.seekTo(value, true);
  if(command==='volume') player.setVolume(value);
  if(command==='mute') value ? player.mute() : player.unMute();
};
setInterval(() => {
  if (!state.ready) return;
  state.position=player.getCurrentTime(); state.duration=player.getDuration();
  state.volume=player.getVolume(); state.muted=player.isMuted();
}, 250);
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
        query = {"controls": "0", "autoplay": "1"}
        if re.fullmatch(r"[A-Za-z0-9]+", video_hash):
            query["h"] = video_hash
        src = f"https://player.vimeo.com/video/{link.video_id}?" + urlencode(query)
        content = f'<iframe id="player" src="{escape(src, quote=True)}" allow="autoplay; fullscreen"></iframe>'
        loader = '<script src="https://player.vimeo.com/api/player.js"></script>'
        script = """
const player = new Vimeo.Player(document.getElementById('player'));
player.ready().then(() => { state.ready=true; }).catch(() => {state.error='Vimeo playback unavailable';});
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
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            "<meta name='referrer' content='strict-origin-when-cross-origin'>"
            "<style>html,body{margin:0;width:100%;height:100%;background:#000;overflow:hidden}"
            "#player{display:block;width:100%;height:100%;border:0}</style></head><body>"
            + content + "<script>const state={ready:false,playing:false,ended:false,"
            "position:0,duration:0,volume:100,muted:false,error:''};"
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


class VideoPlayer(QWidget):
    """Native theme-aware controls shared by direct, YouTube and Vimeo videos."""
    duration_available = Signal(int)
    popup_closed = Signal()
    open_in_browser = Signal()

    def __init__(self, parent=None, slider_factory=QSlider):
        super().__init__(parent)
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
        self._fullscreen = None
        self._popout_mode = False
        self._embedded_parent = parent
        self.setMinimumWidth(0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.surface = QStackedWidget()
        self.surface.setMinimumSize(0, 0)
        self.surface.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.status = QLabel("Loading...")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setWordWrap(True)
        self.status.setStyleSheet("background: black; color: white;")
        self.surface.addWidget(self.status)
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
        self.volume.setValue(100)
        self.volume.setFixedWidth(55)
        self.volume.valueChanged.connect(lambda value: self._command("volume", value))
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
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._poll)
        self._set_enabled(False)
        self._refresh_icons()

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
        self.controls.setStyleSheet(transform(styles.get(theme, styles["Modern"])))
        self.controls.layout().setContentsMargins(4 if tiny else 6, 3 if tiny else 4,
                                                  4 if tiny else 6, 3 if tiny else 4)
        self.controls.layout().setSpacing(3 if tiny else 5)
        self.volume.setFixedWidth(40 if tiny else 55)
        for button in (self.play_button, self.mute_button, self.fullscreen_button):
            button.setFixedWidth(26 if tiny else 32)
        self._refresh_icons()

    def _set_enabled(self, ready: bool) -> None:
        for widget in (self.play_button, self.mute_button, self.volume):
            widget.setEnabled(ready)
        self.seek.setEnabled(ready and self._duration > 0)

    def load(self, info: VideoInfo) -> None:
        self.stop()
        self.info = info
        self._ready = self._playing = self._muted = self._ended = False
        self._duration = 0
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
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget
        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumSize(0, 0)
        self.video_widget.setStyleSheet("background: black;")
        self.surface.addWidget(self.video_widget)
        self.surface.setCurrentWidget(self.video_widget)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(self.volume.value() / 100)
        self.media_player = QMediaPlayer(self)
        self.media_player.setAudioOutput(self.audio)
        self.media_player.setVideoOutput(self.video_widget)
        self.media_player.errorOccurred.connect(self._direct_error)
        self.media_player.setSource(QUrl(info.link.source_url))
        self.media_player.play()
        self.timer.start()

    def _load_provider(self, info: VideoInfo) -> None:
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
        self.profile = QWebEngineProfile(self.web)
        page = PlayerPage(self.profile, self.web)
        self.web.setPage(page)
        page.setBackgroundColor(QColor("black"))
        page.settings().setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        self.surface.addWidget(self.web)
        self.surface.setCurrentWidget(self.web)
        self.web.setHtml(provider_player_html(info), QUrl(PLAYER_BASE_URL))
        if info.link.provider in ("youtube", "vimeo"):
            self.timer.start()
            generation = self._generation
            QTimer.singleShot(20000, lambda: self._check_ready(generation))
        else:
            # Providers without a reliable control API retain their own bar.
            for widget in (self.play_button, self.seek, self.time_label, self.mute_button, self.volume):
                widget.hide()

    def _check_ready(self, generation: int) -> None:
        if generation == self._generation and self.info and not self._ready:
            self._error("Video player did not load. Open in Browser to watch.")

    def _direct_error(self, error, message: str) -> None:
        self._error("Video playback unavailable. Open in Browser to watch.")

    def _error(self, text: str) -> None:
        # Prevent a late SDK ready callback from autoplaying behind an error.
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
            self._poll_pending = True
            generation = self._generation
            def result(state):
                if generation != self._generation:
                    return
                self._poll_pending = False
                self._update_state(state)
            self.web.page().runJavaScript("window.spriteState ? window.spriteState() : null", result)

    def _update_state(self, state) -> None:
        if not isinstance(state, dict):
            return
        if state.get("error"):
            self._error(str(state["error"]))
            return
        self._ready = bool(state.get("ready"))
        icons_changed = self._playing != bool(state.get("playing")) or self._muted != bool(state.get("muted"))
        self._playing = bool(state.get("playing"))
        self._muted = bool(state.get("muted"))
        self._ended = bool(state.get("ended"))
        duration = max(0, float(state.get("duration", 0)))
        if duration > 0 and int(duration) != int(self._duration):
            self.duration_available.emit(int(duration))
        self._duration = duration
        position = max(0, float(state.get("position", 0)))
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
            self.volume.setValue(round(float(state.get("volume", 100))))
            self.volume.blockSignals(False)

    def _command(self, command: str, value=0) -> None:
        if not self.info or not self._ready:
            return
        if self.media_player is not None:
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
            if self._popout_mode:
                if self._fullscreen.isFullScreen():
                    self._fullscreen.showNormal()
                else:
                    self._fullscreen.showFullScreen()
            else:
                self._fullscreen.close()
            return
        self._enter_window(fullscreen=True)

    def pop_out(self) -> None:
        if self._fullscreen is None:
            self._enter_window(fullscreen=False)

    def _enter_window(self, *, fullscreen: bool) -> None:
        parent = self.parentWidget()
        self._embedded_parent = parent
        self._embedded_layout = parent.layout()
        self._embedded_index = self._embedded_layout.indexOf(self)
        self._embedded_maximum_height = self.maximumHeight()
        self._embedded_minimum_height = self.minimumHeight()
        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)
        dialog = QDialog(self.window())
        dialog.setWindowTitle(self.info.title if self.info and self.info.title else "SpriteLink Video")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self, 1)
        self._popout_mode = not fullscreen
        if not fullscreen:
            # Tiny windows cannot fit YouTube's minimum 200x200 player.
            # A resizable pop-up retains usable playback without resizing chat.
            dialog.setMinimumSize(480, 360)
            dialog.resize(640, 420)
            dialog.setWindowModality(Qt.WindowModality.WindowModal)
            footer = QHBoxLayout()
            footer.setContentsMargins(8, 4, 8, 8)
            footer.addStretch(1)
            open_button = QPushButton("Open in Browser")
            open_button.clicked.connect(self.open_in_browser.emit)
            footer.addWidget(open_button)
            dismiss = QPushButton("Dismiss")
            dismiss.clicked.connect(dialog.close)
            footer.addWidget(dismiss)
            layout.addLayout(footer)
        self._fullscreen = dialog
        dialog.finished.connect(self._leave_fullscreen)
        dialog.showFullScreen() if fullscreen else dialog.show()

    def _leave_fullscreen(self, *args) -> None:
        dialog = self._fullscreen
        self._fullscreen = None
        was_popout = self._popout_mode
        self._popout_mode = False
        if self._embedded_parent is not None:
            self.setParent(self._embedded_parent)
            self._embedded_layout.insertWidget(self._embedded_index, self, 1)
            self.setMinimumHeight(self._embedded_minimum_height)
            self.setMaximumHeight(self._embedded_maximum_height)
            self.show()
        if dialog is not None:
            dialog.deleteLater()
        if was_popout:
            self.popup_closed.emit()

    def fit_viewport(self, maximum_height: int, width: int) -> None:
        if self._fullscreen is not None:
            return
        bar_height = self.controls.sizeHint().height() if self.controls.isVisible() else 0
        height = max(1, min(maximum_height, round(width * 9 / 16) + bar_height + 4))
        self.setMinimumHeight(height)
        self.setMaximumHeight(height)

    def stop(self) -> None:
        if self._fullscreen is not None:
            self._fullscreen.close()
        self._generation += 1
        self._poll_pending = False
        self.timer.stop()
        self.info = None
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
                self.surface.removeWidget(widget)
                widget.deleteLater()
        self.video_widget = self.web = None
        profile = getattr(self, "profile", None)
        if profile is not None:
            # Defer profile destruction until after its view/page are deleted.
            profile.deleteLater()
            self.profile = None
        self.surface.setCurrentWidget(self.status)
