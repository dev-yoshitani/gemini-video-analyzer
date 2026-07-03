import os

content_transcriber = """@echo off
cd /d "%~dp0"

set "EXE_PATH="
if exist "Gemini_CLI_Transcriber.exe" (
    set "EXE_PATH=Gemini_CLI_Transcriber.exe"
) else if exist "audio_transcriber.py" (
    set "EXE_PATH=python audio_transcriber.py"
) else (
    echo 実行に必要なファイル（Gemini_CLI_Transcriber.exe または audio_transcriber.py）が見つかりません。
    pause
    exit /b
)

if "%~1"=="" (
    echo =======================================================
    echo   PC音声録音 ＆ 文字起こしツール
    echo =======================================================
    echo.
    echo   PCのシステム音声を録音し、AIが文字起こしします。
    echo.
    %EXE_PATH%
) else (
    echo =======================================================
    echo   ファイル文字起こしツール
    echo =======================================================
    echo.
    echo   ファイル: %~nx1
    echo.
    
    echo "%~x1" | findstr /i "\.mp4 \.avi \.mkv \.mov \.wmv \.flv \.webm" >nul
    if not errorlevel 1 (
        echo ＞ 動画ファイルが指定されました。
        
        echo "%*" | findstr /i "\-\-extract-key-slides" >nul
        if not errorlevel 1 (
            echo ＞ 既にスライド抽出オプションが指定されています。
            %EXE_PATH% --video "%~1" --extract-key-slides
        ) else (
            choice /C yn /N /T 120 /D n /M "キースライド（スライド画像）も抽出しますか？ [y:する / n:しない（音声のみ）] [y,n]? "
            if errorlevel 2 (
                echo.
                echo ＞ スライド抽出はスキップし、音声の文字起こしのみ行います。
                %EXE_PATH% "%~1"
            ) else (
                echo.
                echo ＞ スライド抽出と音声の文字起こしを行います。
                %EXE_PATH% --video "%~1" --extract-key-slides
            )
        )
    ) else (
        echo ＞ 音声ファイルの文字起こしを開始します。
        %EXE_PATH% "%~1"
    )
)
echo.
pause
"""

content_screen = """@echo off
cd /d "%~dp0"

set "EXE_PATH="
if exist "Gemini_CLI_Transcriber.exe" (
    set "EXE_PATH=Gemini_CLI_Transcriber.exe"
) else if exist "audio_transcriber.py" (
    set "EXE_PATH=python audio_transcriber.py"
) else (
    echo 実行に必要なファイル（Gemini_CLI_Transcriber.exe または audio_transcriber.py）が見つかりません。
    pause
    exit /b
)

echo =======================================================
echo   画面録画 ＆ スライド抽出ツール
echo =======================================================
echo.
echo   画面を録画し、自動でキースライド抽出と文字起こしを行います。
echo.
%EXE_PATH% --record-screen
echo.
pause
"""

with open("run_transcriber.bat", "w", encoding="cp932", errors="ignore") as f:
    f.write(content_transcriber)

with open("run_screen_recorder.bat", "w", encoding="cp932", errors="ignore") as f:
    f.write(content_screen)

try:
    os.remove("release_scripts/run_transcriber.bat")
    os.remove("release_scripts/run_screen_recorder.bat")
except Exception:
    pass
