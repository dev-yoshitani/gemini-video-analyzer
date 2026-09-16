import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import wave
from types import SimpleNamespace
from unittest import mock

import desktop_app
import recording_recovery
import timeline_analysis as timeline
import workflow_control


def write_wav(path, seconds=3, rate=100):
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        output.writeframes(b"\x01\x00" * int(seconds * rate))


class TimelineTests(unittest.TestCase):
    def test_timed_api_transcription_preserves_negation_and_cleans_remote_file(self):
        import audio_transcriber
        client = mock.Mock()
        client.files.upload.return_value = SimpleNamespace(name="files/test", state=SimpleNamespace(name="ACTIVE"))
        segment = [{"start": 0, "end": 1, "text": "その数値は10ではない。"}]
        client.models.generate_content.return_value = SimpleNamespace(text=json.dumps(segment), candidates=[])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audio.wav"
            write_wav(path, 1)
            with mock.patch("google.genai.Client", return_value=client), mock.patch.object(
                    audio_transcriber, "compress_audio_for_upload", return_value=(str(path), False)):
                text, parsed = audio_transcriber.transcribe_with_gemini(str(path), "fake", language="ja",
                                                                       timestamps=True, duration_seconds=1)
        self.assertEqual(text, "その数値は10ではない。")
        self.assertEqual(parsed, segment)
        client.files.delete.assert_called_once_with(name="files/test")

    def test_malformed_timestamp_response_is_not_saved_and_remote_file_is_deleted(self):
        import audio_transcriber
        client = mock.Mock()
        client.files.upload.return_value = SimpleNamespace(name="files/test", state=SimpleNamespace(name="ACTIVE"))
        client.models.generate_content.return_value = SimpleNamespace(text='[{"start":0,"end":100,"text":"x"}]', candidates=[])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audio.wav"
            write_wav(path, 1)
            with mock.patch("google.genai.Client", return_value=client), mock.patch.object(
                    audio_transcriber, "compress_audio_for_upload", return_value=(str(path), False)):
                with self.assertRaises(ValueError):
                    audio_transcriber.transcribe_with_gemini(str(path), "fake", language="en",
                                                            timestamps=True, duration_seconds=1)
        client.files.delete.assert_called_once_with(name="files/test")

    def test_failed_chunk_only_is_retried_and_offsets_are_absolute(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.wav"
            write_wav(source, 5)
            segment = [{"start": 0, "end": 1, "text": "spoken words"}]
            first = mock.Mock(side_effect=[("spoken words", segment), RuntimeError("offline")])
            with self.assertRaisesRegex(RuntimeError, "offline"):
                timeline.transcribe_chunks(source, "fake", root / "chunks", first, chunk_seconds=2)
            second = mock.Mock(return_value=("spoken words", segment))
            text, result = timeline.transcribe_chunks(source, "fake", root / "chunks", second, chunk_seconds=2)
            self.assertEqual(second.call_count, 2)
            self.assertEqual([item["start"] for item in result], [0, 2, 4])
            self.assertEqual(text.count("spoken words"), 3)
            self.assertEqual(list((root / "chunks").glob("*.wav")), [])

    def test_changed_audio_or_language_does_not_reuse_old_transcript(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.wav"
            write_wav(source, 1)
            transcribe = mock.Mock(return_value=("text", [{"start": 0, "end": 1, "text": "text"}]))
            timeline.transcribe_chunks(source, "fake", root / "chunks", transcribe, language="ja")
            timeline.transcribe_chunks(source, "fake", root / "chunks", transcribe, language="en")
            self.assertEqual(transcribe.call_count, 2)

    def test_audio_limit_is_checked_before_any_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.wav"
            write_wav(source, 61)
            transcribe = mock.Mock()
            with self.assertRaises(ValueError):
                timeline.transcribe_chunks(source, "fake", root / "chunks", transcribe, max_audio_minutes=1)
            transcribe.assert_not_called()

    def test_cancel_retains_completed_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.wav"
            write_wav(source, 3)
            control = workflow_control.JobControl()
            def transcribe(*args, **kwargs):
                control.cancel.set()
                return "text", [{"start": 0, "end": 1, "text": "text"}]
            with mock.patch.object(workflow_control, "active", control):
                with self.assertRaises(workflow_control.JobCancelled):
                    timeline.transcribe_chunks(source, "fake", root / "chunks", transcribe, chunk_seconds=1)
            self.assertTrue((root / "chunks" / "00000.json").exists())
            self.assertFalse((root / "chunks" / "00001.json").exists())

    def test_context_uses_matching_speech_not_head_or_tail(self):
        data = [{"start": 0, "end": 10, "text": "opening"},
                {"start": 300, "end": 310, "text": "matching setting"},
                {"start": 900, "end": 910, "text": "closing"}]
        context = timeline.scene_context(data, 300, 330, "previous scene")
        self.assertIn("matching setting", context)
        self.assertIn("previous scene", context)
        self.assertNotIn("opening", context)
        self.assertNotIn("closing", context)

    def test_timestamp_validation_rejects_invalid_or_invented_ranges(self):
        for item in ([{"start": -1, "end": 1, "text": "x"}],
                     [{"start": 0, "end": 100, "text": "x"}],
                     [{"start": float("nan"), "end": 1, "text": "x"}],
                     [{"start": True, "end": 2, "text": "x"}],
                     [{"start": 0, "end": 1, "text": ""}]):
            with self.subTest(item=item), self.assertRaises(ValueError):
                timeline.validate_segments(item, 10)
        self.assertEqual(timeline.validate_segments([], 10), [])

    def test_selection_covers_beginning_middle_and_end_within_limit(self):
        scenes = [SimpleNamespace(timestamp_sec=i * 60, change_score=1 if i < 4 else 0.1)
                  for i in range(20)]
        result = timeline.balanced_candidates(scenes, 5)
        self.assertEqual(len(result), 5)
        self.assertEqual(result[0].timestamp_sec, 0)
        self.assertEqual(result[-1].timestamp_sec, 1140)
        self.assertTrue(any(400 <= scene.timestamp_sec <= 800 for scene in result))

    def test_corrupt_chunk_cache_is_replaced_without_losing_other_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.wav"
            write_wav(source, 2)
            transcribe = mock.Mock(return_value=("text", [{"start": 0, "end": 1, "text": "text"}]))
            timeline.transcribe_chunks(source, "fake", root / "chunks", transcribe, chunk_seconds=1)
            (root / "chunks" / "00001.json").write_text("truncated", encoding="utf-8")
            transcribe.reset_mock()
            timeline.transcribe_chunks(source, "fake", root / "chunks", transcribe, chunk_seconds=1)
            self.assertEqual(transcribe.call_count, 1)


class RecordingRecoveryTests(unittest.TestCase):
    def test_stuck_driver_call_is_bounded(self):
        import local_screen_recorder as recorder
        released = threading.Event()
        try:
            self.assertFalse(recorder._bounded_driver_call(released.wait, timeout=0.01))
        finally:
            released.set()
    def test_audio_rotation_ignores_paused_samples_and_finalizes_chunks(self):
        import local_screen_recorder as recorder
        if recorder.np is None:
            self.skipTest("NumPy unavailable")
        with tempfile.TemporaryDirectory() as directory:
            audio = recorder.SystemAudioRecorder(Path(directory) / "audio.wav", segment_seconds=0.02)
            audio.rate, audio.channels = 100, 1
            audio.parts_dir.mkdir()
            audio.chunk_path = audio.parts_dir / "audio_00000.wav"
            audio.wave_file = wave.open(str(audio.chunk_path), "wb")
            audio.wave_file.setparams((1, 2, 100, 0, "NONE", "not compressed"))
            calls = 0
            def read(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls <= 2:
                    audio.pause_event.set()
                else:
                    audio.pause_event.clear()
                if calls == 5:
                    audio.stop_event.set()
                return b"\x01\x00" * 2
            audio.stream = mock.Mock()
            audio.stream.read.side_effect = read
            audio._worker()
            self.assertTrue(audio.stop())
            with wave.open(str(audio.output_path), "rb") as saved:
                self.assertEqual(saved.getnframes(), 6)
            manifest = json.loads((audio.parts_dir / "audio_parts.json").read_text())
            self.assertEqual(len(manifest["parts"]), 3)

    def test_audio_merge_preserves_samples_and_source_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / "a.wav", root / "b.wav"]
            for path in paths:
                write_wav(path, 1)
            result = recording_recovery.merge_audio(paths, root / "merged.wav")
            with wave.open(str(result), "rb") as audio:
                self.assertEqual(audio.getnframes(), 200)
            self.assertTrue(all(path.exists() for path in paths))

    def test_failed_merge_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "merged.wav"
            target.write_bytes(b"original")
            with self.assertRaises(FileNotFoundError):
                recording_recovery.merge_audio([root / "missing.wav"], target)
            self.assertEqual(target.read_bytes(), b"original")

    def test_recovery_rejects_paths_outside_owned_chunk_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            timeline.atomic_json(root / ".recording_parts" / "audio_parts.json", {"parts": ["../private.wav"]})
            with self.assertRaises(ValueError):
                recording_recovery.recover_recording(root)

    def test_video_rotation_and_recovery_without_capturing_real_screen(self):
        import local_screen_recorder as recorder
        if recorder.cv2 is None or recorder.np is None:
            self.skipTest("OpenCV unavailable")
        class Capture:
            monitors = [{"left": 0, "top": 0, "width": 64, "height": 48}]
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def grab(self, region):
                return recorder.np.zeros((48, 64, 4), dtype=recorder.np.uint8)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            desktop = recorder.DesktopRecorder(root / "screen", fps=10, segment_seconds=0.2,
                                                capture_region=Capture.monitors[0])
            with mock.patch.object(recorder, "mss", SimpleNamespace(mss=Capture)), mock.patch.object(recorder, "_require_recording_dependencies"):
                desktop.start()
                desktop.stop_event.wait(0.65)
                result = desktop.stop()
            manifest = json.loads((root / ".recording_parts" / "video_parts.json").read_text())
            self.assertGreaterEqual(len(manifest["parts"]), 2)
            self.assertTrue(result.exists())
            capture = recorder.cv2.VideoCapture(str(result))
            self.assertEqual(int(capture.get(recorder.cv2.CAP_PROP_FRAME_COUNT)), desktop.frames_written)
            capture.release()


class DesktopTests(unittest.TestCase):
    def test_settings_reject_unbounded_uploads(self):
        for settings in ({"max_candidates": 0}, {"max_audio_minutes": 1441}, {"language": "xx"}):
            with self.assertRaises(ValueError):
                desktop_app.validate_settings(settings)

    def test_worker_reports_failure_without_network_or_interactive_input(self):
        job = {"mode": "analyze", "path": "__nonexistent_video__.mp4", "settings": desktop_app.DEFAULTS}
        run = subprocess.run([sys.executable, str(Path(desktop_app.__file__)), "--worker"],
                             input=json.dumps(job) + "\n", capture_output=True, text=True,
                             encoding="utf-8", env=dict(os.environ, PYTHONUTF8="1"), timeout=20)
        self.assertEqual(run.returncode, 1)
        self.assertIn(desktop_app.EVENT_PREFIX, run.stdout)
        self.assertIn('"kind": "error"', run.stdout)

    def test_gui_constructs_and_localizes_without_starting_capture(self):
        try:
            import tkinter as tk
            root = tk.Tk()
        except Exception as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        try:
            root.withdraw()
            with mock.patch.object(desktop_app.DesktopApp, "refresh_pending"):
                app = desktop_app.DesktopApp(root, "en")
                root.update_idletasks()
                self.assertEqual(app.status.get(), "Ready")
                self.assertFalse(app.busy())
                self.assertEqual(str(app.stop_button["state"]), "disabled")
        finally:
            root.destroy()

    def test_consent_requires_an_explicit_answer(self):
        events = []
        requested = threading.Event()
        def emit(**event):
            events.append(event)
            requested.set()
        control = workflow_control.JobControl(emit)
        result = []
        thread = threading.Thread(target=lambda: result.append(control.confirm()))
        thread.start()
        self.assertTrue(requested.wait(timeout=2))
        self.assertFalse(control.answer.is_set())
        control.accepted = False
        control.answer.set()
        thread.join(timeout=2)
        self.assertEqual(result, [False])
        self.assertEqual(events[0]["kind"], "consent")

    def test_resume_uses_user_limits_but_preserves_report_language(self):
        import gemini_hybrid_analyzer as hybrid
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / hybrid.STATE_FILENAME
            timeline.atomic_json(path, {"source": {"path": "video.mp4"},
                                        "options": {"language": "en", "max_candidates": 80, "max_audio_minutes": 60}})
            with mock.patch.object(hybrid, "analyze_with_gemini") as analyze:
                hybrid.resume_analysis(path, max_candidates=10, max_audio_minutes=180)
            self.assertEqual(analyze.call_args.kwargs["max_candidates"], 10)
            self.assertEqual(analyze.call_args.kwargs["max_audio_minutes"], 180)
            self.assertEqual(analyze.call_args.kwargs["language"], "en")


if __name__ == "__main__":
    unittest.main()
