"""Tests for audio-only recording and transcription workflows."""
import json
import io
import queue
import os
import subprocess
import tempfile
import threading
import time
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
                mock.patch.object(recorder, "test_pc_audio", side_effect=AssertionError("No preflight should run")) as preflight,
                mock.patch.object(recorder, "SystemAudioRecorder", side_effect=init_fake),
                mock.patch.object(recorder.time, "monotonic", side_effect=fake_clock),
            ):
                result = recorder.record_audio(output_root, language="ja", control=control)

            self.assertTrue(result["success"])
            preflight.assert_not_called()
            fake_audio.start.assert_called_once()
            self.assertEqual(list(Path(result["recording_dir"]).glob("*テスト*")), [])
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

            fake_audio, init_fake = self._setup_fake_audio(output_root)
            fake_audio.start.side_effect = RuntimeError("PC音声の録音デバイスが見つかりません")
            with mock.patch.object(recorder, "SystemAudioRecorder", side_effect=init_fake):
                with self.assertRaisesRegex(RuntimeError, "録音デバイスが見つかりません"):
                    recorder.record_audio(output_root, language="ja", control=control)

            # Check that no stray recording directory is left behind
            subdirs = list(output_root.iterdir())
            self.assertEqual(len(subdirs), 0)

    def test_record_audio_reports_no_received_audio_instead_of_file_missing(self):
        for language, message in (("ja", "音声データを受信できません"), ("en", "No audio data")):
            with self.subTest(language=language), tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
                fake_audio, init_fake = self._setup_fake_audio(Path(temporary))
                fake_audio.stop.side_effect = None
                fake_audio.stop.return_value = False
                control = JobControl(lambda **ev: None)
                control.stop.set()
                with mock.patch.object(recorder, "SystemAudioRecorder", side_effect=init_fake):
                    with self.assertRaisesRegex(RuntimeError, message):
                        recorder.record_audio(Path(temporary), language=language, control=control)

    def test_record_audio_keeps_original_stop_error_when_no_audio_was_saved(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            fake_audio, init_fake = self._setup_fake_audio(Path(temporary))
            fake_audio.stop.side_effect = RuntimeError("driver shutdown failed")
            control = JobControl(lambda **ev: None)
            control.stop.set()
            with mock.patch.object(recorder, "SystemAudioRecorder", side_effect=init_fake):
                with self.assertRaisesRegex(RuntimeError, "driver shutdown failed"):
                    recorder.record_audio(Path(temporary), control=control)

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
                mock.patch.object(recorder, "test_pc_audio", side_effect=AssertionError("No preflight should run")),
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
                    "timeline_analysis.transcribe_whole",
                    return_value=("This is a test transcript of PC audio.", []),
                ),
                mock.patch(
                    "audio_transcriber.generate_title_from_text",
                    return_value="Meeting Discussion",
                ),
                mock.patch("audio_transcriber.create_pdf", side_effect=fake_create_pdf) as mock_pdf,
                mock.patch("audio_compression.prepare_storage_audio", side_effect=lambda source, **kw: source),
            ):
                result = hybrid.transcribe_audio_only(audio_file, language="ja", require_consent=False)

            self.assertTrue(result["success"])
            self.assertEqual(result["mode"], "audio_transcribe")
            self.assertIn("Meeting Discussion", result["pdf"])
            self.assertTrue(Path(result["pdf"]).name.endswith(".pdf"))
            self.assertEqual(result["transcript_chars"], len("This is a test transcript of PC audio."))
            mock_pdf.assert_called_once()
            _, kwargs = mock_pdf.call_args
            self.assertIsNone(kwargs.get("key_slides"))


class DesktopAppAudioModeTests(unittest.TestCase):
    def _poll_app(self):
        app = desktop_app.DesktopApp.__new__(desktop_app.DesktopApp)
        app.events = queue.Queue()
        app.process = mock.Mock()
        app.workers = {app.process: desktop_app.WorkerJob(1, "record_audio_transcribe", desktop_app.DEFAULTS.copy(), capturing=True)}
        app.api_key = None
        app.finishing = False
        app.settings = desktop_app.DEFAULTS.copy()
        app.status = mock.Mock()
        app.progress = {}
        app.pause_button = mock.Mock()
        app.stop_button = mock.Mock()
        app.root = mock.Mock()
        app.refresh_pending = mock.Mock()
        app.update_worker_list = mock.Mock()
        return app

    def test_previous_worker_exit_cannot_disable_current_recording(self):
        app = self._poll_app()
        app.events.put((object(), None))
        desktop_app.DesktopApp.poll(app)
        app.stop_button.configure.assert_not_called()
        app.refresh_pending.assert_not_called()

    def test_queued_recording_notification_does_not_reenable_stop_after_click(self):
        app = self._poll_app()
        app.finishing = True
        app.events.put((app.process, desktop_app.EVENT_PREFIX + json.dumps({
            "kind": "recording", "seconds": 1, "level": 0, "paused": False, "silent": False})))
        desktop_app.DesktopApp.poll(app)
        app.stop_button.configure.assert_not_called()

    def test_worker_failure_immediately_disables_recording_controls(self):
        app = self._poll_app()
        app.events.put((app.process, desktop_app.EVENT_PREFIX + json.dumps({"kind": "error", "message": "startup failed"})))
        with mock.patch("tkinter.messagebox.showerror"):
            desktop_app.DesktopApp.poll(app)
        self.assertFalse(app.recording)
        app.stop_button.configure.assert_called_once_with(state="disabled")

    def test_audio_button_goes_directly_to_transcription(self):
        app = mock.Mock()
        app.recording_busy.return_value = False
        app.tr = lambda ja, en: ja
        with mock.patch("tkinter.messagebox.askokcancel", return_value=True) as consent:
            desktop_app.DesktopApp.record_audio_prompt(app)
        app.start.assert_called_once_with("record_audio_transcribe")
        self.assertIn("マイクは記録しません", consent.call_args.args[1])

    def test_declining_recording_does_not_start_worker(self):
        app = mock.Mock()
        app.recording_busy.return_value = False
        app.tr = lambda ja, en: ja
        with mock.patch("tkinter.messagebox.askokcancel", return_value=False):
            desktop_app.DesktopApp.record_audio_prompt(app)
        app.start.assert_not_called()

    def test_worker_routes_audio_without_screen_capture(self):
        job = {"mode": "record_audio_transcribe", "settings": desktop_app.DEFAULTS}
        import workflow_control
        with (
            mock.patch("sys.stdin", io.StringIO(json.dumps(job) + "\n")),
            mock.patch("sys.stdout", io.StringIO()),
            mock.patch("threading.Thread"),
            mock.patch.object(workflow_control, "active", None),
            mock.patch.object(recorder, "record_audio", return_value={"audio": "PC音声.wav", "recording_dir": "recorded"}) as record,
            mock.patch.object(recorder, "DesktopRecorder", side_effect=AssertionError("screen capture must not run")),
            mock.patch.object(hybrid, "transcribe_audio_only", return_value={"success": True, "pdf": "report.pdf"}) as transcribe,
        ):
            self.assertEqual(desktop_app.run_worker(), 0)
        self.assertFalse(record.call_args.kwargs["auto_compress_storage"])
        transcribe.assert_called_once_with("PC音声.wav", language="ja", max_audio_minutes=120)

    def test_start_record_audio_bypasses_api_key_prompt(self):
        dummy_app = mock.Mock()
        dummy_app.busy.return_value = False
        dummy_app.recording_busy.return_value = False
        dummy_app.workers = {}
        dummy_app.worker_number = 0
        dummy_app.progress = {}
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


class WorkerInitializationTests(unittest.TestCase):
    def test_missing_numpy_is_reported_before_command_reader_starts(self):
        import builtins
        import workflow_control

        original_import = builtins.__import__

        def checked_import(name, *args, **kwargs):
            if name == "numpy":
                raise ImportError("NumPy unavailable")
            return original_import(name, *args, **kwargs)

        job = {"mode": "record_audio_transcribe", "settings": desktop_app.DEFAULTS}
        output = io.StringIO()
        with (
            mock.patch("sys.stdin", io.StringIO(json.dumps(job) + "\n")),
            mock.patch("sys.stdout", output),
            mock.patch("builtins.__import__", side_effect=checked_import),
            mock.patch("threading.Thread") as thread,
            mock.patch.object(workflow_control, "active", None),
        ):
            self.assertEqual(desktop_app.run_worker(), 1)
        thread.assert_not_called()
        self.assertIn('"kind": "error"', output.getvalue())
        self.assertIn("NumPy unavailable", output.getvalue())

    def test_cold_worker_initializes_while_command_pipe_stays_open(self):
        # A fresh process matters: this test process already imported NumPy.
        # Only media/API entry points are replaced; no capture or network runs.
        child = r'''
import sys
from types import ModuleType
import desktop_app

recorder = ModuleType("local_screen_recorder")
def record_audio(*args, control=None, **kwargs):
    import numpy
    control.emit(kind="recording", seconds=0, level=0, paused=False, silent=False)
    if not control.stop.wait(10):
        raise RuntimeError("Stop command was not received")
    return {"audio": "synthetic.wav", "recording_dir": "synthetic"}
recorder.record_audio = record_audio
sys.modules["local_screen_recorder"] = recorder

hybrid = ModuleType("gemini_hybrid_analyzer")
def forbidden(*args, **kwargs):
    raise AssertionError("No video capture or live API call is allowed")
hybrid.analyze_with_gemini = hybrid.resume_analysis = forbidden
hybrid.transcribe_audio_only = lambda *args, **kwargs: {"success": True}
sys.modules["gemini_hybrid_analyzer"] = hybrid
raise SystemExit(desktop_app.run_worker())
'''
        process = subprocess.Popen(
            [str(Path(desktop_app.sys.executable).with_name("python.exe"))
             if os.name == "nt" else desktop_app.sys.executable, "-u", "-c", child],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", cwd=Path(desktop_app.__file__).parent,
            env=dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1"),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        lines = queue.Queue()

        def read():
            for line in process.stdout:
                lines.put(line)
            lines.put(None)

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        received = []

        def wait_event(kind, timeout):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    line = lines.get(timeout=min(.2, max(.01, deadline - time.monotonic())))
                except queue.Empty:
                    continue
                if line is None:
                    break
                received.append(line)
                if line.startswith(desktop_app.EVENT_PREFIX):
                    event = json.loads(line[len(desktop_app.EVENT_PREFIX):])
                    if event.get("kind") == kind:
                        return event
            self.fail(f"Worker did not emit {kind} with stdin open: {''.join(received)}")

        try:
            job = {"mode": "record_audio_transcribe", "settings": desktop_app.DEFAULTS}
            process.stdin.write(json.dumps(job) + "\n")
            process.stdin.flush()
            # No command or EOF may wake the stdin reader before readiness.
            wait_event("recording", 20)
            process.stdin.write('{"command": "stop"}\n')
            process.stdin.flush()
            self.assertTrue(wait_event("complete", 10)["result"]["success"])
            try:
                process.stdin.close()
            except OSError:
                pass  # The worker may have exited immediately after complete.
            self.assertEqual(process.wait(timeout=5), 0)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            try:
                process.stdin.close()
            except OSError:
                pass
            reader.join(timeout=2)
            process.stdout.close()


class AudioResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = _create_dummy_wav(self.root / "PC音声.wav", 5)
        self.jobs = self.root / "pending"
        patch = mock.patch.object(hybrid, "_pending_jobs_dir", return_value=self.jobs)
        patch.start()
        self.addCleanup(patch.stop)

    @staticmethod
    def pdf(output_filepath, **kwargs):
        Path(output_filepath).write_bytes(b"%PDF-1.4\n%%EOF\n")
        return output_filepath

    def test_upload_declined_preserves_audio_and_pending_job_without_api(self):
        with mock.patch.object(hybrid, "_ask_cloud_consent", return_value=False) as consent, mock.patch.object(hybrid, "_get_api_key") as key:
            with self.assertRaisesRegex(RuntimeError, "送信をキャンセル"):
                hybrid.transcribe_audio_only(self.source)
        key.assert_not_called()
        consent.assert_called_once_with("ja", audio_only=True)
        self.assertTrue(self.source.is_file())
        self.assertEqual(len(hybrid.list_pending_analyses()), 1)

    def test_audio_limit_checked_before_consent_or_api(self):
        _create_dummy_wav(self.source, 61)
        with mock.patch.object(hybrid, "_ask_cloud_consent") as consent, mock.patch.object(hybrid, "_get_api_key") as key:
            with self.assertRaisesRegex(ValueError, "上限"):
                hybrid.transcribe_audio_only(self.source, max_audio_minutes=1)
        consent.assert_not_called()
        key.assert_not_called()
        self.assertTrue(self.source.exists())

    def test_long_recording_uses_one_full_audio_request(self):
        _create_dummy_wav(self.source, 900)
        with (
            mock.patch.object(hybrid, "_get_api_key", return_value="fake"),
            mock.patch.object(hybrid, "_ask_cloud_consent", return_value=True) as consent,
            mock.patch("audio_transcriber.transcribe_with_gemini", return_value=(
                "全体の発言", [{"start": 0, "end": 900, "text": "全体の発言"}])) as transcribe,
            mock.patch("audio_transcriber.generate_title_from_text", return_value="会議"),
            mock.patch("audio_transcriber.create_pdf", side_effect=self.pdf),
            mock.patch("audio_compression.prepare_storage_audio", side_effect=lambda source, **kw: source),
        ):
            result = hybrid.transcribe_audio_only(self.source)
        consent.assert_called_once_with("ja", audio_only=True)
        transcribe.assert_called_once_with(str(self.source), "fake", language="ja",
                                           timestamps=False, duration_seconds=900)
        self.assertTrue(Path(result["pdf"]).is_file())
        self.assertFalse((Path(result["output_dir"]) / "Transcript_Chunks").exists())

    def test_pdf_failure_resumes_without_retranscription_or_images(self):
        with (
            mock.patch.object(hybrid, "_get_api_key", return_value="fake"),
            mock.patch("timeline_analysis.transcribe_whole", return_value=("テスト発言です。", [])) as whole,
            mock.patch("audio_transcriber.generate_title_from_text", return_value="会議") as title,
            mock.patch("audio_transcriber.create_pdf", side_effect=[RuntimeError("PDF failed"), None]) as pdf,
            mock.patch("audio_compression.prepare_storage_audio", side_effect=lambda source, **kw: source),
            mock.patch("key_slide_extractor.KeySlideExtractor", side_effect=AssertionError("no images")),
        ):
            with self.assertRaisesRegex(RuntimeError, "PDF"):
                hybrid.transcribe_audio_only(self.source, require_consent=False)
            job = hybrid.list_pending_analyses()[0]
            self.assertTrue(self.source.is_file())
            pdf.side_effect = self.pdf
            result = hybrid.resume_analysis(job["state_path"], require_consent=False, language="en")
        self.assertTrue(result["resumed"])
        self.assertEqual(whole.call_count, 1)
        self.assertEqual(title.call_count, 1)
        self.assertEqual(list(Path(result["output_dir"]).iterdir()), [Path(result["pdf"])])
        self.assertEqual(hybrid.list_pending_analyses(), [])
        self.assertTrue(self.source.is_file())

    def test_resume_retries_failed_whole_audio_without_splitting(self):
        _create_dummy_wav(self.source, 65)
        second = [{"start": 0, "end": 1, "text": "続きの発言"}]
        with (
            mock.patch.object(hybrid, "_get_api_key", return_value="fake"),
            mock.patch("audio_transcriber.transcribe_with_gemini", side_effect=RuntimeError("offline")) as transcribe,
            mock.patch("audio_transcriber.generate_title_from_text", return_value="会議"),
            mock.patch("audio_transcriber.create_pdf", side_effect=self.pdf),
            mock.patch("audio_compression.prepare_storage_audio", side_effect=lambda source, **kw: source),
        ):
            with self.assertRaisesRegex(RuntimeError, "offline"):
                hybrid.transcribe_audio_only(self.source, require_consent=False)
            transcribe.reset_mock()
            transcribe.side_effect = None
            transcribe.return_value = ("続きの発言", second)
            result = hybrid.resume_analysis(hybrid.list_pending_analyses()[0]["state_path"], require_consent=False)
        transcribe.assert_called_once()
        self.assertEqual(transcribe.call_args.args[0], str(self.source))
        self.assertEqual(transcribe.call_args.kwargs["duration_seconds"], 65)
        self.assertTrue(Path(result["pdf"]).exists())

    def test_audio_consent_event_describes_no_images(self):
        import workflow_control
        control = mock.Mock()
        control.confirm.return_value = True
        with mock.patch.object(workflow_control, "active", control):
            self.assertTrue(hybrid._ask_cloud_consent("en", audio_only=True))
        control.confirm.assert_called_once_with(language="en", audio_only=True)

