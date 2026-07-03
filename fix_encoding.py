import io
content = """@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
echo =======================================================
echo   PCシステム音声録音（CUI）ツール - コマンドライン版
echo =======================================================
echo.

if not "%~1"=="" (
    echo [ファイルがドロップされました]
    echo ファイル: %~nx1
    echo.
    set "is_video=0"
    set "ext=%~x1"
    if /I "!ext!"==".mp4" set is_video=1
    if /I "!ext!"==".webm" set is_video=1
    if /I "!ext!"==".avi" set is_video=1
    if /I "!ext!"==".mkv" set is_video=1
    if /I "!ext!"==".mov" set is_video=1

    if "!is_video!"=="1" (
        choice /C yn /N /M "キースライド（スライド画像）も抽出しますか？ [y: する / n: しない（音声のみ）] [y,n]? "
        if errorlevel 2 (
            echo.
            echo ^> 【音声のみ】 文字起こしのみを行います。
            Gemini_CLI_Transcriber.exe "%~1"
        ) else (
            echo.
            echo ^> 【スライド抽出】 キースライド抽出と文字起こしを行います。
            Gemini_CLI_Transcriber.exe --video "%~1" --extract-key-slides
        )
    ) else (
        echo ^> 音声ファイルの文字起こしを開始します。
        Gemini_CLI_Transcriber.exe "%~1"
    )
    echo.
    pause
    exit /b
)

echo   PCの音声をリアルタイムで録音し、AIが文字起こしします。
echo   あわせて画面を録画し、スライドを自動抽出することも可能です。
echo.
choice /C yn /N /M "画面を録画して自動でスライドを抽出しますか？ [y: 録画＋音声 / n: 音声のみ] [y,n]? "
if errorlevel 2 (
    echo.
    echo ^> 【音声のみ】 録画は行わず、PCの音声の録音のみ行います。
    Gemini_CLI_Transcriber.exe
) else (
    echo.
    echo ^> 【録画＋音声】 画面を録画し、スライド抽出と音声の録音を行います。
    Gemini_CLI_Transcriber.exe --record-screen
)
echo.
pause
"""
with open("release_scripts/Start_Transcriber.bat", "w", encoding="cp932", errors="ignore") as f:
    f.write(content)
