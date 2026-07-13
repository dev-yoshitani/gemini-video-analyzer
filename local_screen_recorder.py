"""画面とPC再生音をローカル録画し、そのまま動画解析へ渡す。"""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import os
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Any

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None
    np = None

try:
    import mss
except ImportError:
    mss = None


def _require_recording_dependencies() -> None:
    missing = []
    if cv2 is None or np is None:
        missing.append("opencv-python-headless / numpy")
    if mss is None:
        missing.append("mss")
    try:
        import pyaudiowpatch  # noqa: F401
    except ImportError:
        missing.append("PyAudioWPatch")
    if missing:
        raise RuntimeError(
            "録画用ライブラリがありません: "
            + ", ".join(missing)
            + "\n  pip install -r requirements-local.txt"
        )


def _normalize_capture_region(region: dict[str, int]) -> dict[str, int]:
    """動画エンコーダーで扱える偶数サイズの録画領域へ整える。"""
    normalized = {
        "left": int(region["left"]),
        "top": int(region["top"]),
        "width": int(region["width"]) // 2 * 2,
        "height": int(region["height"]) // 2 * 2,
    }
    if normalized["width"] < 32 or normalized["height"] < 32:
        raise ValueError("録画範囲が小さすぎます。32×32ピクセル以上を選んでください。")
    return normalized


def _available_monitors() -> list[dict[str, int]]:
    _require_recording_dependencies()
    with mss.mss() as capture:
        return [_normalize_capture_region(dict(item)) for item in capture.monitors]


def _choose_monitor(monitors: list[dict[str, int]], purpose: str) -> dict[str, int]:
    physical = monitors[1:]
    if len(physical) == 1:
        return physical[0]
    print(f"\n{purpose}モニターを選んでください:")
    for index, monitor in enumerate(physical, 1):
        print(
            f"  [{index}] モニター{index}: {monitor['width']}×{monitor['height']} "
            f"(位置 {monitor['left']}, {monitor['top']})"
        )
    try:
        choice = input("番号を入力してください: ").strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise RuntimeError("録画範囲の選択をキャンセルしました。") from exc
    if not choice.isdigit() or not 1 <= int(choice) <= len(physical):
        raise ValueError("モニター番号が正しくありません。")
    return physical[int(choice) - 1]


def _select_region_with_mouse(monitor: dict[str, int]) -> dict[str, int]:
    """選択したモニター上でドラッグされた矩形を返す。"""
    try:
        import tkinter as tk
    except ImportError as exc:
        raise RuntimeError("範囲選択画面を開始できません。Tkinterが必要です。") from exc

    result: dict[str, int] | None = None
    start: tuple[int, int] | None = None
    rectangle: int | None = None
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-alpha", 0.28)
    root.configure(background="black")
    root.update_idletasks()

    # 負の座標に置かれた左側モニターでも正しい位置へ表示する。
    if sys.platform == "win32":
        hwnd = int(root.frame(), 16)
        ctypes.windll.user32.SetWindowPos(
            hwnd,
            -1,
            monitor["left"],
            monitor["top"],
            monitor["width"],
            monitor["height"],
            0x0040,
        )
    else:
        root.geometry(
            f"{monitor['width']}x{monitor['height']}"
            f"{monitor['left']:+d}{monitor['top']:+d}"
        )

    canvas = tk.Canvas(root, cursor="cross", background="black", highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    canvas.create_text(
        monitor["width"] // 2,
        36,
        text="録画したい範囲をドラッグしてください（Escでキャンセル）",
        fill="white",
        font=("Yu Gothic UI", 16, "bold"),
    )

    def cancel(_event: Any = None) -> None:
        root.quit()

    def press(event: Any) -> None:
        nonlocal start, rectangle
        start = (event.x, event.y)
        if rectangle is not None:
            canvas.delete(rectangle)
        rectangle = canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="#00d8ff", width=3, fill="#227799"
        )

    def drag(event: Any) -> None:
        if start is not None and rectangle is not None:
            canvas.coords(rectangle, start[0], start[1], event.x, event.y)

    def release(event: Any) -> None:
        nonlocal result
        if start is None:
            return
        left = max(0, min(start[0], event.x))
        top = max(0, min(start[1], event.y))
        right = min(monitor["width"], max(start[0], event.x))
        bottom = min(monitor["height"], max(start[1], event.y))
        if right - left >= 32 and bottom - top >= 32:
            result = _normalize_capture_region({
                "left": monitor["left"] + left,
                "top": monitor["top"] + top,
                "width": right - left,
                "height": bottom - top,
            })
            root.quit()

    root.bind("<Escape>", cancel)
    canvas.bind("<ButtonPress-1>", press)
    canvas.bind("<B1-Motion>", drag)
    canvas.bind("<ButtonRelease-1>", release)
    try:
        root.mainloop()
    finally:
        root.destroy()
    if result is None:
        raise RuntimeError("録画範囲の選択をキャンセルしました。")
    return result


def choose_capture_region(
    mode: str = "ask",
    *,
    monitor_index: int | None = None,
    explicit_region: dict[str, int] | None = None,
) -> tuple[dict[str, int], str]:
    """全画面、単一モニター、ドラッグ範囲から録画領域を決める。"""
    monitors = _available_monitors()
    if explicit_region is not None:
        region = _normalize_capture_region(explicit_region)
        return region, f"指定範囲 {region['width']}×{region['height']}"

    if mode == "ask":
        print("\n録画する範囲を選んでください:")
        print("  [1] すべての画面（既定）")
        print("  [2] モニターを1台選ぶ")
        print("  [3] マウスで範囲を指定する")
        try:
            choice = input("番号を入力してください [1]: ").strip() or "1"
        except (EOFError, KeyboardInterrupt) as exc:
            raise RuntimeError("録画範囲の選択をキャンセルしました。") from exc
        mode = {"1": "all", "2": "monitor", "3": "region"}.get(choice, "")
        if not mode:
            raise ValueError("録画範囲は1〜3の番号で選んでください。")

    if mode == "all":
        region = monitors[0]
        return region, f"すべての画面 {region['width']}×{region['height']}"

    physical = monitors[1:]
    if monitor_index is not None:
        if not 1 <= monitor_index <= len(physical):
            raise ValueError("--monitor-index の番号が正しくありません。")
        monitor = physical[monitor_index - 1]
    else:
        monitor = _choose_monitor(monitors, "録画する" if mode == "monitor" else "範囲指定する")

    if mode == "monitor":
        index = physical.index(monitor) + 1
        return monitor, f"モニター{index} {monitor['width']}×{monitor['height']}"
    if mode == "region":
        region = _select_region_with_mouse(monitor)
        return region, f"指定範囲 {region['width']}×{region['height']}"
    raise ValueError(f"未対応の録画範囲です: {mode}")


class DesktopRecorder:
    """MSSで指定画面を取り込み、OpenCVで動画保存する。"""

    def __init__(
        self,
        output_base: Path,
        fps: float = 10.0,
        capture_region: dict[str, int] | None = None,
    ):
        self.output_base = output_base
        self.fps = fps
        self.output_path: Path | None = None
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.writer: Any = None
        self.monitor: dict[str, int] | None = None
        self.width = 0
        self.height = 0
        self.frames_written = 0
        self.error: Exception | None = None
        self.requested_region = capture_region

    def _open_writer(self, width: int, height: int) -> tuple[Any, Path]:
        candidates = [
            (self.output_base.with_suffix(".mp4"), "mp4v"),
            (self.output_base.with_suffix(".avi"), "MJPG"),
        ]
        for path, codec in candidates:
            writer = cv2.VideoWriter(
                os.fspath(path),
                cv2.VideoWriter_fourcc(*codec),
                self.fps,
                (width, height),
            )
            if writer.isOpened():
                return writer, path
            writer.release()
        raise RuntimeError("動画エンコーダーを開始できませんでした。")

    def start(self) -> Path:
        _require_recording_dependencies()
        self.output_base.parent.mkdir(parents=True, exist_ok=True)
        if self.requested_region is None:
            with mss.mss() as capture:
                monitor = dict(capture.monitors[0])
        else:
            monitor = dict(self.requested_region)
        monitor = _normalize_capture_region(monitor)
        self.width = monitor["width"]
        self.height = monitor["height"]
        self.monitor = monitor
        self.writer, self.output_path = self._open_writer(self.width, self.height)
        self.stop_event.clear()
        self.frames_written = 0
        self.error = None
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()
        return self.output_path

    def _worker(self) -> None:
        frame_interval = 1.0 / self.fps
        next_frame_at = time.perf_counter()
        try:
            with mss.mss() as capture:
                while not self.stop_event.is_set():
                    shot = capture.grab(self.monitor)
                    frame = np.asarray(shot, dtype=np.uint8)[:self.height, :self.width, :3]
                    self.writer.write(frame)
                    self.frames_written += 1
                    next_frame_at += frame_interval
                    delay = max(0.0, next_frame_at - time.perf_counter())
                    self.stop_event.wait(delay)
        except Exception as exc:
            self.error = exc
            self.stop_event.set()
        finally:
            if self.writer is not None:
                self.writer.release()

    def stop(self) -> Path:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=10)
        if self.thread is not None and self.thread.is_alive():
            raise RuntimeError("画面録画の停止がタイムアウトしました。")
        if self.error is not None:
            raise RuntimeError(f"画面録画中にエラーが発生しました: {self.error}")
        if self.output_path is None or self.frames_written == 0:
            raise RuntimeError("画面を録画できませんでした。")
        return self.output_path


class SystemAudioRecorder:
    """Windows WASAPIループバックでPCから再生される音をWAVへ保存する。"""

    chunk_size = 1024

    def __init__(self, output_path: Path):
        self.output_path = output_path
        self.pa: Any = None
        self.stream: Any = None
        self.wave_file: Any = None
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.error: Exception | None = None
        self.bytes_written = 0
        self.device_name = ""

    def _find_loopback_device(self) -> dict[str, Any]:
        import pyaudiowpatch as pyaudio

        self.pa = pyaudio.PyAudio()
        try:
            wasapi = self.pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        except OSError as exc:
            raise RuntimeError("Windows WASAPIを利用できません。") from exc

        default_output = self.pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
        if default_output.get("isLoopbackDevice", False):
            return default_output

        default_name = default_output.get("name", "")
        for index in range(self.pa.get_device_count()):
            device = self.pa.get_device_info_by_index(index)
            if (
                device.get("isLoopbackDevice", False)
                and device.get("name", "").startswith(default_name[:30])
            ):
                return device
        try:
            return self.pa.get_wasapi_loopback_analogue_by_dict(default_output)
        except Exception as exc:
            raise RuntimeError(
                f"PC音声の録音デバイスが見つかりません: {default_name}"
            ) from exc

    def start(self) -> Path:
        import pyaudiowpatch as pyaudio

        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        device = self._find_loopback_device()
        rate = int(device.get("defaultSampleRate", 48000))
        channels = max(1, int(device.get("maxInputChannels", 2)))
        self.device_name = str(device.get("name", "WASAPI loopback"))
        try:
            self.stream = self.pa.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=device["index"],
                frames_per_buffer=self.chunk_size,
            )
            self.wave_file = wave.open(os.fspath(self.output_path), "wb")
            self.wave_file.setnchannels(channels)
            self.wave_file.setsampwidth(2)
            self.wave_file.setframerate(rate)
        except Exception:
            self.close()
            raise

        self.stop_event.clear()
        self.error = None
        self.bytes_written = 0
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()
        return self.output_path

    def _worker(self) -> None:
        try:
            while not self.stop_event.is_set():
                data = self.stream.read(self.chunk_size, exception_on_overflow=False)
                self.wave_file.writeframesraw(data)
                self.bytes_written += len(data)
        except Exception as exc:
            if not self.stop_event.is_set():
                self.error = exc
        finally:
            self.close()

    def stop(self) -> bool:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=5)
        if self.thread is not None and self.thread.is_alive():
            self.close()
            raise RuntimeError("PC音声録音の停止がタイムアウトしました。")
        if self.error is not None:
            raise RuntimeError(f"PC音声の録音中にエラーが発生しました: {self.error}")
        recorded = self.bytes_written > 0 and self.output_path.is_file()
        self.close()
        return recorded

    def close(self) -> None:
        if self.stream is not None:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        if self.wave_file is not None:
            try:
                self.wave_file.close()
            except Exception:
                pass
            self.wave_file = None
        if self.pa is not None:
            try:
                self.pa.terminate()
            except Exception:
                pass
            self.pa = None


def _default_output_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "output" / "local_recordings"
    return Path(__file__).resolve().parent / "output" / "local_recordings"


def record_and_analyze(
    output_root: Path,
    fps: float = 10.0,
    capture_mode: str = "ask",
    monitor_index: int | None = None,
    capture_region: dict[str, int] | None = None,
) -> dict[str, Any]:
    _require_recording_dependencies()
    session_name = "録画_" + dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    session_dir = output_root.expanduser().resolve() / session_name
    session_dir.mkdir(parents=True, exist_ok=False)

    selected_region, region_label = choose_capture_region(
        capture_mode,
        monitor_index=monitor_index,
        explicit_region=capture_region,
    )
    video = DesktopRecorder(session_dir / "画面録画", fps=fps, capture_region=selected_region)
    audio = SystemAudioRecorder(session_dir / "PC音声.wav")

    print("=" * 64)
    print("  画面録画 + 高精度AI解析")
    print("=" * 64)
    print(f"  ・録画範囲: {region_label}")
    print("  ・選択した画面範囲とPC再生音を録画します")
    print("  ・通知、チャット、パスワード画面も映る可能性があります")
    print("  ・停止後、音声と候補画像だけをGeminiへ送る前に確認します")
    print("  ・元の録画動画全体は送信しません")
    print(f"  ・保存先: {session_dir}")
    print()
    try:
        input("準備ができたら Enter キーで録画開始...")
    except (KeyboardInterrupt, EOFError) as exc:
        raise RuntimeError("録画をキャンセルしました。") from exc

    video_path: Path | None = None
    audio_ok = False
    started_at = time.monotonic()
    try:
        video_path = video.start()
        try:
            audio.start()
            print(f"PC音声: {audio.device_name}")
        except Exception as exc:
            print(f"警告: PC音声を開始できませんでした: {exc}", file=sys.stderr)
            print("画面のみ録画します。文字起こしは省略されます。", file=sys.stderr)
        print("\n● 録画中です。停止するには Enter キーを押してください。")
        try:
            input()
        except (KeyboardInterrupt, EOFError):
            pass
    finally:
        try:
            audio_ok = audio.stop()
        except Exception as exc:
            print(f"警告: PC音声の保存に失敗しました: {exc}", file=sys.stderr)
            audio.close()
        if video.output_path is not None:
            video_path = video.stop()

    duration = time.monotonic() - started_at
    if video_path is None or not video_path.is_file():
        raise RuntimeError("録画動画が作成されませんでした。")
    if duration < 0.5:
        raise RuntimeError("録画時間が短すぎます。1秒以上録画してください。")

    print(f"\n録画完了: {duration:.1f}秒")
    print("続けてシーン抽出と文字起こしを開始します。")
    analysis_dir = session_dir / "AI解析結果"
    if not audio_ok:
        raise RuntimeError("高精度文字起こしに必要なPC音声を録音できませんでした。")
    from gemini_hybrid_analyzer import analyze_with_gemini

    result = analyze_with_gemini(
        video_path,
        audio_path=audio.output_path,
        output_dir=analysis_dir,
        require_consent=True,
    )
    result.update({
        "recording_dir": os.fspath(session_dir),
        "video": os.fspath(video_path),
        "audio": os.fspath(audio.output_path) if audio_ok else None,
    })
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="画面とPC音声を録画して高精度AI解析します。")
    parser.add_argument("--output-root", type=Path, default=_default_output_root())
    parser.add_argument("--fps", type=float, default=10.0, help="画面録画fps（デフォルト: 10）")
    parser.add_argument(
        "--capture",
        choices=["ask", "all", "monitor", "region"],
        default="ask",
        help="録画範囲の選び方（デフォルト: ask）",
    )
    parser.add_argument("--monitor-index", type=int, help="録画するモニター番号（1から開始）")
    parser.add_argument(
        "--region",
        help="録画座標 left,top,width,height（指定時は選択画面を省略）",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.fps <= 0 or args.fps > 30:
        print("エラー: --fps は0より大きく30以下で指定してください。", file=sys.stderr)
        return 2
    capture_region = None
    if args.region:
        try:
            left, top, width, height = (int(value.strip()) for value in args.region.split(","))
            capture_region = {
                "left": left,
                "top": top,
                "width": width,
                "height": height,
            }
        except (ValueError, TypeError):
            print(
                "エラー: --region は left,top,width,height の形式で指定してください。",
                file=sys.stderr,
            )
            return 2
    try:
        result = record_and_analyze(
            args.output_root,
            fps=args.fps,
            capture_mode=args.capture,
            monitor_index=args.monitor_index,
            capture_region=capture_region,
        )
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"\nエラー: {exc}", file=sys.stderr)
        return 1
    print("\n" + "=" * 64)
    print("  録画から解析まで完了しました")
    print(f"  結果: {result['output_dir']}")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
