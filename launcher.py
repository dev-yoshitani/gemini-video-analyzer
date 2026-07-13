"""Start.bat から呼び出す日本語の統合ランチャー。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


def _wait_for_key() -> None:
    try:
        input("\nEnterキーを押すと閉じます...")
    except (EOFError, KeyboardInterrupt):
        pass


def _run_python(script_name: str, *arguments: str) -> int:
    script = BASE_DIR / script_name
    result = subprocess.run([sys.executable, str(script), *arguments], cwd=BASE_DIR)
    return result.returncode


def _run_hybrid_video(video_path: str) -> int:
    print("\n高精度AI解析を開始します。元動画全体は送信しません。")
    result = _run_python("gemini_hybrid_analyzer.py", video_path)
    _wait_for_key()
    return result


def _run_recording() -> int:
    print("\n画面とPC音声を録画します。画面通知や個人情報に注意してください。")
    result = _run_python("local_screen_recorder.py")
    _wait_for_key()
    return result


def _run_pending_analysis() -> int:
    from gemini_hybrid_analyzer import list_pending_analyses

    jobs = list_pending_analyses()
    if not jobs:
        print("\n再開できる未完了の高精度解析はありません。")
        return 0
    print("\n再開する解析を選んでください:")
    for index, job in enumerate(jobs, 1):
        source_name = Path(job["source"]).name
        print(f"  [{index}] {source_name}（停止位置: {job['current_stage']}）")
    try:
        choice = input("番号を入力してください: ").strip()
    except (EOFError, KeyboardInterrupt):
        return 0
    if not choice.isdigit() or not 1 <= int(choice) <= len(jobs):
        print("番号が正しくありません。")
        return 1
    state_path = jobs[int(choice) - 1]["state_path"]
    result = _run_python("gemini_hybrid_analyzer.py", "--resume", state_path)
    _wait_for_key()
    return result


def _run_gemini() -> int:
    script = BASE_DIR / "audio_transcriber.py"
    if not script.is_file():
        print("Gemini起動ファイルが見つかりません。")
        return 1
    return _run_python("audio_transcriber.py")


def _ask_video_path() -> str | None:
    try:
        value = input("\n動画ファイルをドラッグ＆ドロップしてEnterを押してください: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    return value.strip('"') or None


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments:
        return _run_hybrid_video(arguments[0])

    while True:
        print("\n" + "=" * 56)
        print("  動画解析ツール")
        print("=" * 56)
        print("  [1] 録画して高精度AI解析・解析結果PDF（おすすめ）")
        print("  [2] 保存済み動画を高精度AI解析・解析結果PDF")
        print("  [3] 未完了の高精度AI解析を途中から再開")
        print("  [4] 従来のGemini文字起こしツール")
        print("  [0] 終了")
        try:
            choice = input("\n番号を選んでください: ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0

        if choice == "1":
            return _run_recording()
        if choice == "2":
            video_path = _ask_video_path()
            if video_path:
                return _run_hybrid_video(video_path)
            print("動画が選択されませんでした。")
        elif choice == "3":
            return _run_pending_analysis()
        elif choice == "4":
            return _run_gemini()
        elif choice == "0":
            return 0
        else:
            print("0〜4の番号を入力してください。")


if __name__ == "__main__":
    raise SystemExit(main())
