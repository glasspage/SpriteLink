"""Browser regressions run in a fresh Qt process, without external requests."""
import os
import subprocess
import sys
import unittest


class BrowserVideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        check = subprocess.run(
            [sys.executable, "-c", "from PySide6.QtWebEngineWidgets import QWebEngineView"],
            capture_output=True, text=True,
        )
        if check.returncode:
            raise unittest.SkipTest("Qt WebEngine system libraries unavailable: " + check.stderr.strip())

    def run_browser(self, source):
        env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software"}
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            # Chromium cannot sandbox a root test process. Production settings
            # are unchanged; this applies only to the isolated test child.
            env["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
        result = subprocess.run([sys.executable, "-c", source], env=env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("WebEnginePage still not deleted", result.stderr)

    def test_dynamic_cross_origin_embed_controls_hidden_under_strict_style_csp(self):
        self.run_browser(r'''
import json
from PySide6.QtCore import QBuffer, QIODevice, QUrl
from PySide6.QtGui import QColor
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtTest import QTest
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlScheme, QWebEngineUrlSchemeHandler
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout
from spritelink_video import BrowserFrameView, install_youtube_chrome
scheme = QWebEngineUrlScheme(b'fixture')
scheme.setSyntax(QWebEngineUrlScheme.Syntax.HostAndPort)
scheme.setDefaultPort(443)
scheme.setFlags(QWebEngineUrlScheme.Flag.SecureScheme)
QWebEngineUrlScheme.registerScheme(scheme)
app = QApplication([])
fixture = """<!doctype html><meta http-equiv="Content-Security-Policy"
content="style-src 'none'; script-src 'unsafe-inline'">
<div id="movie_player"><div id="layer"><video controls></video></div>
<div id="chrome">New control markup</div><div class="ytp-error">Error</div>
<div class="ytp-ad-module">Ad</div></div>
<script>
document.body.style.cssText='margin:0;background:blue';
document.getElementById('movie_player').style.height='100vh';
document.querySelector('video').style.cssText='width:1px;height:1px';
setTimeout(()=>{
  const caption=document.createElement('div');caption.id='new-caption';
  caption.textContent='Caption';document.getElementById('movie_player').appendChild(caption);
  document.getElementById('chrome').style.setProperty('display','block','important');
},80);
</script>"""
class Handler(QWebEngineUrlSchemeHandler):
    def requestStarted(self, job):
        buffer = QBuffer(job); buffer.setData(fixture.encode()); buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        job.reply(b'text/html', buffer)
profile = QWebEngineProfile()
handler = Handler(profile); profile.installUrlSchemeHandler(b'fixture', handler)
install_youtube_chrome(profile)
web = QWebEngineView(); page = QWebEnginePage(profile, web); web.setPage(page)
mirror = BrowserFrameView(web)
root = QWidget(); root.resize(640,480); QVBoxLayout(root).addWidget(mirror); root.show()
web.setHtml("""<style>html,body,iframe{margin:0;border:0;width:100%;height:100%}</style>
<script>setTimeout(()=>{const f=document.createElement('iframe');
f.src='fixture://www.youtube-nocookie.com/embed/M7lc1UVf-VE';document.body.appendChild(f);},30)</script>""",
QUrl('https://github.com/'))
state = []
script = """(()=>{if(!document.getElementById('new-caption'))return null;
return JSON.stringify({chrome:getComputedStyle(document.getElementById('chrome')).display,
caption:getComputedStyle(document.getElementById('new-caption')).display,
video:document.querySelector('video').controls,
error:getComputedStyle(document.querySelector('.ytp-error')).display,
ad:getComputedStyle(document.querySelector('.ytp-ad-module')).display});})()"""
for _ in range(100):
    QTest.qWait(20)
    children = page.mainFrame().children()
    if children:
        children[0].runJavaScript(script, 1, lambda s:state.append(s) if s else None)
    if state: break
QTest.qWait(50)
assert state, 'embed never loaded'
assert json.loads(state[-1]) == {'chrome':'none','caption':'none','video':False,'error':'block','ad':'block'}, state
assert root.findChildren(QQuickWidget) == []
assert web.findChildren(QQuickWidget)
for _ in range(100):
    QTest.qWait(20); mirror._capture_frame()
    if not mirror.frame.isNull() and mirror.frame.pixelColor(mirror.frame.width()//2,mirror.frame.height()//2) == QColor('blue'):
        break
assert mirror.frame.pixelColor(mirror.frame.width()//2,mirror.frame.height()//2) == QColor('blue')
render_size = web.size()
for size in ((480,260),(900,650),(400,600)):
    old = mirror.frame.copy(); root.resize(*size); root.layout().activate()
    assert mirror.frame == old, 'frame changed before the render host resized'
    assert web.size() == render_size
    QTest.qWait(160); mirror._capture_frame()
    assert mirror.frame.pixelColor(mirror.frame.width()//2, mirror.frame.height()//2) == QColor('blue'), mirror.frame.pixelColor(mirror.frame.width()//2,mirror.frame.height()//2).name()
    assert root.findChildren(QQuickWidget) == []
mirror.release(); web.deleteLater(); root.close(); root.deleteLater(); QTest.qWait(50)
profile.deleteLater(); QTest.qWait(50)
''')

    def test_real_browser_mode_changes_and_spritelink_controls(self):
        self.run_browser(r'''
from unittest import mock
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtQuickWidgets import QQuickWidget
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout
import spritelink_video as V
app = QApplication([])
root = QWidget(); root.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
root.resize(640,480); QVBoxLayout(root)
player = V.VideoPlayer(root); root.layout().addWidget(player); root.show()
root_id = int(root.winId())
html = """<html><body style='margin:0;background:blue'>
<script>window.spriteState=()=>({ready:true,playing:false,duration:120});
window.spriteCommand=(command)=>{if(command==='play')document.body.style.background='lime';};
</script></body></html>"""
with mock.patch.object(V, 'provider_player_html', return_value=html):
    player.load(V.VideoInfo(V.VideoLink('youtube','M7lc1UVf-VE','https://youtu.be/M7lc1UVf-VE'),'youtube.com'))
QTest.qWait(600)
web, mirror = player.web, player.video_widget
assert mirror.frame.pixelColor(mirror.frame.width()//2,mirror.frame.height()//2) == QColor('blue'), mirror.frame.pixelColor(mirror.frame.width()//2,mirror.frame.height()//2).name()
assert player._ready
player.play_button.click()
QTest.qWait(200)
assert mirror.frame.pixelColor(mirror.frame.width()//2,mirror.frame.height()//2) == QColor('lime')
mini = QWidget(root); mini.resize(280,220); QVBoxLayout(mini); mini.show()
host_id = int(web.winId())
for mode in ('mini','popout','main'):
    player.set_mode(mode, mini); QTest.qWait(160)
    assert player.web is web
    assert int(web.winId()) == host_id
    assert int(root.winId()) == root_id
    assert not root.isAncestorOf(web)
    assert player.findChildren(QQuickWidget) == []
    assert mirror.frame.pixelColor(mirror.frame.width()//2,mirror.frame.height()//2) == QColor('lime')
player.stop(); assert not mirror._frame_timer.isActive()
root.close(); root.deleteLater(); QTest.qWait(100)
''')

    def test_youtube_zoom_shrinks_native_css_elements_and_keeps_full_video_viewport(self):
        self.run_browser(r'''
import json
from unittest import mock
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout
import spritelink_video as V
app = QApplication([])
root = QWidget(); root.resize(640,480); QVBoxLayout(root)
player = V.VideoPlayer(root); root.layout().addWidget(player); root.show()
html = """<style>html,body{margin:0;width:100%;height:100%;background:blue}
#logo{position:absolute;left:0;top:0;width:40px;height:40px;background:red}</style>
<div id='logo'></div><script>window.spriteState=()=>({ready:true,playing:false,duration:120});</script>"""
for provider, factor, extent in [('youtube',0.25,10),('vimeo',1.0,40)]:
    info=V.VideoInfo(V.VideoLink(provider,'M7lc1UVf-VE','https://example.org'),provider)
    with mock.patch.object(V, 'provider_player_html', return_value=html) as make_html:
        player.load(info)
        make_html.assert_called_once_with(info, player.volume.value())
    web, mirror = player.web, player.video_widget
    assert web.zoomFactor() == factor
    for _ in range(100):
        QTest.qWait(20); mirror._capture_frame()
        if not mirror.frame.isNull() and mirror.frame.pixelColor(1,1) == QColor('red'): break
    frame = mirror.frame
    assert frame.size().width() == 1920 and frame.size().height() == 1080
    assert frame.pixelColor(extent-1,extent-1) == QColor('red')
    assert frame.pixelColor(extent+2,extent+2) == QColor('blue')
    assert frame.pixelColor(1918,1078) == QColor('blue')
    state=[]
    web.page().runJavaScript('JSON.stringify({width:innerWidth,height:innerHeight})',lambda s:state.append(s))
    QTest.qWait(50)
    viewport=json.loads(state[-1]); ratio=web.devicePixelRatioF()
    assert abs(viewport['width']*ratio*factor-1920) < 2, viewport
    assert abs(viewport['height']*ratio*factor-1080) < 2, viewport
    if provider == 'youtube':
        # An initialized renderer rejects values below the documented minimum.
        for unsupported in (0.1,0.2,0.249):
            web.setZoomFactor(unsupported); QTest.qWait(50)
            assert web.zoomFactor() == 0.25
            mirror._capture_frame()
            assert mirror.frame.pixelColor(9,9) == QColor('red')
            assert mirror.frame.pixelColor(12,12) == QColor('blue')
        player.volume.setValue(70)
    player.stop(); QTest.qWait(50)
assert player.volume.value() == 70
root.close(); root.deleteLater(); QTest.qWait(50)
''')

    def test_below_minimum_zoom_on_a_fresh_page_is_rejected_after_renderer_initializes(self):
        self.run_browser(r'''
import json
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout
from spritelink_video import BrowserFrameView
app=QApplication([])
root=QWidget();root.resize(640,480);QVBoxLayout(root);root.show()
html="""<style>html,body{margin:0;width:100%;height:100%;background:blue}
#logo{width:100px;height:100px;background:red}</style><div id='logo'></div>"""
for requested in (0.1,0.2,0.249):
    web=QWebEngineView();web.setZoomFactor(requested)
    mirror=BrowserFrameView(web);root.layout().addWidget(mirror);web.setHtml(html)
    for _ in range(100):
        QTest.qWait(20);mirror._capture_frame()
        if not mirror.frame.isNull() and mirror.frame.pixelColor(1,1)==QColor('red'):break
    assert web.zoomFactor()==1.0, (requested,web.zoomFactor())
    assert mirror.frame.pixelColor(99,99)==QColor('red')
    assert mirror.frame.pixelColor(102,102)==QColor('blue')
    viewport=[]
    web.page().runJavaScript('JSON.stringify({width:innerWidth,height:innerHeight})',lambda s:viewport.append(s))
    QTest.qWait(50);size=json.loads(viewport[-1]);ratio=web.devicePixelRatioF()
    assert abs(size['width']*ratio-1920)<2, size
    assert abs(size['height']*ratio-1080)<2, size
    mirror.release();root.layout().removeWidget(mirror)
    web.deleteLater();mirror.deleteLater();QTest.qWait(50)
root.close();root.deleteLater();QTest.qWait(50)
''')


if __name__ == "__main__":
    unittest.main()
