import os

content = """@echo off
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

if not "%~1"=="" (
    echo =======================================================
    echo   ファイル文字起こし
    echo =======================================================
    echo.
    echo   ファイル: %~nx1
    echo.
    
    echo "%~x1" | findstr /i "\\.mp4 \\.avi \\.mkv \\.mov \\.wmv \\.flv \\.webm" >nul
    if not errorlevel 1 (
        choice /C yn /N /M "キースライド（スライド画像）も抽出しますか？ [y:する / n:しない(音声のみ)] [y,n]? "
        if errorlevel 2 (
            echo.
            echo ＞ スライド抽出はスキップし、音声の文字起こしのみ行います。
            %EXE_PATH% "%~1"
        ) else (
            echo.
            echo ＞ スライド抽出と音声の文字起こしを行います。
            %EXE_PATH% --video "%~1" --extract-key-slides
        )
    ) else (
        echo ＞ 音声ファイルの文字起こしを開始します。
        %EXE_PATH% "%~1"
    )
    echo.
    pause
    exit /b
)

:MENU
cls
echo =======================================================
echo   Gemini Voice Transcriber - 総合メニュー
echo =======================================================
echo.
echo  [1] PC音声をリアルタイム録音して文字起こし
echo  [2] PC画面の録画と録音を行い、スライド抽出と議事録作成
echo  [3] 既存の音声・動画ファイルを文字起こし
echo.
choice /C 123 /N /M "実行したい機能の番号を入力してください [1, 2, 3]: "
if errorlevel 3 goto TRANSCRIBE_FILE
if errorlevel 2 goto RECORD_SCREEN
if errorlevel 1 goto RECORD_AUDIO

:RECORD_AUDIO
echo.
echo =======================================================
echo   PC音声録音 ＆ 文字起こし
echo =======================================================
%EXE_PATH%
echo.
pause
exit /b

:RECORD_SCREEN
echo.
echo =======================================================
echo   画面録画 ＆ スライド抽出
echo =======================================================
%EXE_PATH% --record-screen
echo.
pause
exit /b

:TRANSCRIBE_FILE
echo.
echo =======================================================
echo   既存ファイルの文字起こし
echo =======================================================
echo.
echo 文字起こししたいファイル（音声または動画）を
echo この黒い画面内にドラッグ＆ドロップして、Enterキーを押してください。
echo.
set /p TARGET_FILE="ファイルパス: "

rem ダブルクォーテーションを取り除く処理
set TARGET_FILE=%TARGET_FILE:"=%

if "%TARGET_FILE%"=="" (
    echo.
    echo ファイルが指定されませんでした。メニューに戻ります。
    pause
    goto MENU
)

echo.
echo ファイル: %TARGET_FILE%
echo.
echo "%TARGET_FILE%" | findstr /i "\\.mp4 \\.avi \\.mkv \\.mov \\.wmv \\.flv \\.webm" >nul
if not errorlevel 1 (
    choice /C yn /N /M "キースライド（スライド画像）も抽出しますか？ [y:する / n:しない(音声のみ)] [y,n]? "
    if errorlevel 2 (
        echo.
        echo ＞ スライド抽出はスキップし、音声の文字起こしのみ行います。
        %EXE_PATH% "%TARGET_FILE%"
    ) else (
        echo.
        echo ＞ スライド抽出と音声の文字起こしを行います。
        %EXE_PATH% --video "%TARGET_FILE%" --extract-key-slides
    )
) else (
    echo ＞ 音声ファイルの文字起こしを開始します。
    %EXE_PATH% "%TARGET_FILE%"
)
echo.
pause
exit /b
"""

with open("Start_Transcriber.bat", "w", encoding="cp932", errors="ignore") as f:
    f.write(content)

try:
    os.remove("run_transcriber.bat")
    os.remove("run_screen_recorder.bat")
except Exception:
    pass
