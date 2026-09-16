import json
import wave
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import audio_transcriber
import gemini_hybrid_analyzer as hybrid
import key_slide_extractor
import launcher


class EnglishLauncherTests(unittest.TestCase):
    def test_start_en_forwards_to_the_common_launcher_in_english_mode(self):
        start_en = Path(__file__).resolve().parents[1] / "Start_EN.bat"
        text = start_en.read_text(encoding="ascii")
        self.assertIn("desktop_app.py --language en %*", text)

    def test_dragged_video_uses_english_workflow(self):
        with mock.patch.object(launcher, "_run_hybrid_video", return_value=7) as run:
            self.assertEqual(launcher.main(["--language", "en", "sample.mp4"]), 7)
        run.assert_called_once_with("sample.mp4", language="en")

    def test_english_recording_audio_is_found_as_a_sidecar(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            folder = Path(temporary)
            video = folder / "Screen Recording.mp4"
            audio = folder / "PC Audio.wav"
            video.write_bytes(b"video")
            audio.write_bytes(b"RIFF" + b"\x00" * 48)

            self.assertEqual(launcher._find_sidecar_audio(str(video)), audio.resolve())


class EnglishPromptTests(unittest.TestCase):
    def test_english_prompts_do_not_contain_japanese_and_request_english_output(self):
        prompts = [
            audio_transcriber._build_transcription_prompt("en"),
            audio_transcriber._build_title_prompt("en"),
            key_slide_extractor._build_english_frame_analysis_prompt("An English transcript."),
        ]
        for prompt in prompts:
            self.assertNotRegex(prompt, r"[ぁ-んァ-ン一-龯]")
            self.assertIn("English", prompt)

    def test_english_consent_uses_a_clickable_windows_dialog_before_console_input(self):
        with (
            mock.patch.object(hybrid, "_show_cloud_consent_dialog", return_value=True) as dialog,
            mock.patch("builtins.input") as console_input,
        ):
            self.assertTrue(hybrid._ask_cloud_consent("en"))

        dialog.assert_called_once_with("en")
        console_input.assert_not_called()

    def test_japanese_frame_ocr_is_translated_before_report_generation(self):
        translated = {
            "items": [{
                "index": 0,
                "summary": "The VS Code welcome screen is open.",
                "detected_text": "File; Edit; View; Open Folder",
                "reason": "This screen establishes the first setup step.",
            }]
        }
        client = SimpleNamespace(
            models=SimpleNamespace(
                generate_content=mock.Mock(
                    return_value=SimpleNamespace(
                        text=json.dumps(translated),
                        candidates=[],
                    )
                )
            )
        )
        extractor = key_slide_extractor.KeySlideExtractor(
            api_key="test-api-key",
            language="en",
        )
        frames = [{
            "analysis": {
                "summary": "VS Codeの画面です。",
                "detected_text": "ファイル 編集 表示 フォルダーを開く",
                "reason": "最初の設定手順です。",
            }
        }]

        result = extractor._normalize_english_analyses(frames, client)

        report_text = json.dumps(result, ensure_ascii=False)
        self.assertNotRegex(report_text, r"[ぁ-んァ-ン一-龯]")
        self.assertIn("Open Folder", report_text)

    def test_failed_frame_cleanup_preserves_original_for_resume(self):
        untranslated = {
            "items": [{
                "index": 0,
                "summary": "日本語のまま",
                "detected_text": "ファイル",
                "reason": "重要です",
            }]
        }
        client = SimpleNamespace(
            models=SimpleNamespace(
                generate_content=mock.Mock(
                    return_value=SimpleNamespace(
                        text=json.dumps(untranslated, ensure_ascii=False),
                        candidates=[],
                    )
                )
            )
        )
        extractor = key_slide_extractor.KeySlideExtractor(
            api_key="test-api-key",
            language="en",
        )
        frames = [{
            "analysis": {
                "summary": "日本語の要約",
                "detected_text": "ファイル",
                "reason": "重要です",
            }
        }]

        with self.assertRaisesRegex(RuntimeError, "translation is incomplete"):
            extractor._normalize_english_analyses(frames, client)
        self.assertEqual(frames[0]["analysis"]["summary"], "日本語の要約")
        self.assertEqual(frames[0]["analysis"]["detected_text"], "ファイル")


class EnglishPdfTests(unittest.TestCase):
    def test_english_pdf_uses_english_labels_even_without_a_japanese_font(self):
        class RecordingPdf:
            cells = []

            def __init__(self):
                pass

            def set_auto_page_break(self, **_kwargs):
                pass

            def add_font(self, *_args, **_kwargs):
                pass

            def add_page(self):
                pass

            def set_font(self, *_args, **_kwargs):
                pass

            def cell(self, _width, _height, text, **_kwargs):
                self.cells.append(str(text))

            def ln(self, *_args, **_kwargs):
                pass

            def set_text_color(self, *_args, **_kwargs):
                pass

            def set_draw_color(self, *_args, **_kwargs):
                pass

            def line(self, *_args, **_kwargs):
                pass

            def get_y(self):
                return 20

            def multi_cell(self, *_args, **_kwargs):
                pass

            def output(self, output_path):
                Path(output_path).write_bytes(b"PDF")

        RecordingPdf.cells = []
        slide = {
            "timestamp_str": "00:00:05.000",
            "analysis": {
                "importance_score": 88,
                "summary": "Important result.",
                "detected_text": "Revenue increased by 12%.",
            },
        }
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            output = Path(temporary) / "report.pdf"
            with (
                mock.patch("audio_transcriber.find_japanese_font", return_value=None),
                mock.patch("fpdf.FPDF", RecordingPdf),
            ):
                audio_transcriber.create_pdf(
                    "A complete English transcript.",
                    "",
                    str(output),
                    audio_filename="sample.mp4",
                    key_slides=[slide],
                    document_title="Quarterly Review",
                    language="en",
                )

            self.assertTrue(output.is_file())
            labels = "\n".join(RecordingPdf.cells)
            for label in (
                "Created:",
                "Source File:",
                "Model:",
                "[ Image Analysis Results ]",
                "Analysis 1",
                "Importance:",
                "Key Text in Image",
                "[ Full Transcript ]",
            ):
                self.assertIn(label, labels)


class EnglishHybridWorkflowTests(unittest.TestCase):
    def test_english_analysis_propagates_language_to_every_report_stage(self):
        class FakeScene:
            timestamp_sec = 0.0
            timestamp = "00:00:00.000"
            image = "Extracted Scenes/scene_001.jpg"
            change_score = 1.0

            def to_dict(self):
                return {
                    "timestamp_sec": self.timestamp_sec,
                    "timestamp": self.timestamp,
                    "image": self.image,
                    "change_score": self.change_score,
                }

        class FakeExtractor:
            init_kwargs = None

            def __init__(self, **kwargs):
                type(self).init_kwargs = kwargs

            def analyze_all_frames(
                self,
                frames,
                transcript_text=None,
                progress_callback=None,
                skip_analyzed=False,
            ):
                for frame in frames:
                    frame["analysis"] = {
                        "is_key_slide": True,
                        "importance_score": 90,
                        "frame_type": "slide",
                        "summary": "An English frame explanation.",
                        "detected_text": "Translated text.",
                        "reason": "It contains the main conclusion.",
                    }
                if progress_callback:
                    progress_callback(frames)
                return frames

            def deduplicate_frames(self, frames):
                return frames

            def select_key_slides(self, frames):
                return frames

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            root = Path(temporary)
            video = root / "画面録画.mp4"
            output = root / "result"
            audio = root / "PC Audio.wav"
            video.write_bytes(b"video")
            with wave.open(str(audio), "wb") as wav:
                wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                wav.writeframes(b"\x01\x00" * 16000)
            captured_extract_kwargs = {}

            def fake_extract_scenes(*_args, **kwargs):
                captured_extract_kwargs.update(kwargs)
                return [FakeScene()], {"duration_sec": 1.0}

            def fake_create_pdf(**kwargs):
                Path(kwargs["output_filepath"]).write_bytes(b"%PDF-1.4\n%%EOF\n")
                return kwargs["output_filepath"]

            with (
                mock.patch.object(hybrid, "_get_api_key", return_value="test-api-key"),
                mock.patch("local_video_analyzer.extract_scenes", side_effect=fake_extract_scenes),
                mock.patch(
                    "local_video_analyzer.deduplicate_image_candidates",
                    side_effect=lambda frames, **_kwargs: (frames, 0),
                ),
                mock.patch(
                    "audio_transcriber.transcribe_with_gemini",
                    return_value=("An English transcript.", ""),
                ) as transcribe,
                mock.patch(
                    "audio_transcriber.generate_title_from_text",
                    return_value="Quarterly Review",
                ) as title,
                mock.patch("audio_transcriber.create_pdf", side_effect=fake_create_pdf) as create_pdf,
                mock.patch("key_slide_extractor.KeySlideExtractor", FakeExtractor),
            ):
                result = hybrid.analyze_with_gemini(
                    video,
                    audio_path=audio,
                    output_dir=output,
                    require_consent=False,
                    language="en",
                )

            self.assertEqual(captured_extract_kwargs["language"], "en")
            self.assertEqual(transcribe.call_args.kwargs["language"], "en")
            self.assertEqual(FakeExtractor.init_kwargs["language"], "en")
            self.assertEqual(title.call_args.kwargs["language"], "en")
            self.assertEqual(create_pdf.call_args.kwargs["language"], "en")
            self.assertEqual(create_pdf.call_args.kwargs["audio_filename"], "Input Video.mp4")
            self.assertTrue(Path(result["pdf"]).name.endswith("_Analysis_Report.pdf"))

    def test_resume_keeps_the_language_saved_with_the_interrupted_job(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            root = Path(temporary)
            source = root / "video.mp4"
            source.write_bytes(b"video")
            state_path = root / hybrid.STATE_FILENAME
            state_path.write_text(
                '{"source": {"path": "' + str(source).replace("\\", "\\\\")
                + '"}, "audio_path": null, "options": '
                + '{"max_candidates": 4, "max_key_slides": 2, "language": "en"}}',
                encoding="utf-8",
            )
            with mock.patch.object(hybrid, "analyze_with_gemini", return_value={}) as analyze:
                hybrid.resume_analysis(state_path, require_consent=False)

            self.assertEqual(analyze.call_args.kwargs["language"], "en")


if __name__ == "__main__":
    unittest.main()
