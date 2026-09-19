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

$ErrorActionPreference = 'Stop'

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
