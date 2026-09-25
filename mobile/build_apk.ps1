# 卡丘琴谱器 · 安卓版 —— 一键打包 APK
#
# 用法：在项目根目录执行
#     powershell -ExecutionPolicy Bypass -File mobile\build_apk.ps1
#
# 产物：mobile\android\app\build\outputs\apk\debug\app-debug.apk
#       脚本最后会把它复制到桌面，方便直接传到手机。
#
# ★ 依赖的东西都在 `_android_tools`（跟项目同级）★
#   JDK 17 + Android cmdline-tools（sdkmanager 装的 platform-34 / build-tools 34）
#   换机器的话改下面两个路径，或者把它们设成环境变量。

param(
    [string]$JdkRoot = "",
    [string]$SdkRoot = "",
    [switch]$Release
)

# ★ 这里**不能**用 'Stop' ★
#   这个脚本从头到尾都在调外部程序（java / npx / gradlew），而它们
#   很多信息是往 **stderr** 写的 —— PowerShell 5.1 会把 native command
#   的 stderr 包装成 ErrorRecord，`ErrorActionPreference = 'Stop'`
#   一看见就**直接把脚本掐掉**。
#
#   最典型的是 `java -version`：它把版本号打到 stderr（Java 的老传统），
#   于是脚本在"检查 JDK"这一步就无声无息地退出，后面什么都不跑，
#   只留下一条看着像报错的版本号。
#
#   改成 'Continue'，每一步**自己查 `$LASTEXITCODE`** ——
#   对调外部程序的脚本来说这才是对的写法。
$ErrorActionPreference = 'Continue'

function Fail($msg) {
    Write-Host ""
    Write-Host "★ $msg" -ForegroundColor Red
    exit 1
}

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Tools = Join-Path (Split-Path -Parent $Root) '_android_tools'

if (-not $JdkRoot) {
    $cand = Get-ChildItem (Join-Path $Tools 'jdk') -Directory -ErrorAction SilentlyContinue |
            Select-Object -First 1
    if ($cand) { $JdkRoot = $cand.FullName }
}
if (-not $SdkRoot) { $SdkRoot = Join-Path $Tools 'sdk' }

if (-not (Test-Path "$JdkRoot\bin\java.exe")) {
    throw "没找到 JDK。用 -JdkRoot 指定，或者检查 $Tools\jdk"
}

# ★ 光看文件在不在是不够的 ★
#   真出过事：一次解压中途被打断（防病毒实时扫描锁住了刚写出来的 .exe），
#   JDK 目录**结构完整、内容半残** —— `javac.exe` 还在、`java.exe` 没了、
#   `lib/modules` 缺了几块。只看目录的话会一路走到 Gradle 才报一句
#   看不懂的错。所以这里真的把 java 跑一下。
#
#   （修的办法：`tar -xf` 解压到一个**全新空目录**。`Expand-Archive -Force`
#     会先尝试删掉已有的同名文件，而那些文件正被扫描锁着 ——
#     于是删除失败、覆盖也失败，留下的就是混合状态。
#     `tar` 没这个"先删后写"的阶段，稳得多。）
#
# ★ 顺序有讲究：先存 LASTEXITCODE，再用管道 ★
#   `java -version` 把版本号打到 **stderr**，所以要 `2>&1` 才收得到。
#   但**不能**写成 `(& java -version 2>&1 | Select-Object -First 1)` ——
#   `Select-Object -First 1` 拿到第一项后会**掐断上游管道**，
#   被中断的原生命令退出码变成 **-1**，于是这里误报"JDK 起不来"，
#   而 JDK 明明是好的（实测：直接调是 0，套上那个管道就成了 -1）。
Remove-Item Env:\JAVA_TOOL_OPTIONS -ErrorAction SilentlyContinue
$jvOut = & "$JdkRoot\bin\java.exe" -version 2>&1
$jvCode = $LASTEXITCODE
$jv = ($jvOut | Select-Object -First 1) -join ''
if ($jvCode -ne 0 -or [string]::IsNullOrEmpty($jv)) {
    Fail "JDK 起不来（$JdkRoot 里那份可能坏了，或者 JAVA_TOOL_OPTIONS 在捣乱）。"
}
Write-Host "Java       : $jv"

if (-not (Test-Path "$SdkRoot\platforms\android-34\android.jar")) {
    throw "没找到 Android SDK（缺 platform-34）。用 -SdkRoot 指定，或者检查 $Tools\sdk"
}

# ★ 系统里预设的 JAVA_TOOL_OPTIONS 带了个 JDK17 不认的开关 ★
#   （`-XX:+UseAllWindowsProcessorGroups`）—— 不清掉的话 JVM 直接起不来，
#   报一句 "Unrecognized VM option"，然后 Gradle 说找不到 Java。
#   这个坑排查起来挺绕：`java -version` 看着是好的，只有真正带参数跑才炸。
Remove-Item Env:\JAVA_TOOL_OPTIONS -ErrorAction SilentlyContinue

$env:JAVA_HOME = $JdkRoot
$env:ANDROID_HOME = $SdkRoot
$env:ANDROID_SDK_ROOT = $SdkRoot

Write-Host "JDK        : $JdkRoot"
Write-Host "Android SDK: $SdkRoot"

# Gradle 靠这个文件找 SDK
$localProps = Join-Path $Root 'mobile\android\local.properties'
$sdkLine = 'sdk.dir=' + ($SdkRoot -replace '\\', '\\' -replace ':', '\:')
Set-Content -Path $localProps -Value $sdkLine -Encoding ascii
Write-Host "写入 $localProps"

Push-Location (Join-Path $Root 'mobile')
try {
    Write-Host "`n[0/3] 把音源拷进网页目录（web/assets/notes/）"
    # ★ 这份是**复制**出来的，没进仓库 ★
    #   `assets/notes/` 才是音源的唯一来源；`web/assets/notes/` 是给
    #   网页用的那一份。提交两份迟早会漂移（换了音色只改了其中一边），
    #   所以每次构建都从源头同步一遍。
    $srcNotes = Join-Path $Root 'assets\notes'
    $dstNotes = Join-Path $Root 'web\assets\notes'
    if (-not (Test-Path $srcNotes)) { throw "找不到音源目录：$srcNotes" }
    New-Item -ItemType Directory -Force -Path $dstNotes | Out-Null
    $wavs = Get-ChildItem (Join-Path $srcNotes '*.wav')
    if ($wavs.Count -eq 0) { throw "$srcNotes 里一个 wav 都没有" }
    Copy-Item (Join-Path $srcNotes '*.wav') $dstNotes -Force
    Write-Host "     $($wavs.Count) 个音源已同步"

    Write-Host "`n[1/3] 同步网页资源到安卓工程（web/ -> android/app/src/main/assets/public）"
    npx cap sync android
    if ($LASTEXITCODE -ne 0) { throw "cap sync 失败" }

    $task = if ($Release) { 'assembleRelease' } else { 'assembleDebug' }
    Write-Host "`n[2/3] Gradle 构建（$task），第一次会下依赖，慢"
    Push-Location 'android'
    try {
        .\gradlew.bat --no-daemon $task
        if ($LASTEXITCODE -ne 0) { throw "Gradle 构建失败" }
    } finally {
        Pop-Location
    }
} finally {
    Pop-Location
}

$apkName = if ($Release) { 'app-release-unsigned.apk' } else { 'app-debug.apk' }
$apk = Join-Path $Root "mobile\android\app\build\outputs\apk\$(if ($Release) {'release'} else {'debug'})\$apkName"
if (-not (Test-Path $apk)) {
    throw "构建跑完了但没找到 APK：$apk"
}

$size = [math]::Round((Get-Item $apk).Length / 1MB, 2)
Write-Host "`n[3/3] 产物：$apk （$size MB）"

$desktop = [Environment]::GetFolderPath('Desktop')
$dest = Join-Path $desktop '卡丘琴谱器-安卓版.apk'
Copy-Item $apk $dest -Force
Write-Host "已复制到桌面：$dest"
Write-Host "`n装到手机上：把 apk 传过去点安装（需要在系统设置里允许「安装未知来源应用」）。"
