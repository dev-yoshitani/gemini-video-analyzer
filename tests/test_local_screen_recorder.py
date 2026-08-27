import tempfile
import threading
import unittest
import wave
from pathlib import Path
from unittest import mock

import local_screen_recorder as recorder


class SystemAudioRecorderTests(unittest.TestCase):
    def test_pc_audio_preflight_accepts_detected_sound_and_deletes_test_file(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            output_path = Path(temporary) / "音声テスト.wav"
            fake_recorder = mock.Mock()
            fake_recorder.device_name = "テスト音声"
            fake_recorder.peak_level = 0.25
            fake_recorder.stop.return_value = True

            with (
                mock.patch.object(recorder, "SystemAudioRecorder", return_value=fake_recorder),
                mock.patch.object(recorder.time, "sleep"),
            ):
                device, level = recorder.test_pc_audio(output_path)

            self.assertEqual(device, "テスト音声")
            self.assertEqual(level, 0.25)
            fake_recorder.start.assert_called_once()
            fake_recorder.stop.assert_called_once()
            fake_recorder.close.assert_called_once()

    def test_pc_audio_preflight_rejects_silence(self):
        fake_recorder = mock.Mock()
        fake_recorder.device_name = "テスト音声"
        fake_recorder.peak_level = 0.0
        fake_recorder.stop.return_value = True
        with (
            tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary,
            mock.patch.object(recorder, "SystemAudioRecorder", return_value=fake_recorder),
            mock.patch.object(recorder.time, "sleep"),
        ):
            with self.assertRaisesRegex(RuntimeError, "PC音声を検出できません"):
                recorder.test_pc_audio(Path(temporary) / "音声テスト.wav")

    def test_saved_wav_is_usable_after_a_stop_warning(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            output_path = Path(temporary) / "PC音声.wav"
            with wave.open(str(output_path), "wb") as saved_audio:
                saved_audio.setnchannels(1)
                saved_audio.setsampwidth(2)
                saved_audio.setframerate(48000)
                saved_audio.writeframes(b"\x00\x00" * 100)

            self.assertTrue(recorder._is_usable_wav(output_path))

    def test_stop_unblocks_audio_read_before_waiting_for_worker(self):
        class BlockingStream:
            def __init__(self):
                self.released = threading.Event()
                self.stopped = False

            def read(self, _size, exception_on_overflow=False):
                self.released.wait(timeout=2)
                return b"\x00\x00" * 4

            def stop_stream(self):
                self.stopped = True
                self.released.set()

            def close(self):
                self.released.set()

        class FakeWave:
            def writeframesraw(self, _data):
                pass

            def close(self):
                pass

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            output_path = Path(temporary) / "PC音声.wav"
            output_path.write_bytes(b"RIFF" + b"\x00" * 48)
            audio = recorder.SystemAudioRecorder(output_path)
            stream = BlockingStream()
            audio.stream = stream
            audio.wave_file = FakeWave()
            audio.thread = threading.Thread(target=audio._worker, daemon=True)
            audio.thread.start()

            self.assertTrue(audio.stop())
            self.assertTrue(stream.stopped)
            self.assertFalse(audio.thread.is_alive())


class RecordingWorkflowTests(unittest.TestCase):
    def test_real_audio_start_failure_cancels_and_removes_partial_recording(self):
        class FakeVideo:
            def __init__(self, output_path, **_kwargs):
                self.output_path = Path(output_path).with_suffix(".mp4")

            def start(self):
                self.output_path.write_bytes(b"partial video")
                return self.output_path

            def stop(self):
                return self.output_path

        class FakeAudio:
            def __init__(self, output_path):
                self.output_path = Path(output_path)
                self.device_name = ""

            def start(self):
                raise OSError("device became unavailable")

            def close(self):
                pass

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            output_root = Path(temporary) / "recordings"
            with (
                mock.patch.object(recorder, "_require_recording_dependencies"),
                mock.patch.object(
                    recorder,
                    "choose_capture_region",
                    return_value=(
                        {"left": 0, "top": 0, "width": 320, "height": 180},
                        "テスト範囲",
                    ),
                ),
                mock.patch.object(
                    recorder,
                    "test_pc_audio",
                    return_value=("テスト音声", 0.25),
                ),
                mock.patch.object(recorder, "DesktopRecorder", FakeVideo),
                mock.patch.object(recorder, "SystemAudioRecorder", FakeAudio),
                mock.patch("builtins.input", return_value=""),
            ):
                with self.assertRaisesRegex(RuntimeError, "画面録画を開始せず中止"):
                    recorder.record_and_analyze(output_root)

            self.assertEqual(list(output_root.iterdir()), [])


@unittest.skipIf(recorder.cv2 is None or recorder.np is None, "OpenCV is not installed")
class DesktopRecorderTests(unittest.TestCase):
    def test_capture_region_is_normalized_to_even_dimensions(self):
        region = recorder._normalize_capture_region({
            "left": 11,
            "top": 13,
            "width": 321,
            "height": 181,
        })
        self.assertEqual(region, {"left": 11, "top": 13, "width": 320, "height": 180})

    def test_user_can_choose_a_single_monitor(self):
        monitors = [
            {"left": 0, "top": 0, "width": 3200, "height": 1080},
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": 1920, "top": 0, "width": 1280, "height": 1024},
        ]
        with (
            mock.patch.object(recorder, "_available_monitors", return_value=monitors),
            mock.patch("builtins.input", side_effect=["2", "2"]),
        ):
            region, label = recorder.choose_capture_region("ask")
        self.assertEqual(region, monitors[2])
        self.assertIn("モニター2", label)

    def test_opens_a_local_video_writer_without_capturing_the_screen(self):
        # 現在のプロジェクト配下（日本語パスを含む）でも動画保存できることを確認する。
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            desktop = recorder.DesktopRecorder(Path(temporary) / "screen", fps=10.0)
            writer, output_path = desktop._open_writer(320, 180)
            try:
                frame = recorder.np.zeros((180, 320, 3), dtype=recorder.np.uint8)
                writer.write(frame)
            finally:
                writer.release()

            self.assertTrue(output_path.is_file())
            self.assertGreater(output_path.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
