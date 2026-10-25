import tempfile
import json
import os
import subprocess
import sys
import threading
import time
import queue
import unittest
import wave
from pathlib import Path
from unittest import mock

import local_screen_recorder as recorder


class SystemAudioRecorderTests(unittest.TestCase):
    def test_resampler_initializes_in_cold_worker_with_command_pipe_open(self):
        child = r'''
import json, sys, tempfile, threading, wave
from pathlib import Path
from unittest import mock
import numpy as np
stop = threading.Event()
def command():
    sys.stdin.readline()
    stop.set()
threading.Thread(target=command, daemon=True).start()
import local_screen_recorder as recorder
with tempfile.TemporaryDirectory() as temp:
    audio = recorder.SystemAudioRecorder(Path(temp) / "audio.wav")
    driver = mock.Mock()
    audio.pa = driver
    def deliver():
        callback = driver.open.call_args.kwargs["stream_callback"]
        for _ in range(48):
            callback(np.full((1000, 2), 3000, dtype="<i2").tobytes(), 1000, {}, 0)
    driver.open.return_value.start_stream.side_effect = deliver
    with mock.patch.object(audio, "_find_loopback_device", return_value={
        "index": 1, "maxInputChannels": 2, "defaultSampleRate": 48000,
    }):
        audio.start()
    print("DSP_READY", flush=True)
    if not stop.wait(10):
        raise RuntimeError("No stop command")
    if not audio.stop():
        raise RuntimeError("No synthetic audio saved")
    with wave.open(str(audio.output_path), "rb") as saved:
        print(json.dumps([saved.getframerate(), saved.getnchannels(), saved.getnframes()]), flush=True)
'''
        executable = str(Path(sys.executable).with_name("python.exe")) if os.name == "nt" else sys.executable
        process = subprocess.Popen([executable, "-u", "-c", child], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                   encoding="utf-8", cwd=Path(recorder.__file__).parent,
                                   env=dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1"),
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        lines = queue.Queue()

        def read():
            for line in process.stdout:
                lines.put(line.strip())
            lines.put(None)

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        received = []
        try:
            deadline = time.monotonic() + 20
            while True:
                line = lines.get(timeout=max(.01, deadline - time.monotonic()))
                self.assertIsNotNone(line, received)
                received.append(line)
                if line == "DSP_READY":
                    break
                self.assertLess(time.monotonic(), deadline, received)
            process.stdin.write("stop\n")
            process.stdin.flush()
            self.assertEqual(json.loads(lines.get(timeout=10)), [16000, 1, 16000])
            self.assertEqual(process.wait(timeout=5), 0)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            process.stdin.close()
            reader.join(timeout=2)
            process.stdout.close()

    def _record_synthetic_audio(self, output, rate, samples, block_frames=1024, segment_seconds=60):
        audio = recorder.SystemAudioRecorder(output, segment_seconds=segment_seconds)
        driver = mock.Mock()
        audio.pa = driver
        channels = samples.shape[1]

        def deliver():
            callback = driver.open.call_args.kwargs["stream_callback"]
            for index in range(0, samples.shape[0], block_frames):
                data = samples[index:index + block_frames]
                self.assertEqual(callback(data.tobytes(), len(data), {}, 0), (None, 0))
                # Synthetic input arrives much faster than an actual sound device.
                # Let the writer catch up without changing the real callback.
                deadline = time.monotonic() + 5
                while audio.audio_queue.qsize() > 128:
                    self.assertLess(time.monotonic(), deadline)
                    self.assertFalse(audio.stop_event.wait(.001))

        driver.open.return_value.start_stream.side_effect = deliver
        with mock.patch.object(audio, "_find_loopback_device", return_value={
            "index": 17, "name": "Synthetic [Loopback]", "maxInputChannels": channels,
            "defaultSampleRate": rate,
        }):
            audio.start()
        self.assertTrue(audio.stop())
        self.assertEqual(driver.open.call_args.kwargs["rate"], rate)
        self.assertEqual(driver.open.call_args.kwargs["channels"], channels)
        with wave.open(str(output), "rb") as saved:
            params = saved.getnchannels(), saved.getsampwidth(), saved.getframerate(), saved.getnframes()
            pcm = recorder.np.frombuffer(saved.readframes(saved.getnframes()), dtype="<i2")
        return audio, params, pcm

    def test_recording_is_six_times_smaller_before_merge_and_preserves_speech_tone(self):
        np = recorder.np
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            root = Path(temporary)
            signal = (np.sin(2 * np.pi * 1000 * np.arange(48000) / 48000) * 12000).astype("<i2")
            stereo = np.column_stack((signal, signal))
            audio, params, pcm = self._record_synthetic_audio(root / "audio.wav", 48000, stereo,
                                                            segment_seconds=.2)
            self.assertEqual(params, (1, 2, 16000, 16000))
            self.assertEqual(audio.bytes_written, stereo.nbytes // 6)
            with wave.open(str(audio.parts[0]), "rb") as part:
                self.assertEqual((part.getnchannels(), part.getframerate()), (1, 16000))
            spectrum = np.abs(np.fft.rfft(pcm.astype(float)))
            frequency = np.fft.rfftfreq(len(pcm), 1 / 16000)[np.argmax(spectrum)]
            self.assertAlmostEqual(frequency, 1000, delta=1)
            self.assertGreater(np.max(np.abs(pcm.astype(np.int32))), 11000)
            self.assertFalse(audio.thread.is_alive())
            self.assertTrue(audio.audio_queue.empty())

    def test_fractional_resampling_has_no_duration_drift_across_callback_sizes(self):
        np = recorder.np
        for block in (101, 1024, 4096):
            with self.subTest(block=block), tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
                samples = np.full((44100, 2), 15000, dtype="<i2")
                audio, params, pcm = self._record_synthetic_audio(Path(temporary) / "audio.wav", 44100,
                                                                samples, block_frames=block)
                self.assertEqual(params, (1, 2, 16000, 16000))
                self.assertEqual(audio.bytes_written, 32000)
                self.assertTrue(np.all(pcm[64:-64] == 15000))

    def test_downmix_avoids_overflow_and_keeps_audio_from_one_channel(self):
        np = recorder.np
        for left, right, expected in ((32767, 32767, 32767), (0, 24000, 12000),
                                      (-32768, -32768, -32768)):
            with self.subTest(left=left, right=right), tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
                samples = np.tile(np.array([[left, right]], dtype="<i2"), (16000, 1))
                _, params, pcm = self._record_synthetic_audio(Path(temporary) / "audio.wav", 16000, samples)
                self.assertEqual(params, (1, 2, 16000, 16000))
                self.assertTrue(np.all(pcm == expected))

    def test_lower_native_rate_is_not_upscaled(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            samples = recorder.np.full((8000, 1), 12000, dtype="<i2")
            _, params, pcm = self._record_synthetic_audio(Path(temporary) / "audio.wav", 8000, samples)
            self.assertEqual(params, (1, 2, 8000, 8000))
            self.assertTrue(recorder.np.all(pcm == 12000))

    def test_downsampling_filters_high_frequencies_instead_of_aliasing_them(self):
        np = recorder.np
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            signal = (np.sin(2 * np.pi * 12000 * np.arange(48000) / 48000) * 12000).astype("<i2")
            _, _, pcm = self._record_synthetic_audio(Path(temporary) / "audio.wav", 48000,
                                                    np.column_stack((signal, signal)))
            rms = float(np.sqrt(np.mean(pcm[256:-256].astype(float) ** 2)))
            self.assertLess(rms, 100)

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

    def _callback_recorder(self, output_path):
        audio = recorder.SystemAudioRecorder(output_path)
        audio._callback_continue = 0
        audio._callback_complete = 1
        audio._callback_abort = 2
        audio._input_overflow = 2
        return audio

    def _prepare_wave(self, audio):
        audio.rate, audio.channels = 48000, 1
        audio.parts_dir.mkdir()
        audio.chunk_path = audio.parts_dir / "audio_00000.wav"
        audio.wave_file = wave.open(str(audio.chunk_path), "wb")
        audio.wave_file.setparams((1, 2, 48000, 0, "NONE", "not compressed"))

    def test_stop_drains_callback_audio_without_blocking_driver_read(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            output_path = Path(temporary) / "PC音声.wav"
            audio = self._callback_recorder(output_path)
            self._prepare_wave(audio)
            stream = mock.Mock()
            audio.stream = stream
            data = b"\x01\x10" * 100
            for _ in range(10):
                self.assertEqual(audio._audio_callback(data, 100, {}, 0), (None, 0))
            audio.thread = threading.Thread(target=audio._worker, daemon=True)
            audio.thread.start()

            self.assertTrue(audio.stop())
            self.assertFalse(audio.thread.is_alive())
            with wave.open(str(output_path), "rb") as saved:
                self.assertEqual(saved.getnframes(), 1000)
                self.assertEqual(saved.readframes(1000), data * 10)
            self.assertGreater(audio.peak_level, 0)
            self.assertEqual(audio._audio_callback(data, 100, {}, 0), (None, 1))
            self.assertTrue(audio.audio_queue.empty())
            stream.read.assert_not_called()
            stream.stop_stream.assert_called()

    def test_start_uses_callback_and_initializes_writer_before_stream_starts(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            audio = recorder.SystemAudioRecorder(Path(temporary) / "audio.wav")
            driver = mock.Mock()
            stream = driver.open.return_value
            audio.pa = driver

            def deliver():
                self.assertIsNotNone(audio.wave_file)
                self.assertTrue(audio.thread.is_alive())
                driver.open.call_args.kwargs["stream_callback"](b"\x01\x00" * 480, 480, {}, 0)

            stream.start_stream.side_effect = deliver
            with mock.patch.object(audio, "_find_loopback_device", return_value={
                "index": 17, "name": "Earphones [Loopback]", "maxInputChannels": 1,
                "defaultSampleRate": 48000,
            }):
                audio.start()
            self.assertFalse(driver.open.call_args.kwargs["start"])
            self.assertEqual(driver.open.call_args.kwargs["input_device_index"], 17)
            self.assertEqual(driver.open.call_args.kwargs["rate"], 48000)
            self.assertEqual((audio.rate, audio.channels), (16000, 1))
            self.assertTrue(audio.stop())
            stream.read.assert_not_called()
            stream.start_stream.assert_called_once()
            driver.terminate.assert_called_once()

    def test_paused_audio_is_discarded_at_callback_time(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            audio = self._callback_recorder(Path(temporary) / "audio.wav")
            self._prepare_wave(audio)
            audio.pause_event.set()
            audio._audio_callback(b"\x02\x00" * 10, 10, {}, 0)
            audio.pause_event.clear()
            audio._audio_callback(b"\x01\x00" * 4, 4, {}, 0)
            # Pausing now must not discard samples received before the pause.
            audio.pause_event.set()
            audio.stop_event.set()
            audio._worker()
            self.assertTrue(audio.stop())
            with wave.open(str(audio.output_path), "rb") as saved:
                self.assertEqual(saved.readframes(100), b"\x01\x00" * 4)

    def test_callback_overflow_is_reported_and_preserves_received_audio(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            audio = self._callback_recorder(Path(temporary) / "audio.wav")
            self._prepare_wave(audio)
            audio.audio_queue = queue.Queue(maxsize=1)
            data = b"\x01\x00" * 4
            audio._audio_callback(data, 4, {}, 0)
            self.assertEqual(audio._audio_callback(data, 4, {}, 0), (None, 2))
            audio._worker()
            with self.assertRaisesRegex(RuntimeError, "保存が追いつかず"):
                audio.stop()
            self.assertEqual(len(audio.parts), 1)
            with wave.open(str(audio.parts[0]), "rb") as saved:
                self.assertEqual(saved.readframes(100), data)

    def test_input_overflow_is_not_silently_ignored(self):
        audio = self._callback_recorder(Path("unused.wav"))
        self.assertEqual(audio._audio_callback(b"\x01\x00", 1, {}, 2), (None, 2))
        self.assertIn("取りこぼし", str(audio.error))
        self.assertTrue(audio.stop_event.is_set())

    def test_no_audio_exits_without_fabricating_a_recording(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            audio = self._callback_recorder(Path(temporary) / "audio.wav")
            self._prepare_wave(audio)
            audio.stream = mock.Mock()
            audio.thread = threading.Thread(target=audio._worker, daemon=True)
            audio.thread.start()
            self.assertFalse(audio.stop())
            self.assertFalse(audio.thread.is_alive())
            self.assertFalse(audio.output_path.exists())
            self.assertEqual(audio.bytes_written, 0)

    def test_start_failure_stops_writer_and_closes_driver(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            audio = recorder.SystemAudioRecorder(Path(temporary) / "audio.wav")
            driver = mock.Mock()
            driver.open.return_value.start_stream.side_effect = OSError("device disconnected")
            audio.pa = driver
            with mock.patch.object(audio, "_find_loopback_device", return_value={
                "index": 17, "maxInputChannels": 1, "defaultSampleRate": 48000,
            }):
                with self.assertRaisesRegex(OSError, "disconnected"):
                    audio.start()
            self.assertFalse(audio.thread.is_alive())
            driver.open.return_value.close.assert_called_once()
            driver.terminate.assert_called_once()
            self.assertFalse(audio.parts_dir.exists())


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
                    side_effect=AssertionError("No preflight should run"),
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
