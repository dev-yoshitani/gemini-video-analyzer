"""Start.bat から呼び出す統合ランチャー。"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


def _is_english(language: str) -> bool:
    return language == "en"


def _wait_for_key(language: str = "ja") -> None:
    prompt = "\nPress Enter to close..." if _is_english(language) else "\nEnterキーを押すと閉じます..."
    try:
        input(prompt)
    except (EOFError, KeyboardInterrupt):
        pass


def _run_python(script_name: str, *arguments: str) -> int:
    script = BASE_DIR / script_name
    result = subprocess.run([sys.executable, str(script), *arguments], cwd=BASE_DIR)
    return result.returncode


def _run_hybrid_video(video_path: str, language: str = "ja") -> int:
    english = _is_english(language)
    if english:
        print("\nStarting high-accuracy AI analysis. The full source video is not uploaded.")
    else:
        print("\n高精度AI解析を開始します。元動画全体は送信しません。")

    arguments = [video_path]
    sidecar_audio = _find_sidecar_audio(video_path)
    if sidecar_audio is not None:
        if english:
            print(f"Using PC audio from the same folder: {sidecar_audio.name}")
        else:
            print(f"同じフォルダのPC音声を自動使用します: {sidecar_audio.name}")
        arguments.extend(["--audio", str(sidecar_audio)])
    if english:
        arguments.extend(["--language", "en"])

    result = _run_python("gemini_hybrid_analyzer.py", *arguments)
    _wait_for_key(language)
    return result


def _find_sidecar_audio(video_path: str) -> Path | None:
    """録画動画と同じフォルダにあるPC音声を自動で見つける。"""
    source = Path(video_path).expanduser()
    candidates = (
        source.parent / "PC音声.wav",
        source.parent / "PC Audio.wav",
        source.parent / "PC_Audio.wav",
        source.with_suffix(".wav"),
    )
    for candidate in candidates:
        if candidate.is_file() and candidate.stat().st_size > 44:
            return candidate.resolve()
    return None


def _run_recording(language: str = "ja") -> int:
    if _is_english(language):
        print("\nRecording the screen and PC audio, then creating an English AI analysis report.")
        result = _run_python("local_screen_recorder.py", "--language", "en")
    else:
        print("\n画面とPC音声を録画します。録画範囲や個人情報に注意してください。")
        result = _run_python("local_screen_recorder.py")
    _wait_for_key(language)
    return result


def _run_pending_analysis(language: str = "ja") -> int:
    from gemini_hybrid_analyzer import list_pending_analyses

    english = _is_english(language)
    jobs = list_pending_analyses()
    if not jobs:
        print("\nThere are no unfinished high-accuracy AI analyses." if english else "\n再開できる未完了の高精度AI解析はありません。")
        return 0

    print("\nChoose an analysis to resume:" if english else "\n再開する解析を選んでください:")
    for index, job in enumerate(jobs, 1):
        source_name = Path(job["source"]).name
        if english:
            print(f"  [{index}] {source_name}")
        else:
            print(f"  [{index}] {source_name}（現在位置: {job['current_stage']}）")
    try:
        choice = input("Enter a number: " if english else "番号を入力してください: ").strip()
    except (EOFError, KeyboardInterrupt):
        return 0
    if not choice.isdigit() or not 1 <= int(choice) <= len(jobs):
        print("That selection is invalid." if english else "選択が正しくありません。")
        return 1
    state_path = jobs[int(choice) - 1]["state_path"]
    arguments = ["--resume", state_path]
    if english:
        arguments.extend(["--language", "en"])
    result = _run_python("gemini_hybrid_analyzer.py", *arguments)
    _wait_for_key(language)
    return result


def _run_gemini() -> int:
    script = BASE_DIR / "audio_transcriber.py"
    if not script.is_file():
        print("Gemini文字起こしファイルが見つかりません。")
        return 1
    return _run_python("audio_transcriber.py")


def _ask_video_path(language: str = "ja") -> str | None:
    prompt = (
        "\nDrag and drop a video file here, then press Enter: "
        if _is_english(language)
        else "\n動画ファイルをドラッグ＆ドロップしてEnterを押してください: "
    )
    try:
        value = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return None
    return value.strip('"') or None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Launch the high-accuracy video analysis workflow.")
    parser.add_argument("--language", choices=("ja", "en"), default="ja")
    parser.add_argument("video", nargs="?", help="Video file to analyze")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    language = args.language
    english = _is_english(language)
    if args.video:
        if english:
            return _run_hybrid_video(args.video, language="en")
        return _run_hybrid_video(args.video)

    while True:
        print("\n" + "=" * 56)
        if english:
            print("  Video Analysis Tool — English Report")
        else:
            print("  動画解析ツール")
        print("=" * 56)
        if english:
            print("  [1] Record screen + create English AI analysis PDF")
            print("  [2] Analyze a saved video + create English AI analysis PDF")
            print("  [3] Resume an unfinished English analysis")
            print("  [0] Exit")
        else:
            print("  [1] 録画して高精度AI解析（解析結果PDFを出す）")
            print("  [2] 保存済み動画を高精度AI解析（解析結果PDF）")
            print("  [3] 未完了の高精度AI解析を一覧から再開")
            print("  [4] 従来のGemini文字起こしツール")
            print("  [0] 終了")
        try:
            choice = input("\nChoose an option: " if english else "\n選択を選んでください: ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0

        if choice == "1":
            return _run_recording(language)
        if choice == "2":
            video_path = _ask_video_path(language)
            if video_path:
                return _run_hybrid_video(video_path, language)
            print("No video was selected." if english else "動画が選択されませんでした。")
        elif choice == "3":
            return _run_pending_analysis(language)
        elif choice == "4" and not english:
            return _run_gemini()
        elif choice == "0":
            return 0
        else:
            print("Choose a number from the menu." if english else "0〜4の選択を入力してください。")


if __name__ == "__main__":
    raise SystemExit(main())
