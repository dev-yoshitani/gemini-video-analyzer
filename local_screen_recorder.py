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
import tempfile
import shutil
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


def _require_recording_dependencies(language: str = "ja") -> None:
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
        prefix = "Missing recording libraries: " if language == "en" else "録画用ライブラリがありません: "
        raise RuntimeError(
            prefix
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


def _available_monitors(language: str = "ja") -> list[dict[str, int]]:
    _require_recording_dependencies(language)
    with mss.mss() as capture:
        return [_normalize_capture_region(dict(item)) for item in capture.monitors]


def _choose_monitor(monitors: list[dict[str, int]], purpose: str, language: str = "ja") -> dict[str, int]:
    physical = monitors[1:]
    if len(physical) == 1:
        return physical[0]
    english = language == "en"
    print(f"\nChoose a monitor to {purpose}:" if english else f"\n{purpose}モニターを選んでください:")
    for index, monitor in enumerate(physical, 1):
        if english:
            print(
                f"  [{index}] Monitor {index}: {monitor['width']}×{monitor['height']} "
                f"(position {monitor['left']}, {monitor['top']})"
            )
        else:
            print(
                f"  [{index}] モニター{index}: {monitor['width']}×{monitor['height']} "
                f"(位置 {monitor['left']}, {monitor['top']})"
            )
    try:
        choice = input("Enter a number: " if english else "番号を入力してください: ").strip()
    except (EOFError, KeyboardInterrupt) as exc:
        raise RuntimeError("Recording-area selection was cancelled." if english else "録画範囲の選択をキャンセルしました。") from exc
    if not choice.isdigit() or not 1 <= int(choice) <= len(physical):
        raise ValueError("The monitor number is invalid." if english else "モニター番号が正しくありません。")
    return physical[int(choice) - 1]


def _select_region_with_mouse(monitor: dict[str, int], language: str = "ja") -> dict[str, int]:
    """選択したモニター上でドラッグされた矩形を返す。"""
    try:
        import tkinter as tk
    except ImportError as exc:
        raise RuntimeError(
            "Could not open the region-selection screen. Tkinter is required."
            if language == "en"
            else "範囲選択画面を開始できません。Tkinterが必要です。"
        ) from exc

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
        text=(
            "Drag to select the area to record (Esc to cancel)"
            if language == "en"
            else "録画したい範囲をドラッグしてください（Escでキャンセル）"
        ),
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
        raise RuntimeError("Recording-area selection was cancelled." if language == "en" else "録画範囲の選択をキャンセルしました。")
    return result


def choose_capture_region(
    mode: str = "ask",
    *,
    monitor_index: int | None = None,
    explicit_region: dict[str, int] | None = None,
    language: str = "ja",
) -> tuple[dict[str, int], str]:
    """全画面、単一モニター、ドラッグ範囲から録画領域を決める。"""
    english = language == "en"
    monitors = _available_monitors(language)
    if explicit_region is not None:
        region = _normalize_capture_region(explicit_region)
        return (
            region,
            f"Custom region {region['width']}×{region['height']}"
            if english
            else f"指定範囲 {region['width']}×{region['height']}",
        )

    if mode == "ask":
        if english:
            print("\nChoose what to record:")
            print("  [1] All screens (default)")
            print("  [2] One monitor")
            print("  [3] A custom area with the mouse")
        else:
            print("\n録画する範囲を選んでください:")
            print("  [1] すべての画面（既定）")
            print("  [2] モニターを1台選ぶ")
            print("  [3] マウスで範囲を指定する")
        try:
            choice = input("Enter a number [1]: " if english else "番号を入力してください [1]: ").strip() or "1"
        except (EOFError, KeyboardInterrupt) as exc:
            raise RuntimeError("Recording-area selection was cancelled." if english else "録画範囲の選択をキャンセルしました。") from exc
        mode = {"1": "all", "2": "monitor", "3": "region"}.get(choice, "")
        if not mode:
            raise ValueError("Choose a number from 1 to 3." if english else "録画範囲は1〜3の番号で選んでください。")

    if mode == "all":
        region = monitors[0]
        return (
            region,
            f"All screens {region['width']}×{region['height']}"
            if english
            else f"すべての画面 {region['width']}×{region['height']}",
        )

    physical = monitors[1:]
    if monitor_index is not None:
        if not 1 <= monitor_index <= len(physical):
            raise ValueError("The --monitor-index value is invalid." if english else "--monitor-index の番号が正しくありません。")
        monitor = physical[monitor_index - 1]
    else:
        if english:
            purpose = "record" if mode == "monitor" else "select a region on"
        else:
            purpose = "録画する" if mode == "monitor" else "範囲指定する"
        monitor = _choose_monitor(monitors, purpose, language)

    if mode == "monitor":
        index = physical.index(monitor) + 1
        return (
            monitor,
            f"Monitor {index} {monitor['width']}×{monitor['height']}"
            if english
            else f"モニター{index} {monitor['width']}×{monitor['height']}",
        )
    if mode == "region":
        region = _select_region_with_mouse(monitor, language)
        return (
            region,
            f"Custom region {region['width']}×{region['height']}"
            if english
            else f"指定範囲 {region['width']}×{region['height']}",
        )
    raise ValueError(f"Unsupported recording area: {mode}" if english else f"未対応の録画範囲です: {mode}")


class DesktopRecorder:
    """MSSで指定画面を取り込み、OpenCVで動画保存する。"""

    def __init__(
        self,
        output_base: Path,
        fps: float = 10.0,
        capture_region: dict[str, int] | None = None,
        language: str = "ja",
        segment_seconds: float = 60.0,
        pause_event=None,
    ):
        self.output_base = output_base
        self.segment_seconds = segment_seconds
        self.pause_event = pause_event or threading.Event()
        self.parts = []
        self.parts_dir = output_base.parent / ".recording_parts"
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
        self.language = "en" if language == "en" else "ja"

    def _open_writer(self, width: int, height: int, base=None) -> tuple[Any, Path]:
        base = base or self.output_base
        candidates = [
            (base.with_suffix(".mp4"), "mp4v"),
            (base.with_suffix(".avi"), "MJPG"),
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
        raise RuntimeError(
            "Could not start a video encoder."
            if self.language == "en"
            else "動画エンコーダーを開始できませんでした。"
        )

    def start(self) -> Path:
        _require_recording_dependencies(self.language)
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
        self.parts_dir.mkdir(parents=True, exist_ok=True)
        self.writer, self.chunk_path = self._open_writer(self.width, self.height, self.parts_dir / "video_00000")
        self.output_path = self.output_base.with_suffix(".mp4")
        self.stop_event.clear()
        self.frames_written = 0
        self.error = None
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()
        return self.output_path

    def _worker(self) -> None:
        from recording_recovery import save_parts
        frame_interval = 1.0 / self.fps
        next_frame_at = time.perf_counter()
        chunk_frames = 0
        try:
            with mss.mss() as capture:
                while not self.stop_event.is_set():
                    if self.pause_event.is_set():
                        self.stop_event.wait(0.03)
                        next_frame_at = time.perf_counter()
                        continue
                    shot = capture.grab(self.monitor)
                    frame = np.asarray(shot, dtype=np.uint8)[:self.height, :self.width, :3]
                    # Maintain wall-clock duration under capture/encoder lag instead of
                    # speeding up the video relative to the audio.
                    due = max(1, int((time.perf_counter() - next_frame_at) / frame_interval) + 1)
                    for _ in range(due):
                        if self.stop_event.is_set():
                            break
                        self.writer.write(frame)
                        self.frames_written += 1
                        chunk_frames += 1
                        if chunk_frames >= max(1, round(self.segment_seconds * self.fps)):
                            self.writer.release()
                            self.parts.append(self.chunk_path)
                            chunk_frames = 0
                            save_parts(self.parts_dir, "video", self.parts, fps=self.fps)
                            self.writer, self.chunk_path = self._open_writer(
                                self.width, self.height, self.parts_dir / f"video_{len(self.parts):05d}")
                        next_frame_at += frame_interval
                    delay = max(0.0, next_frame_at - time.perf_counter())
                    self.stop_event.wait(delay)
        except Exception as exc:
            self.error = exc
            self.stop_event.set()
        finally:
            if self.writer is not None:
                self.writer.release()
                if chunk_frames:
                    self.parts.append(self.chunk_path)
                    save_parts(self.parts_dir, "video", self.parts, fps=self.fps)

    def stop(self) -> Path:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=10)
        if self.thread is not None and self.thread.is_alive():
            raise RuntimeError(
                "Timed out while stopping screen recording."
                if self.language == "en"
                else "画面録画の停止がタイムアウトしました。"
            )
        if self.error is not None:
            raise RuntimeError(
                f"Screen recording failed: {self.error}"
                if self.language == "en"
                else f"画面録画中にエラーが発生しました: {self.error}"
            )
        if self.output_path is None or self.frames_written == 0:
            raise RuntimeError(
                "No screen frames were recorded."
                if self.language == "en"
                else "画面を録画できませんでした。"
            )
        from recording_recovery import merge_video
        return merge_video(self.parts, self.output_path, self.fps)


def _bounded_driver_call(action, timeout=2.0):
    """A stuck audio driver must not block the application's stop path forever."""
    def run():
        try:
            action()
        except Exception:
            pass
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout)
    return not thread.is_alive()


class SystemAudioRecorder:
    """Windows WASAPIループバックでPCから再生される音をWAVへ保存する。"""

    chunk_size = 1024

    def __init__(self, output_path: Path, language: str = "ja", segment_seconds=60, pause_event=None):
        self.output_path = output_path
        self.segment_seconds = segment_seconds
        self.pause_event = pause_event or threading.Event()
        self.parts = []
        self.parts_dir = output_path.parent / ".recording_parts"
        self.current_level = 0.0
        self.last_sound_at = time.monotonic()
        self.pa: Any = None
        self.stream: Any = None
        self.wave_file: Any = None
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.error: Exception | None = None
        self.bytes_written = 0
        self.peak_level = 0.0
        self.device_name = ""
        self.language = "en" if language == "en" else "ja"

    def _find_loopback_device(self) -> dict[str, Any]:
        import pyaudiowpatch as pyaudio

        self.pa = pyaudio.PyAudio()
        try:
            wasapi = self.pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        except OSError as exc:
            raise RuntimeError("Windows WASAPI is unavailable." if self.language == "en" else "Windows WASAPIを利用できません。") from exc

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
                f"Could not find a PC-audio recording device: {default_name}"
                if self.language == "en"
                else f"PC音声の録音デバイスが見つかりません: {default_name}"
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
            self.rate, self.channels = rate, channels
            self.parts_dir.mkdir(parents=True, exist_ok=True)
            self.chunk_path = self.parts_dir / "audio_00000.wav"
            self.wave_file = wave.open(os.fspath(self.chunk_path), "wb")
            self.wave_file.setnchannels(channels)
            self.wave_file.setsampwidth(2)
            self.wave_file.setframerate(rate)
        except Exception:
            self.close()
            raise

        self.stop_event.clear()
        self.error = None
        self.bytes_written = 0
        self.peak_level = 0.0
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()
        return self.output_path

    def _worker(self) -> None:
        from recording_recovery import save_parts
        chunk_bytes = 0
        try:
            while not self.stop_event.is_set():
                data = self.stream.read(self.chunk_size, exception_on_overflow=False)
                if self.pause_event.is_set():
                    self.current_level = 0.0
                    continue
                self.wave_file.writeframesraw(data)
                self.bytes_written += len(data)
                chunk_bytes += len(data)
                samples = np.frombuffer(data, dtype=np.int16)
                if samples.size:
                    peak = float(np.max(np.abs(samples.astype(np.int32)))) / 32768.0
                    self.peak_level = max(self.peak_level, peak)
                    self.current_level = peak
                    if peak >= 0.001:
                        self.last_sound_at = time.monotonic()
                if hasattr(self, "rate") and chunk_bytes >= self.segment_seconds * self.rate * self.channels * 2:
                    self.wave_file.close()
                    self.parts.append(self.chunk_path)
                    chunk_bytes = 0
                    save_parts(self.parts_dir, "audio", self.parts)
                    self.chunk_path = self.parts_dir / f"audio_{len(self.parts):05d}.wav"
                    self.wave_file = wave.open(os.fspath(self.chunk_path), "wb")
                    self.wave_file.setnchannels(self.channels)
                    self.wave_file.setsampwidth(2)
                    self.wave_file.setframerate(self.rate)
                    chunk_bytes = 0
        except Exception as exc:
            if not self.stop_event.is_set():
                self.error = exc
        finally:
            if hasattr(self, "chunk_path") and self.wave_file is not None:
                self.wave_file.close()
                self.wave_file = None
                if chunk_bytes:
                    self.parts.append(self.chunk_path)
                    save_parts(self.parts_dir, "audio", self.parts)

    def stop(self) -> bool:
        self.stop_event.set()
        # WASAPI の read() が待機中だと、先に join() しても録音スレッドは
        # 終了できない。まずストリームを停止して read() を解除してから待つ。
        if self.stream is not None:
            _bounded_driver_call(self.stream.stop_stream)
        if self.thread is not None:
            self.thread.join(timeout=5)
        if self.thread is not None and self.thread.is_alive():
            # 一部の音声ドライバーは stop_stream() だけでは解除されないため、
            # ストリームを閉じてもう一度だけ終了を待つ。
            if self.stream is not None:
                _bounded_driver_call(self.stream.close)
            self.thread.join(timeout=2)
        if self.thread is not None and self.thread.is_alive():
            # Never close a WAV still owned by the blocked worker. Finalized chunks
            # remain recoverable, even if the current chunk cannot be finalized.
            raise RuntimeError(
                "Timed out while stopping PC-audio recording."
                if self.language == "en"
                else "PC音声録音の停止がタイムアウトしました。"
            )
        if self.error is not None:
            self.close()
            raise RuntimeError(
                f"PC-audio recording failed: {self.error}"
                if self.language == "en"
                else f"PC音声の録音中にエラーが発生しました: {self.error}"
            )
        if self.parts:
            from recording_recovery import merge_audio
            merge_audio(self.parts, self.output_path)
        recorded = self.bytes_written > 0 and self.output_path.is_file()
        self.close()
        return recorded

    def close(self) -> None:
        if self.thread is not None and self.thread.is_alive():
            return
        if self.stream is not None:
            _bounded_driver_call(self.stream.stop_stream)
            _bounded_driver_call(self.stream.close)
            self.stream = None
        if self.wave_file is not None:
            try:
                self.wave_file.close()
            except Exception:
                pass
            self.wave_file = None
        if self.pa is not None:
            _bounded_driver_call(self.pa.terminate)
            self.pa = None


def _is_usable_wav(path: Path) -> bool:
    """停止処理で警告が出ても、正常に保存済みのWAVなら再利用できる。"""
    try:
        if not path.is_file() or path.stat().st_size <= 44:
            return False
        with wave.open(os.fspath(path), "rb") as saved_audio:
            return saved_audio.getnframes() > 0 and saved_audio.getframerate() > 0
    except (OSError, EOFError, wave.Error):
        return False


def test_pc_audio(
    output_path: Path,
    *,
    duration_seconds: float = 3.0,
    minimum_peak: float = 0.002,
    language: str = "ja",
) -> tuple[str, float]:
    """録画開始前にPC音声を短時間録音し、実際に音が入ることを確認する。"""
    english = language == "en"
    # Preflight chunks must never overwrite the real recording's recovery manifest.
    test_dir = Path(tempfile.mkdtemp(prefix="audio-test-", dir=output_path.parent))
    test_recorder = SystemAudioRecorder(test_dir / output_path.name, language=language)
    try:
        test_recorder.start()
        time.sleep(duration_seconds)
        recorded = test_recorder.stop()
        peak_level = test_recorder.peak_level
        if not recorded or peak_level < minimum_peak:
            raise RuntimeError(
                "No PC audio was detected. Play a video or music on the PC, "
                "raise the volume, and try again."
                if english
                else "PC音声を検出できませんでした。PCで動画や音楽を再生し、"
                "音量を上げてからもう一度お試しください。"
            )
        return test_recorder.device_name, peak_level
    finally:
        test_recorder.close()
        if test_dir.resolve().parent == output_path.parent.resolve():
            shutil.rmtree(test_dir)


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
    language: str = "ja",
    control=None,
    analysis_options=None,
) -> dict[str, Any]:
    english = language == "en"
    _require_recording_dependencies(language)
    session_name = (
        "Recording_" if english else "録画_"
    ) + dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    session_dir = output_root.expanduser().resolve() / session_name
    session_dir.mkdir(parents=True, exist_ok=False)

    selected_region, region_label = choose_capture_region(
        capture_mode,
        monitor_index=monitor_index,
        explicit_region=capture_region,
        language=language,
    )
    if english:
        video = DesktopRecorder(
            session_dir / "Screen Recording",
            fps=fps,
            capture_region=selected_region,
            language="en",
        )
        audio = SystemAudioRecorder(session_dir / "PC Audio.wav", language="en")
    else:
        video = DesktopRecorder(session_dir / "画面録画", fps=fps, capture_region=selected_region)
        audio = SystemAudioRecorder(session_dir / "PC音声.wav")
    if control is not None:
        video.pause_event = control.pause
        audio.pause_event = control.pause

    print("=" * 64)
    print("  Screen Recording + High-Accuracy AI Analysis" if english else "  画面録画 + 高精度AI解析")
    print("=" * 64)
    if english:
        print(f"  - Recording area: {region_label}")
        print("  - The selected screen area and PC playback audio will be recorded.")
        print("  - Notifications, chats, and password screens may be captured.")
        print("  - After recording, only PC audio and selected candidate images are sent to Gemini after confirmation.")
        print("  - The complete source recording is not uploaded.")
        print(f"  - Save location: {session_dir}")
    else:
        print(f"  ・録画範囲: {region_label}")
        print("  ・選択した画面範囲とPC再生音を録画します")
        print("  ・通知、チャット、パスワード画面も映る可能性があります")
        print("  ・停止後、音声と候補画像だけをGeminiへ送る前に確認します")
        print("  ・元の録画動画全体は送信しません")
        print(f"  ・保存先: {session_dir}")
    print()
    try:
        if control is None:
            input(
                "Start playing a video or music on the PC, then press Enter. "
                "Recording will begin after a 3-second audio test..."
                if english
                else "PCで動画や音楽を再生した状態にして、"
                "Enterキーを押してください（3秒間の音声テスト後に録画開始）..."
            )
    except (KeyboardInterrupt, EOFError) as exc:
        raise RuntimeError("Recording was cancelled." if english else "録画をキャンセルしました。") from exc

    print("\nTesting PC audio for 3 seconds..." if english else "\nPC音声を3秒間テスト中...")
    try:
        tested_device, peak_level = test_pc_audio(
            session_dir / ("Audio Test.wav" if english else "音声テスト.wav"),
            language=language,
        )
    except Exception:
        try:
            session_dir.rmdir()
        except OSError:
            pass
        raise
    print(
        f"PC audio test OK: {tested_device} (level {peak_level * 100:.1f}%)"
        if english
        else f"PC音声テスト OK: {tested_device}（レベル {peak_level * 100:.1f}%）"
    )

    video_path: Path | None = None
    audio_ok = False
    if control is not None:
        control.checkpoint()
    video_path = video.start()
    try:
        audio.start()
        print(f"PC audio: {audio.device_name}" if english else f"PC音声: {audio.device_name}")
    except Exception as exc:
        audio.close()
        try:
            if video.output_path is not None:
                video_path = video.stop()
        except Exception:
            pass
        finally:
            for partial_path in (video_path, audio.output_path):
                if partial_path is None:
                    continue
                try:
                    Path(partial_path).unlink()
                except FileNotFoundError:
                    pass
            try:
                session_dir.rmdir()
            except OSError:
                pass
        raise RuntimeError(
            "PC audio could not start, so screen recording was cancelled before it began."
            if english
            else "PC音声を開始できなかったため、画面録画を開始せず中止しました。"
        ) from exc

    started_at = time.monotonic()
    try:
        print(
            "\n● Recording. Press Enter to stop."
            if english
            else "\n● 録画中です。停止するには Enter キーを押してください。"
        )
        if control is None:
            try:
                input()
            except (KeyboardInterrupt, EOFError):
                pass
        else:
            while not control.stop.wait(0.2):
                if control.cancel.is_set():
                    break
                if video.error or audio.error:
                    raise RuntimeError(f"Recording device error / 録画デバイス異常: {video.error or audio.error}")
                control.emit(kind="recording", seconds=video.frames_written / fps,
                             level=audio.current_level, paused=control.pause.is_set(),
                             silent=not control.pause.is_set() and time.monotonic() - audio.last_sound_at > 15)
    finally:
        if control is not None:
            control.emit(kind="stage", stage="録画保存")
        try:
            audio_ok = audio.stop()
        except Exception as exc:
            audio.close()
            audio_ok = _is_usable_wav(audio.output_path)
            if audio_ok:
                print(
                    (
                        "Note: ending PC-audio recording had a problem, but the saved audio will be used: "
                        f"{exc}"
                    )
                    if english
                    else f"注意: 音声録音の終了処理で問題が発生しましたが、"
                    f"保存済みのPC音声を使用します: {exc}",
                    file=sys.stderr,
                )
            else:
                print(
                    f"Warning: failed to save PC audio: {exc}"
                    if english
                    else f"警告: PC音声の保存に失敗しました: {exc}",
                    file=sys.stderr,
                )
        if video.output_path is not None:
            video_path = video.stop()

    duration = time.monotonic() - started_at
    if video_path is None or not video_path.is_file():
        raise RuntimeError("The recording video was not created." if english else "録画動画が作成されませんでした。")
    if duration < 0.5:
        raise RuntimeError("The recording is too short. Record for at least one second." if english else "録画時間が短すぎます。1秒以上録画してください。")

    print(f"\nRecording complete: {duration:.1f} seconds" if english else f"\n録画完了: {duration:.1f}秒")
    print("Starting scene extraction and transcription." if english else "続けてシーン抽出と文字起こしを開始します。")
    analysis_dir = session_dir / ("Analysis Results" if english else "AI解析結果")
    if not audio_ok:
        raise RuntimeError(
            "The PC audio required for high-accuracy transcription was not recorded."
            if english
            else "高精度文字起こしに必要なPC音声を録音できませんでした。"
        )
    # Both final media files exist; only app-owned recovery chunks can be removed.
    parts_dir = (session_dir / ".recording_parts").resolve()
    if parts_dir.parent == session_dir.resolve() and parts_dir.is_dir():
        shutil.rmtree(parts_dir)
    if control is not None:
        control.emit(kind="saved_recording", video=os.fspath(video_path), audio=os.fspath(audio.output_path))
        control.checkpoint()
    from gemini_hybrid_analyzer import analyze_with_gemini

    result = analyze_with_gemini(
        video_path,
        audio_path=audio.output_path,
        output_dir=analysis_dir,
        require_consent=True,
        language=language,
        **(analysis_options or {}),
    )
    result.update({
        "recording_dir": os.fspath(session_dir),
        "video": os.fspath(video_path),
        "audio": os.fspath(audio.output_path) if audio_ok else None,
    })
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="画面とPC音声を録画して高精度AI解析します。")
    parser.add_argument(
        "--language",
        choices=("ja", "en"),
        default="ja",
        help="Report and interface language (default: ja)",
    )
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
    english = args.language == "en"
    if args.fps <= 0 or args.fps > 30:
        print(
            "Error: --fps must be greater than 0 and no greater than 30."
            if english
            else "エラー: --fps は0より大きく30以下で指定してください。",
            file=sys.stderr,
        )
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
                "Error: --region must use the format left,top,width,height."
                if english
                else "エラー: --region は left,top,width,height の形式で指定してください。",
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
            language=args.language,
        )
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"\nError: {exc}" if english else f"\nエラー: {exc}", file=sys.stderr)
        return 1
    print("\n" + "=" * 64)
    print("  Recording and analysis complete" if english else "  録画から解析まで完了しました")
    print(f"  Results: {result['output_dir']}" if english else f"  結果: {result['output_dir']}")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
