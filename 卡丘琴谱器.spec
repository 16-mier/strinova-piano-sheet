# -*- mode: python ; coding: utf-8 -*-
"""卡丘琴谱器 打包配置（onedir）。

★ 运行时依赖只有两样：PyQt6 + numpy ★
  听音 / 跟随 / 播音 / 透视贴合整套删掉之后，torch、opencv、scipy、
  librosa、soundfile、soundcard、PIL 全都不在主程序里了 ——
  它们只出现在 `tools/` 的分析脚本里，那些不参与打包。
  所以这一份的瘦身空间很大，下面那些 excludes 基本是白捡的。

★ 不开 UPX ★
  邻居项目（卡丘简易桌宠）踩过两次：UPX 会把 PyQt6 的 .pyd 压坏
  （QtCore.pyd 2.6MB → 0.5MB），症状是 sip 的类型名表损坏，
  import 时报 "cannot import type '<乱码>' from PyQt6.QtCore" 这种
  完全看不懂的形式。代价只是体积大几十 MB，远比 DLL 被压坏划算。

★ 图标用桌宠那一个 ★
  `assets/app.ico` 是从 `strinova-desktop-pet/assets/app.ico` 复制来的
  （用户：「图标就用和桌宠一样的」）。

★ 显式写死的几条"别砍" ★
  这些是邻居 spec 里用真金白银换来的教训，照抄：
    · libcrypto-3 / libssl-3 —— Python 的 `_ssl.pyd` 运行时依赖它们
    · Qt6Core / Qt6Gui / Qt6Widgets —— 主界面就是它们
    · Qt6Network —— 哪怕本程序不 import QtNetwork，Qt6Gui 的导入表
      也牵着它，砍了会 "DLL load failed"
    · qwindows —— Qt 的平台插件，砍了程序根本起不来
    · libEGL / libGLESv2 / d3dcompiler / opengl32 —— 渲染后端

产物：`dist/卡丘琴谱器/`（整个文件夹一起拷走就能用，exe 在文件夹里）。
"""

import os
import shutil

_EXCLUDES = [
    # 主程序一行都不碰的包（只出现在 tools/ 的分析脚本里）
    'torch', 'torchaudio', 'torchvision', 'cv2', 'scipy', 'librosa',
    'matplotlib', 'soundfile', 'soundcard', 'sounddevice', 'PIL',
    'sklearn', 'pandas', 'numba', 'onnx', 'onnxruntime',
    'pytorch_lightning', 'transformers', 'tokenizers',
    # Python 层冗余（钩子容易误收）
    'pytest', '_pytest', 'pluggy', 'iniconfig', 'nodeenv',
    'twisted', 'OpenSSL', 'cryptography', 'attr', 'attrs',
    'setuptools', 'pkg_resources', 'pip', 'wheel',
    'IPython', 'jupyter', 'notebook', 'nbformat',
    'tkinter', 'pydoc_data', 'lib2to3',
    # 用不到的 PyQt6 顶层模块
    'PyQt6.QtWebEngineCore', 'PyQt6.QtWebEngineWidgets',
    'PyQt6.QtWebEngineQuick', 'PyQt6.QtWebChannel',
    'PyQt6.QtQuick', 'PyQt6.QtQuickWidgets', 'PyQt6.QtQml',
    'PyQt6.QtOpenGL', 'PyQt6.QtOpenGLWidgets',
    'PyQt6.QtPrintSupport', 'PyQt6.QtPdf', 'PyQt6.QtPdfWidgets',
    'PyQt6.QtSql', 'PyQt6.QtTest', 'PyQt6.QtXml', 'PyQt6.QtDBus',
    'PyQt6.QtDesigner', 'PyQt6.QtHelp', 'PyQt6.QtSvg',
    'PyQt6.QtSvgWidgets',
    # ⚠ 千万不能排 QtMultimedia ★ —— `ui/keypad.py` 用它给制谱器的
    #   打击垫发声（`QSoundEffect`）。第一次打包就把它排掉了，
    #   结果 exe 起来就死：
    #     ui/keypad.py line 19 -> ModuleNotFoundError:
    #     No module named 'PyQt6.QtMultimedia'
    #   而它是**间接**用的（main -> ui.control -> ui.editor -> ui.keypad），
    #   光扫 main/core/ui 的顶层 import 看不见 —— 得顺着 import 链走。
    #   Qt6Multimedia 依赖 Qt6Network，所以 `_BIN_KEEP` 里那条
    #   "别砍 Qt6Network" 也是给它留的。
    'PyQt6.QtBluetooth', 'PyQt6.QtNfc', 'PyQt6.QtSensors',
    'PyQt6.QtSerialPort', 'PyQt6.QtStateMachine', 'PyQt6.QtTextToSpeech',
    'PyQt6.QtPositioning', 'PyQt6.QtNetworkAuth', 'PyQt6.QtWebSockets',
    'PyQt6.QtRemoteObjects', 'PyQt6.QtSpatialAudio',
    'PyQt6.QtCharts', 'PyQt6.QtDataVisualization', 'PyQt6.QtGraphs',
    # numpy 的测试噪音
    # ⚠ 千万别把 'numpy.typing' 也排掉 —— numpy.random._mt19937 在
    #   **顶层** import 它（不只是 delayed/conditional），排掉之后
    #   numpy 一导入就炸，exe 静默起不来（console=False 连个错都看不到）。
    #   第一次打包就栽在这，PyInstaller 的 warn 文件里那行
    #   "excluded module named numpy.typing - imported by
    #    numpy.random._mt19937 (top-level)" 就是它。
    'numpy.testing', 'numpy.f2py', 'numpy.distutils',
]

# Qt6/bin 里用不到的 DLL —— PyQt6 的 hook 会把整个 bin 收进来
_BIN_CUT = ('opengl32sw', 'Qt6Sql', 'Qt6Designer', 'Qt6Help', 'Qt6Test',
            'Qt6Bluetooth', 'Qt6Nfc', 'Qt6Sensors', 'Qt6SerialPort',
            'Qt6StateMachine', 'Qt6TextToSpeech',
            'Qt6Svg', 'Qt6WebEngine', 'Qt6WebChannel',
            'Qt6Quick', 'Qt6Qml', 'Qt6OpenGL', 'Qt6PrintSupport', 'Qt6Pdf',
            'Qt6Positioning', 'Qt6WebSockets', 'Qt6Charts',
            'Qt6DataVisualization', 'Qt6RemoteObjects', 'Qt6SpatialAudio',
            'Qt6Nfc', 'Qt6Graphs', 'Qt6ShaderTools', 'Qt6VirtualKeyboard')

# ★ 这几条是"砍了就起不来"的 ★ —— 见文件头
_BIN_KEEP = ('libcrypto', 'libssl', 'Qt6Core', 'Qt6Gui', 'Qt6Widgets',
             'Qt6Network', 'qwindows', 'libegl', 'libglesv2',
             'd3dcompiler', 'opengl32', 'qt6freetype', 'qt6harfbuzz')


def _keep_bin(entry):
    fname = os.path.basename(entry[0].replace('\\', '/'))
    low = fname.lower()
    if any(low.startswith(k.lower()) for k in _BIN_KEEP):
        return True
    return not any(low.startswith(k.lower()) for k in _BIN_CUT)


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('sheets', 'sheets')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_EXCLUDES,
    noarchive=False,
    optimize=0,
)
a.binaries = [b for b in a.binaries if _keep_bin(b)]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='卡丘琴谱器',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                       # ★ 见文件头：UPX 会压坏 PyQt6 的 .pyd
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/app.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='卡丘琴谱器',
)

# ===========================================================================
# ★ 打包后：把"要放在 exe 旁边"的资源补上 ★
# ===========================================================================
#
# 用户：「打包版本的声音跟源码的不一样啊」—— 就是这一段原来缺了。
#
# ★ 为什么音源不能走 `datas` ★
#     `datas` 里的东西全被塞进 `_internal/`（= `sys._MEIPASS`），
#     而 `ui/keypad.py` 找音源用的是
#         os.path.join(app_dir(), 'assets', 'notes')
#     `app_dir()`（见 `core/paths.py`）打包后返回的是
#     **exe 所在目录**，不是 `_internal`。两边对不上 ——
#     于是 exe 一跑发现那儿没有 wav，就地**现合成** 16 个
#     （正弦 + 包络），音色跟源码版差得远。
#     所以这些 wav 必须**复制到 exe 旁边**，不能打进 `_internal`。
#
# ★ 完整的资源清单（= 打包版跑起来会用到的东西）★
#     `_internal/sheets/demo.txt`  内置谱面        ← `datas` 里那条 `sheets`
#     `assets/notes/*.wav`         音源            ← 下面这段复制
#     `assets/app.ico`             窗口图标        ← 编译进 exe
#     `sheets/`（exe 旁边）        用户谱面目录    ← 运行时 `sheets_dir()` 自己建
#     `config.json`                配置            ← 运行时自己写
#     `_crash.log`                 崩溃日志        ← 出错才有
#     各种 `*.png` 图标            按钮图标        ← `appstyle` 运行时现画
#     最后四条都是**运行时生成**的，不需要打包。
#
# ★ 目录不存在就跳过，绝不让构建失败 ★
#     这些 wav 是**本地的**（`.gitignore` 里有 `*.wav`，
#     而且它们是游戏提取物，不进公开仓库）。
#     别人 clone 下来自己打包时多半没有 —— 那时就让程序运行时自己合成。

_notes_src = os.path.join(SPECPATH, 'assets', 'notes')
_notes_dst = os.path.join(SPECPATH, 'dist', '卡丘琴谱器', 'assets', 'notes')
_notes_n = 0
if os.path.isdir(_notes_src):
    os.makedirs(_notes_dst, exist_ok=True)
    for _f in sorted(os.listdir(_notes_src)):
        if _f.lower().endswith('.wav'):
            shutil.copy2(os.path.join(_notes_src, _f),
                         os.path.join(_notes_dst, _f))
            _notes_n += 1
    print('[spec] 音源已拷到 exe 旁边：%d 个 -> %s' % (_notes_n, _notes_dst))
else:
    print('[spec] 源码里没有 assets/notes/ —— 跳过；'
          '打包版第一次运行时会自己合成音源（音色跟源码版不同）')

# ★ 自检：该在的都在吗 ★
#   少一样都不会当场报错（程序会静默降级，比如没音源就现合成），
#   所以打完包必须念一遍，别等用户听出来。
_dist = os.path.join(SPECPATH, 'dist', '卡丘琴谱器')
_coll = os.path.join(_dist, '_internal')
for _label, _p in (
        ('卡丘琴谱器.exe', os.path.join(_dist, '卡丘琴谱器.exe')),
        ('_internal/python314.dll', os.path.join(_coll, 'python314.dll')),
        ('_internal/sheets/demo.txt', os.path.join(_coll, 'sheets', 'demo.txt')),
        ('_internal/PyQt6', os.path.join(_coll, 'PyQt6')),
        ('_internal/numpy', os.path.join(_coll, 'numpy')),
        ('assets/notes（音源）', _notes_dst)):
    _ok = os.path.isdir(_p) if _label.endswith('（音源）') or _label.endswith(
        'PyQt6') or _label.endswith('numpy') else os.path.exists(_p)
    print('[spec] %s %s' % ('OK  ' if _ok else '★缺 ', _label))
print('[spec] 音源文件数：%d' % _notes_n)
