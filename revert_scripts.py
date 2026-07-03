import os

content_transcriber = """@echo off
cd /d "%~dp0"

if "%~1"=="" (
    echo =======================================================
    echo   PC音声録音 ＆ 文字起こしツール
    echo =======================================================
    echo.
    echo   PCのシステム音声を録音し、AIが文字起こしします。
    echo.
    Gemini_CLI_Transcriber.exe
) else (
    echo =======================================================
    echo   ファイル文字起こしツール
    echo =======================================================
    echo.
    echo   ファイル: %~nx1
    echo.
    Gemini_CLI_Transcriber.exe "%~1"
)
echo.
pause
"""

content_screen = """@echo off
cd /d "%~dp0"

echo =======================================================
echo   画面録画 ＆ スライド抽出ツール
echo =======================================================
echo.
echo   画面を録画し、自動でキースライド抽出と文字起こしを行います。
echo.
Gemini_CLI_Transcriber.exe --record-screen
echo.
pause
"""

with open("release_scripts/run_transcriber.bat", "w", encoding="cp932", errors="ignore") as f:
    f.write(content_transcriber)

with open("release_scripts/run_screen_recorder.bat", "w", encoding="cp932", errors="ignore") as f:
    f.write(content_screen)

try:
    os.remove("release_scripts/Start_Transcriber.bat")
except Exception:
    pass
