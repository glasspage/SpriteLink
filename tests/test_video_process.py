import os
from pathlib import Path
import subprocess
import shutil
import sys
import unittest


class VideoProcessTests(unittest.TestCase):
    def run_qt(self, source, timeout=30):
        env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software"}
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            env["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
        result = subprocess.run([sys.executable, "-c", source], env=env,
                                text=True, capture_output=True, timeout=timeout)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("resource_tracker:", result.stderr)
        self.assertNotIn("QProcess: Destroyed while process", result.stderr)

    def test_cold_browser_startup_does_not_block_chat_and_reuses_helper_across_videos(self):
        self.run_qt(r'''
import os,sys,time
from multiprocessing.shared_memory import SharedMemory
from unittest import mock
from PySide6.QtCore import QTimer,QProcess
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout,QPushButton
from spritelink_video import VideoPlayer,VideoInfo,VideoLink
import spritelink_video_process as P
app=QApplication([])
root=QWidget();root.resize(640,480);QVBoxLayout(root)
player=VideoPlayer(root);root.layout().addWidget(player);button=QPushButton('Chat',root);root.layout().addWidget(button);root.show()
heartbeat=[];timer=QTimer();timer.timeout.connect(lambda:heartbeat.append(time.monotonic()));timer.start(10)
clicks=[];button.clicked.connect(lambda:clicks.append(True))
html="""<body style='margin:0;background:blue'><script>
const state={ready:true,playing:false,duration:120,position:0,volume:INITIAL_VOLUME,muted:false};
window.spriteState=()=>state;
window.spriteCommand=(command,value)=>{
if(command==='play'){state.playing=true;document.body.style.background='lime';}
if(command==='pause'){state.playing=false;document.body.style.background='blue';}
if(command==='volume')state.volume=value;
if(command==='seek')state.position=value;
if(command==='mute')state.muted=value;
};</script>"""
# Simulate slow Windows backend initialization.
code="import sys,time,runpy;time.sleep(0.8)\nimport spritelink_video as V\nV.provider_player_html=lambda info,volume: "+repr(html)+".replace('INITIAL_VOLUME',str(volume))\nsys.argv=['SpriteLink.pyw','--spritelink-video-worker',sys.argv[1]]\nrunpy.run_path('SpriteLink.pyw',run_name='__main__')"
info=VideoInfo(VideoLink('youtube','M7lc1UVf-VE','https://youtu.be/M7lc1UVf-VE'),'youtube.com')
with mock.patch.object(P,'worker_arguments',side_effect=lambda name:['-c',code,name]):
    start=time.monotonic();player.load(info)
assert time.monotonic()-start<0.25
assert player.surface.currentWidget() is player.status
QTest.qWait(300);button.click()
assert len(heartbeat)>=15 and clicks==[True], heartbeat
assert not player._ready
assert not any(name.startswith(('PySide6.QtWebEngine','PySide6.QtMultimedia','PySide6.QtQuick')) for name in sys.modules)
def wait_for(check):
    for _ in range(300):
        QTest.qWait(20)
        if check():return
    raise AssertionError('helper did not update player: '+player.status.text())
def color(value):
    image=player.video_widget.frame
    return not image.isNull() and image.pixelColor(image.width()//2,image.height()//2)==QColor(value)
wait_for(lambda:player._ready and color('blue'))
engine=player._remote_engine;pid=engine.process.processId();name=engine.memory.name
assert pid and pid!=os.getpid()
player.toggle_play();wait_for(lambda:player._playing and color('lime'))
player.volume.setValue(70);player.seek.setValue(5000);player.toggle_mute()
wait_for(lambda:player._muted and player.time_label.text().startswith('1:00'))
assert player.volume.value()==70
mini=QWidget(root);QVBoxLayout(mini);mini.resize(300,230);mini.show()
for mode in ('mini','popout','main'):
    old=player.video_widget.frame.copy();player.set_mode(mode,mini);QTest.qWait(50)
    assert player._remote_engine is engine and engine.process.processId()==pid
    assert player.video_widget.frame==old and color('lime')
player.stop();assert engine.generation is None
player.load(VideoInfo(VideoLink('vimeo','123456','https://vimeo.com/123456'),'vimeo.com'))
wait_for(lambda:player._ready and color('blue'))
assert engine.process.processId()==pid and player.volume.value()==70
assert not any(name.startswith(('PySide6.QtWebEngine','PySide6.QtMultimedia','PySide6.QtQuick')) for name in sys.modules)
player.stop();engine.shutdown();wait_for(lambda:engine.process.state()==QProcess.ProcessState.NotRunning)
assert engine.memory is None
try: leaked=SharedMemory(name=name)
except FileNotFoundError:pass
else:leaked.close();raise AssertionError('frame mapping was not released')
timer.stop();root.close();root.deleteLater();QTest.qWait(50)
''')

    def test_dismiss_and_replacement_during_slow_startup_ignore_old_frames_and_state(self):
        self.run_qt(r'''
import time
from unittest import mock
from PySide6.QtCore import QProcess
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout
from spritelink_video import VideoPlayer,VideoInfo,VideoLink
import spritelink_video_process as P
app=QApplication([]);root=QWidget();root.resize(640,480);QVBoxLayout(root);root.show()
player=VideoPlayer(root);root.layout().addWidget(player)
code="""import sys,time;time.sleep(0.5)
import spritelink_video as V
V.provider_player_html=lambda info,volume: '<body style="background:'+('red' if info.link.provider=='youtube' else 'blue')+'"><script>window.spriteState=()=>({ready:true,duration:120,volume:'+str(volume)+'});</script>'
from spritelink_video_worker import main
main(sys.argv[1])
"""
with mock.patch.object(P,'worker_arguments',side_effect=lambda name:['-c',code,name]):
    player.load(VideoInfo(VideoLink('youtube','M7lc1UVf-VE',''),'youtube.com'))
engine=player._remote_engine;old_generation=player._generation
start=time.monotonic();player.stop();assert time.monotonic()-start<0.1
assert player.info is None and engine.generation is None
player.volume.setValue(0)
player.load(VideoInfo(VideoLink('vimeo','123456',''),'vimeo.com'))
assert player._generation!=old_generation
seen=[];engine.frame_ready.connect(lambda image:seen.append(image.pixelColor(900,500).name()))
for _ in range(300):
    QTest.qWait(20)
    if player._ready and '#0000ff' in seen:break
assert player._ready and '#0000ff' in seen and '#ff0000' not in seen, seen
assert player.volume.value()==0
player.stop();engine.shutdown()
for _ in range(100):
    QTest.qWait(10)
    if engine.process.state()==QProcess.ProcessState.NotRunning:break
root.close();root.deleteLater();QTest.qWait(50)
''')

    def test_worker_launch_arguments_for_source_and_frozen_package(self):
        from unittest import mock
        import spritelink_video_process as process
        root = Path(__file__).resolve().parents[1]
        arguments = process.worker_arguments("frame-mapping")
        self.assertTrue(Path(arguments[0]).is_file())
        self.assertEqual(arguments[1:], ["--spritelink-video-worker", "frame-mapping"])
        with mock.patch.object(process.sys, "frozen", True, create=True):
            self.assertEqual(process.worker_arguments("frame-mapping"),
                             ["--spritelink-video-worker", "frame-mapping"])
        self.assertIn('"spritelink_video_worker"', (root / "SpriteLink.spec").read_text())

    def test_windowed_helper_uses_native_pipes_when_python_standard_streams_are_none(self):
        import ctypes
        from tempfile import TemporaryDirectory
        from types import SimpleNamespace
        from unittest import mock
        from spritelink_video_worker import _pipe
        with TemporaryDirectory() as folder:
            path = Path(folder) / "pipe"
            path.write_bytes(b"input")
            for name, handle, mode in (("stdin", -10, os.O_RDONLY), ("stdout", -11, os.O_WRONLY)):
                descriptor = os.open(path, mode)
                get_handle = mock.Mock(return_value=1234)
                open_handle = mock.Mock(return_value=descriptor)
                native = SimpleNamespace(kernel32=SimpleNamespace(GetStdHandle=get_handle))
                with mock.patch.object(sys, name, None), \
                        mock.patch.object(ctypes, "windll", native, create=True), \
                        mock.patch.object(os, "O_BINARY", 0, create=True), \
                        mock.patch.dict(sys.modules, {"msvcrt": SimpleNamespace(open_osfhandle=open_handle)}):
                    stream = _pipe(name, handle, mode)
                    if name == "stdin":
                        self.assertEqual(stream.read(), b"input")
                    else:
                        stream.write(b"reply")
                    stream.close()
                get_handle.assert_called_once_with(handle)
                open_handle.assert_called_once_with(1234, mode)
            self.assertEqual(path.read_bytes(), b"reply")

    @unittest.skipUnless(shutil.which('ffmpeg'), "ffmpeg is needed to create a direct video fixture")
    def test_direct_video_decodes_and_plays_in_helper_without_loading_multimedia_in_chat(self):
        self.run_qt(r'''
import sys,subprocess,tempfile,time
from pathlib import Path
from PySide6.QtCore import QProcess,QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout
from spritelink_video import VideoPlayer,VideoInfo,VideoLink
app=QApplication([]);root=QWidget();root.resize(640,480);QVBoxLayout(root);root.show()
player=VideoPlayer(root);root.layout().addWidget(player)
with tempfile.TemporaryDirectory() as folder:
    video=Path(folder)/'video.avi'
    subprocess.run(['ffmpeg','-loglevel','error','-f','lavfi','-i','color=c=blue:s=160x90:r=10',
                    '-t','3','-c:v','mpeg4',str(video)],check=True)
    start=time.monotonic()
    player.load(VideoInfo(VideoLink('direct','',QUrl.fromLocalFile(str(video)).toString()),'localhost'))
    assert time.monotonic()-start<0.25
    for _ in range(300):
        QTest.qWait(20)
        if player._ready and not player.video_widget.frame.isNull():break
    assert player._ready and not player.video_widget.frame.isNull(), player.status.text()
    assert player.volume.value()==20 and player._duration>=2
    assert not any(name.startswith('PySide6.QtMultimedia') for name in sys.modules)
    player.toggle_play();QTest.qWait(350);assert not player._playing
    player.volume.setValue(0);QTest.qWait(350);assert player.volume.value()==0
    engine=player._remote_engine;player.stop();engine.shutdown()
    for _ in range(100):
        QTest.qWait(10)
        if engine.process.state()==QProcess.ProcessState.NotRunning:break
root.close();root.deleteLater();QTest.qWait(50)
''')

    def test_native_provider_controls_receive_mouse_input_in_helper(self):
        self.run_qt(r'''
import sys
from unittest import mock
from PySide6.QtCore import QProcess,Qt,QPoint,QPointF
from PySide6.QtGui import QColor,QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout
from spritelink_video import VideoPlayer,VideoInfo,VideoLink,InteractiveVideoFrame
import spritelink_video_process as P
app=QApplication([]);root=QWidget();root.resize(640,480);QVBoxLayout(root);root.show()
player=VideoPlayer(root);root.layout().addWidget(player)
html="""<body style='margin:0;background:blue' onclick='document.body.style.background="lime"'
onkeydown='document.body.style.background="red"' onkeyup='document.body.style.background="yellow"'
onwheel='document.body.style.background="cyan"'>
<div style='width:100vw;height:100vh'>Provider button</div></body>"""
code="import sys\nimport spritelink_video as V\nV.provider_player_html=lambda *args:"+repr(html)+"\nfrom spritelink_video_worker import main\nmain(sys.argv[1])"
with mock.patch.object(P,'worker_arguments',side_effect=lambda name:['-c',code,name]):
    player.load(VideoInfo(VideoLink('streamable','abc123',''),'streamable.com'))
view=player.video_widget
def color(value):
    image=view.frame
    return not image.isNull() and image.pixelColor(900,500)==QColor(value)
for _ in range(300):
    QTest.qWait(20)
    if color('blue'):break
assert color('blue') and isinstance(view,InteractiveVideoFrame)
assert player.volume.isHidden() and player.play_button.isHidden()
QTest.mouseClick(view,Qt.MouseButton.LeftButton,pos=view.rect().center())
for _ in range(100):
    QTest.qWait(20)
    if color('lime'):break
assert color('lime'), view.frame.pixelColor(900,500).name()
QTest.keyClick(view,Qt.Key.Key_K)
for _ in range(100):
    QTest.qWait(20)
    if color('yellow'):break
assert color('yellow'), view.frame.pixelColor(900,500).name()
point=QPointF(view.rect().center())
event=QWheelEvent(point,point,QPoint(),QPoint(0,120),Qt.MouseButton.NoButton,
                  Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.NoScrollPhase,False)
app.sendEvent(view,event)
for _ in range(100):
    QTest.qWait(20)
    if color('cyan'):break
assert color('cyan'), view.frame.pixelColor(900,500).name()
engine=player._remote_engine;player.stop();engine.shutdown()
for _ in range(100):
    QTest.qWait(10)
    if engine.process.state()==QProcess.ProcessState.NotRunning:break
root.close();root.deleteLater();QTest.qWait(50)
''')

    def test_failed_helper_releases_memory_and_can_restart(self):
        self.run_qt(r'''
from unittest import mock
from PySide6.QtCore import QProcess
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout
from spritelink_video import VideoPlayer,VideoInfo,VideoLink
import spritelink_video_process as P
app=QApplication([]);root=QWidget();QVBoxLayout(root);root.show()
player=VideoPlayer(root);root.layout().addWidget(player)
info=VideoInfo(VideoLink('youtube','M7lc1UVf-VE',''),'youtube.com')
with mock.patch.object(P,'worker_arguments',side_effect=lambda name:['-c','raise SystemExit(3)',name]):
    player.load(info)
engine=player._remote_engine
for _ in range(100):
    QTest.qWait(20)
    if engine.memory is None:break
assert engine.memory is None and engine.process.state()==QProcess.ProcessState.NotRunning
assert player.status.text()=='Video player unavailable. Open in Browser to watch.'
assert not player._ready
code="""import sys
import spritelink_video as V
V.provider_player_html=lambda *args:'<body style="background:blue"><script>window.spriteState=()=>({ready:true});</script>'
from spritelink_video_worker import main
main(sys.argv[1])
"""
with mock.patch.object(P,'worker_arguments',side_effect=lambda name:['-c',code,name]):
    player.load(info)
for _ in range(300):
    QTest.qWait(20)
    if player._ready:break
assert player._ready and engine.memory is not None, player.status.text()
player.stop();engine.shutdown()
for _ in range(100):
    QTest.qWait(10)
    if engine.process.state()==QProcess.ProcessState.NotRunning:break
root.close();root.deleteLater();QTest.qWait(50)
''')


if __name__ == "__main__":
    unittest.main()
