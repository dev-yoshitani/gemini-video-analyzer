"""Import audio without capture, image analysis, live uploads or source changes."""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import wave
from unittest import mock

import desktop_app
import gemini_hybrid_analyzer as hybrid
import workflow_control


def write_audio(path, seconds=2):
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(b"\x01\x00" * int(seconds * 16000))
    return path


class AudioImportRoutingTests(unittest.TestCase):
    def test_audio_extensions_route_to_audio_only_for_drag_and_drop(self):
        for extension in desktop_app.AUDIO_EXTENSIONS:
            app = mock.Mock()
            desktop_app.DesktopApp.start_media(app, "会議" + extension.upper())
            app.start.assert_called_once_with("transcribe_audio", "会議" + extension.upper())
        app = mock.Mock()
        desktop_app.DesktopApp.start_media(app, "video.mp4")
        app.start.assert_called_once_with("analyze", "video.mp4")

    def test_choose_audio_starts_only_the_selected_file(self):
        app = mock.Mock()
        app.busy.return_value = False
        app.tr = lambda ja, en: ja
        with mock.patch("tkinter.filedialog.askopenfilename", return_value="会議.m4a") as choose:
            desktop_app.DesktopApp.choose_audio(app)
        app.start.assert_called_once_with("transcribe_audio", "会議.m4a")
        self.assertIn("*.mp3", choose.call_args.kwargs["filetypes"][0][1])

    def test_cancel_file_selection_does_not_start_worker(self):
        app = mock.Mock()
        app.busy.return_value = False
        app.tr = lambda ja, en: ja
        with mock.patch("tkinter.filedialog.askopenfilename", return_value=""):
            desktop_app.DesktopApp.choose_audio(app)
        app.start.assert_not_called()

    def test_worker_import_mode_never_records_or_analyzes_images(self):
        job = {"mode": "transcribe_audio", "path": "speech.mp3", "settings": desktop_app.DEFAULTS}
        with (
            mock.patch("sys.stdin", io.StringIO(json.dumps(job) + "\n")),
            mock.patch("sys.stdout", io.StringIO()),
            mock.patch("threading.Thread"),
            mock.patch.object(workflow_control, "active", None),
            mock.patch.object(hybrid, "transcribe_audio_only", return_value={"pdf": "report.pdf"}) as transcribe,
            mock.patch.object(hybrid, "analyze_with_gemini", side_effect=AssertionError("no images")),
            mock.patch("local_screen_recorder.record_audio", side_effect=AssertionError("no capture")),
        ):
            self.assertEqual(desktop_app.run_worker(), 0)
        transcribe.assert_called_once_with("speech.mp3", language="ja", max_audio_minutes=120,
                                           preserve_source=True)

    def test_new_initial_audio_file_uses_media_router_after_gui_is_ready(self):
        import tkinter as tk
        try:
            root = tk.Tk()
        except Exception as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        try:
            root.withdraw()
            with mock.patch.object(desktop_app.DesktopApp, "refresh_pending"):
                app = desktop_app.DesktopApp(root, "ja", "speech.mp3")
            with mock.patch.object(app, "start") as start:
                # Trigger the scheduled startup using Tk's own callback registry.
                for callback_id in root.tk.call("after", "info"):
                    script, _ = root.tk.call("after", "info", callback_id)
                    if "lambda" in script:
                        root.tk.call(script)
                        root.after_cancel(callback_id)
                        break
                else:
                    self.fail("Initial-media callback missing")
            start.assert_called_once_with("transcribe_audio", "speech.mp3")
        finally:
            root.destroy()


class ImportedAudioSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patch = mock.patch.object(hybrid, "_pending_jobs_dir", return_value=self.root / "pending")
        patch.start()
        self.addCleanup(patch.stop)

    @staticmethod
    def pdf(output_filepath, **kwargs):
        Path(output_filepath).write_bytes(b"%PDF-1.4\n%%EOF\n")
        return output_filepath

    def run_import(self, source, *, language="ja", pdf=None):
        originals = source.read_bytes(), source.stat().st_mtime_ns
        with (
            mock.patch.object(hybrid, "_get_api_key", return_value="fake"),
            mock.patch.object(hybrid, "_ask_cloud_consent", return_value=True) as consent,
            mock.patch("audio_transcriber.transcribe_with_gemini", return_value=(
                "発言内容", [{"start": 0, "end": 1, "text": "発言内容"}])) as transcribe,
            mock.patch("audio_transcriber.generate_title_from_text", return_value="会議"),
            mock.patch("audio_transcriber.create_pdf", side_effect=pdf or self.pdf),
            mock.patch("audio_compression.prepare_storage_audio", side_effect=AssertionError("must preserve original")),
            mock.patch("key_slide_extractor.KeySlideExtractor", side_effect=AssertionError("no images")),
        ):
            result = hybrid.transcribe_audio_only(source, preserve_source=True, language=language)
        self.assertEqual((source.read_bytes(), source.stat().st_mtime_ns), originals)
        consent.assert_called_once_with(language, audio_only=True)
        transcribe.assert_called_once()
        uploaded = Path(transcribe.call_args.args[0])
        self.assertNotEqual(uploaded, source)
        self.assertEqual(uploaded.parent, Path(result["output_dir"]))
        self.assertFalse(uploaded.exists(), "Remove only our normalized intermediate after success")
        self.assertEqual(result["audio"], str(source))
        self.assertTrue(Path(result["pdf"]).exists())
        return result

    def test_imported_wav_is_preserved_and_only_pdf_is_added(self):
        source = write_audio(self.root / "会議.wav")
        result = self.run_import(source)
        self.assertEqual(list(Path(result["output_dir"]).iterdir()), [Path(result["pdf"])])

    def test_different_source_files_have_separate_result_folders(self):
        first = self.run_import(write_audio(self.root / "one.wav"))
        second = self.run_import(write_audio(self.root / "two.wav"))
        self.assertNotEqual(first["output_dir"], second["output_dir"])
        self.assertTrue(Path(first["pdf"]).exists())

    def test_declining_upload_preserves_original_and_never_calls_provider(self):
        source = write_audio(self.root / "speech.wav")
        original = source.read_bytes()
        with mock.patch.object(hybrid, "_ask_cloud_consent", return_value=False), mock.patch.object(hybrid, "_get_api_key") as key, mock.patch("audio_transcriber.transcribe_with_gemini") as provider:
            with self.assertRaisesRegex(RuntimeError, "送信をキャンセル"):
                hybrid.transcribe_audio_only(source, preserve_source=True)
        self.assertEqual(source.read_bytes(), original)
        key.assert_not_called()
        provider.assert_not_called()

    def test_resume_after_pdf_failure_keeps_original_and_reuses_transcript(self):
        source = write_audio(self.root / "speech.wav")
        original = source.read_bytes()
        with (
            mock.patch.object(hybrid, "_get_api_key", return_value="fake"),
            mock.patch("audio_transcriber.transcribe_with_gemini", return_value=(
                "発言内容", [{"start": 0, "end": 1, "text": "発言内容"}])) as provider,
            mock.patch("audio_transcriber.generate_title_from_text", return_value="会議"),
            mock.patch("audio_transcriber.create_pdf", side_effect=RuntimeError("PDF failed")),
        ):
            with self.assertRaisesRegex(RuntimeError, "PDF"):
                hybrid.transcribe_audio_only(source, preserve_source=True, require_consent=False)
        provider.assert_called_once()
        state = hybrid.list_pending_analyses()[0]["state_path"]
        with mock.patch.object(hybrid, "_get_api_key", return_value="fake"), mock.patch("audio_transcriber.create_pdf", side_effect=self.pdf), mock.patch("audio_transcriber.transcribe_with_gemini") as again, mock.patch("audio_compression.prepare_storage_audio", side_effect=AssertionError("must preserve source")):
            result = hybrid.resume_analysis(state, require_consent=False)
        again.assert_not_called()
        self.assertEqual(source.read_bytes(), original)
        self.assertTrue(Path(result["pdf"]).exists())

    def test_real_audio_codecs_are_decoded_locally_and_originals_preserved(self):
        try:
            import av
            import numpy as np
        except ImportError:
            self.skipTest("PyAV/NumPy unavailable")
        for suffix, codec in ((".flac", "flac"), (".mp3", "libmp3lame"), (".m4a", "aac")):
            with self.subTest(suffix=suffix):
                source = self.root / ("speech" + suffix)
                with av.open(os.fspath(source), "w") as container:
                    stream = container.add_stream(codec, rate=16000)
                    frame = av.AudioFrame.from_ndarray(np.full((1, 32000), 1000, dtype=np.int16),
                                                      format="s16", layout="mono")
                    frame.sample_rate = 16000
                    for packet in stream.encode(frame):
                        container.mux(packet)
                    for packet in stream.encode(None):
                        container.mux(packet)
                self.run_import(source, language="en")
