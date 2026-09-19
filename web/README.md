# 卡丘琴谱器 · 手机版

安卓手机上的完整琴谱器 —— 看谱、跟打、可按、训练、制谱，功能跟桌面版对齐。

产物是 `卡丘琴谱器-安卓版.apk`（约 6 MB），装到手机上就能用。

---

## 跟桌面版是什么关系

**同一套逻辑，两个壳。** 具体说：

| | 桌面版 | 手机版 |
|---|---|---|
| 界面 | PyQt6 + QPainter 自绘 | HTML/CSS + Canvas |
| 核心逻辑 | `core/*.py` | `web/js/*.js`（**逐字段对拍过**） |
| 谱面格式 | `.txt` | `.txt` —— **完全一样** |
| 音源 | `assets/notes/*.wav` | 同一批 16 个文件 |

`web/js/parser.js` 是从 `core/parser.py` 移植的，`tools/port_check_parser.py`
拿 25 条用例（含空文件、和弦、多段变速、时间轴格式、非法 token、CRLF…）
逐字段比过；时间轴那边另有 `tools/port_check_timeline.py`（13 条用例 +
18 个时间探针）。两个脚本随时可以重跑：

```bash
python tools/port_check_parser.py      # Python vs JS 解析结果
python tools/port_check_timeline.py    # Python vs JS 时间轴
```

## 谱面怎么互通

**不用转换，就是同一种文件。**

- 电脑 → 手机：曲库页点「导入 .txt」，从文件管理器里挑
  （电脑版 `sheets/` 目录里的文件直接能用）
- 手机 → 电脑：曲库页点「导出」，得到 `曲名.txt`，
  丢进电脑版的 `sheets/` 文件夹，按「刷新」就出现了

两边都是 UTF-8 纯文本，编辑器也能直接改。

## 手机上怎么用

三页，底部标签栏切换：

**练琴** —— 中间是那台琴的 4×4 网格。
- 格子会按谱面依次亮起来：深橄榄底 + 亮黄粗边的是**现在该弹的**，
  往后的格子越来越暗（用颜色深浅读先后），角标上的 `2` `3`
  是"后面第几个"，`×2` 是"这个键要连按两下"
- 该弹的那个格子外面有个**收缩圆圈**，缩到中心就是"该按了"
- 底部三个开关：
  - **跟打** —— 播放不放原声，你点格子才出声（练琴用）
  - **可按** —— 格子能点着发声
  - **训练** —— 按**音符顺序**一个一个点，点对了才走下一个，
    不看曲子时间（练指法用）

**制谱** —— 三个子标签：
- **谱面文本** —— 直接写/粘贴，跟电脑版一个格式
- **打击垫** —— 点一下就出声、写进谱面（带 PAD 编号，照游戏里对得上）
- **时间轴** —— 单指横向拖滚动，点方块选中，按住拖动改时间，
  下面的 `－` `＋` 缩放，`删掉选中的` 删除。
  在时间轴上拖动之后，谱面会自动转成"时间轴格式"（`秒:音高`）——
  这种格式的每个音位置独立，才能左右挪

**曲库** —— 新建 / 导入 / 导出 / 删除。

## 自己重新打包

```powershell
powershell -ExecutionPolicy Bypass -File mobile\build_apk.ps1
```

产物在 `mobile\android\app\build\outputs\apk\debug\app-debug.apk`，
脚本会顺手复制一份到桌面。

需要 JDK 17 和 Android SDK（platform-34 + build-tools 34）。
脚本默认去项目同级的 `_android_tools` 找，也可以用 `-JdkRoot` / `-SdkRoot` 指定。

### 踩过的坑（改脚本的人会需要）

**1. `JAVA_TOOL_OPTIONS` 会让 JVM 直接起不来。**
某些机器上这个环境变量预设了 `-XX:+UseAllWindowsProcessorGroups`，
JDK 17 不认它 —— 报 `Unrecognized VM option`，然后 Gradle 说找不到 Java。
阴险的地方是 `java -version` 单独跑**看着是好的**，只有带参数跑才炸。
脚本里第一件事就是 `Remove-Item Env:\JAVA_TOOL_OPTIONS`。

**2. Gradle 首次构建要下约 1.8 GB**（Gradle 本体 + Android 依赖），
耗时十几分钟。之后就快了（增量 20 秒左右）。

**3. `cap sync` 必须重新跑。**
改了 `web/` 里的东西之后，不 sync 的话 APK 里还是旧的 ——
`web/` 是源，`android/app/src/main/assets/public/` 是 `cap sync` 拷过去的副本。

## 还能用浏览器直接开

`web/` 本身是个纯静态的网页应用，起个 http 服务就能用：

```bash
# ★ 先把音源拷过来 ★
#   `assets/notes/` 是唯一的源头；`web/assets/notes/` 是给网页用的那一份。
#   提交两份迟早会漂移（换了音色只改了其中一边），所以那份没进仓库，
#   由构建脚本每次同步。手动开网页版的话自己跑一次这个拷贝。
cp assets/notes/*.wav web/assets/notes/

python -m http.server 8765 --directory web
```

然后浏览器打开 `http://127.0.0.1:8765/`。

它同时是个 PWA（有 manifest 和 Service Worker）——手机浏览器打开后
可以「添加到主屏幕」，看起来就跟装了个 App 一样，而且**第一次打开之后
没网也能用**（静态资源和 16 个音源都进了缓存）。

## 目录

```
web/                    网页应用（也是 PWA）
  index.html            三页的骨架
  css/app.css           样式（配色沿用桌面版 theme.py / appstyle.py）
  js/parser.js          谱面解析     ← 移植自 core/parser.py
  js/timeline.js        时间轴       ← 移植自 core/timeline.py
  js/layout.js          16 键位映射  ← 移植自 core/layout.py
  js/render.js          格子渲染（照搬桌面版 GridView 的视觉规则）
  js/tledit.js          时间轴编辑器（PR 那种分区：标尺 + 轨道头 + 轨道）
  js/audio.js           Web Audio 放采样
  js/store.js           本地曲库 + 导入导出
  js/app.js             三页的主控
  sw.js                 离线缓存
  assets/notes/*.wav    16 个音源（跟桌面版同一批）
  icons/                App 图标（tools/make_web_icons.py 画的）

mobile/                 Capacitor 工程（打 APK 用）
  capacitor.config.json webDir 指向 ../web
  package.json
  build_apk.ps1         一键打包
  android/              生成的安卓工程
```
