"""Tests for audio-only recording and transcription workflows."""
import json
import os
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

import desktop_app
import gemini_hybrid_analyzer as hybrid
import local_screen_recorder as recorder
from workflow_control import JobControl


def _create_dummy_wav(path: Path, duration_sec: float = 1.0, rate: int = 16000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        nframes = int(duration_sec * rate)
        wf.writeframes(b"\x00\x00" * nframes)
    return path


class AudioRecordingCoreTests(unittest.TestCase):
    def _setup_fake_audio(self, output_root):
        fake_audio = mock.Mock()
        fake_audio.device_name = "Mock Loopback"
        fake_audio.current_level = 0.5
        fake_audio.last_sound_at = 0.0
        fake_audio.bytes_written = 32000
        fake_audio.rate = 16000
        fake_audio.channels = 1
        fake_audio.error = None

        def init_fake(output_path, **kwargs):
            fake_audio.output_path = Path(output_path)
            return fake_audio

        def fake_stop():
            _create_dummy_wav(fake_audio.output_path, 1.0)
            return True

        fake_audio.stop.side_effect = fake_stop
        return fake_audio, init_fake

    def test_record_audio_clean_payload_without_pdf_key(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            output_root = Path(temp_dir)
            fake_audio, init_fake = self._setup_fake_audio(output_root)

            emitted = []
            control = JobControl(lambda **ev: emitted.append(ev))
            control.stop.set()  # Stop immediately

            clock = [0.0]

            def fake_clock():
                val = clock[0]
                clock[0] += 1.0
                return val

            with (
                mock.patch.object(recorder, "test_pc_audio", return_value=("Mock Loopback", 0.3)),
                mock.patch.object(recorder, "SystemAudioRecorder", side_effect=init_fake),
                mock.patch.object(recorder.time, "monotonic", side_effect=fake_clock),
            ):
                result = recorder.record_audio(output_root, language="ja", control=control)

            self.assertTrue(result["success"])
            self.assertEqual(result["mode"], "audio_only")
            self.assertIn("audio", result)
            self.assertIn("recording_dir", result)
            # Crucial requirement: No "pdf" key in audio-only mode
            self.assertNotIn("pdf", result)

    def test_record_audio_rejects_sub_second_recording(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            output_root = Path(temp_dir)
            fake_audio, init_fake = self._setup_fake_audio(output_root)

            def fake_short_stop():
                _create_dummy_wav(fake_audio.output_path, 0.2)
                return True

            fake_audio.stop.side_effect = fake_short_stop

            control = JobControl(lambda **ev: None)
            control.stop.set()

            # Time does not advance enough (< 0.5s)
            with (
                mock.patch.object(recorder, "test_pc_audio", return_value=("Mock Loopback", 0.3)),
                mock.patch.object(recorder, "SystemAudioRecorder", side_effect=init_fake),
                mock.patch.object(recorder.time, "monotonic", return_value=100.1),
            ):
                with self.assertRaisesRegex(RuntimeError, "短すぎます"):
                    recorder.record_audio(output_root, language="ja", control=control)

    def test_record_audio_handles_wasapi_device_unavailable(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            output_root = Path(temp_dir)
            control = JobControl(lambda **ev: None)

            with mock.patch.object(
                recorder, "test_pc_audio", side_effect=RuntimeError("PC音声の録音デバイスが見つかりません")
            ):
                with self.assertRaisesRegex(RuntimeError, "録音デバイスが見つかりません"):
                    recorder.record_audio(output_root, language="ja", control=control)

            # Check that no stray recording directory is left behind
            subdirs = list(output_root.iterdir())
            self.assertEqual(len(subdirs), 0)

    def test_record_audio_emits_pause_and_recording_events(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            output_root = Path(temp_dir)
            fake_audio, init_fake = self._setup_fake_audio(output_root)

            emitted = []
            control = JobControl(lambda **ev: emitted.append(ev))
            control.pause.set()

            wait_calls = 0

            def fake_wait(timeout):
                nonlocal wait_calls
                wait_calls += 1
                if wait_calls >= 2:
                    control.stop.set()
                    return True
                return False

            control.stop.wait = fake_wait

            clock = [0.0]

            def fake_clock():
                val = clock[0]
                clock[0] += 1.0
                return val

            with (
                mock.patch.object(recorder, "test_pc_audio", return_value=("Mock Loopback", 0.5)),
                mock.patch.object(recorder, "SystemAudioRecorder", side_effect=init_fake),
                mock.patch.object(recorder.time, "monotonic", side_effect=fake_clock),
            ):
                result = recorder.record_audio(output_root, language="ja", control=control)

            self.assertTrue(result["success"])
            rec_events = [ev for ev in emitted if ev.get("kind") == "recording"]
            self.assertTrue(len(rec_events) > 0)
            self.assertTrue(rec_events[0]["paused"])


class AudioTranscriptionTests(unittest.TestCase):
    def test_transcribe_audio_only_pipeline(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            temp_path = Path(temp_dir)
            audio_file = _create_dummy_wav(temp_path / "PC音声.wav", duration_sec=5.0)

            def fake_create_pdf(output_filepath, **kwargs):
                out = Path(output_filepath)
                out.write_bytes(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\nstartxref\n9\n%%EOF\n")
                return os.fspath(out)

            with (
                mock.patch.object(hybrid, "_get_api_key", return_value="fake-api-key"),
                mock.patch(
                    "timeline_analysis.transcribe_chunks",
                    return_value=("This is a test transcript of PC audio.", []),
                ),
                mock.patch(
                    "audio_transcriber.generate_title_from_text",
                    return_value="Meeting Discussion",
                ),
                mock.patch("audio_transcriber.create_pdf", side_effect=fake_create_pdf) as mock_pdf,
            ):
                result = hybrid.transcribe_audio_only(audio_file, language="ja")

            self.assertTrue(result["success"])
            self.assertEqual(result["mode"], "audio_transcribe")
            self.assertIn("Meeting Discussion", result["pdf"])
            self.assertTrue(Path(result["pdf"]).name.endswith(".pdf"))
            self.assertEqual(result["transcript_chars"], len("This is a test transcript of PC audio."))
            mock_pdf.assert_called_once()
            _, kwargs = mock_pdf.call_args
            self.assertIsNone(kwargs.get("key_slides"))


class DesktopAppAudioModeTests(unittest.TestCase):
    def test_start_record_audio_bypasses_api_key_prompt(self):
        dummy_app = mock.Mock()
        dummy_app.busy.return_value = False
        dummy_app.api_key = ""
        dummy_app.settings = desktop_app.DEFAULTS
        dummy_app.status = mock.Mock()
        dummy_app.events = mock.Mock()
        dummy_app.tr = lambda ja, en: ja

        with (
            mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=True),
            mock.patch("tkinter.simpledialog.askstring") as mock_ask,
            mock.patch("subprocess.Popen") as mock_popen,
            mock.patch("threading.Thread"),
        ):
            mock_proc = mock.Mock()
            mock_proc.stdin = mock.Mock()
            mock_proc.stdout = []
            mock_popen.return_value = mock_proc

            desktop_app.DesktopApp.start(dummy_app, "record_audio")

            # API key dialog MUST NOT be called in record_audio mode!
            mock_ask.assert_not_called()
            self.assertEqual(dummy_app.current_mode, "record_audio")
            self.assertTrue(dummy_app.recording)
            mock_popen.assert_called_once()

    def test_recover_recording_audio_only(self):
        from recording_recovery import recover_recording, save_parts
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            recording_dir = Path(temp_dir) / "録音_2026-09-18_crash"
            parts_dir = recording_dir / ".recording_parts"
            parts_dir.mkdir(parents=True, exist_ok=True)

            # Create two 2.5-second audio chunks
            chunk1 = _create_dummy_wav(parts_dir / "audio_00000.wav", duration_sec=2.5)
            chunk2 = _create_dummy_wav(parts_dir / "audio_00001.wav", duration_sec=2.5)
            save_parts(parts_dir, "audio", [chunk1, chunk2])

            # Ensure video_parts.json does NOT exist
            self.assertFalse((parts_dir / "video_parts.json").exists())

            # Recover audio
            recovered = recover_recording(recording_dir)
            self.assertIn("audio", recovered)
            self.assertNotIn("video", recovered)

            recovered_wav = recovered["audio"]
            self.assertTrue(recovered_wav.is_file())
            from audio_compression import get_wav_duration_and_params
            info = get_wav_duration_and_params(recovered_wav)
            self.assertIsNotNone(info)
            self.assertAlmostEqual(info["duration"], 5.0, delta=0.2)

    def test_refresh_pending_detects_audio_recovery(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            audio_crash = Path(temp_dir) / "録音_2026-09-18_120000"
            parts_dir = audio_crash / ".recording_parts"
            parts_dir.mkdir(parents=True, exist_ok=True)
            (parts_dir / "audio_parts.json").write_text('{"parts": []}', encoding="utf-8")

            dummy_app = mock.Mock()
            dummy_app.settings = {"output_root": temp_dir}
            dummy_app.jobs = []
            dummy_app.pending = mock.Mock()
            dummy_app.tr = lambda ja, en: ja

            with mock.patch("gemini_hybrid_analyzer.list_pending_analyses", return_value=[]):
                desktop_app.DesktopApp.refresh_pending(dummy_app)

            self.assertTrue(any("録音を復旧" in job[2] for job in dummy_app.jobs))

