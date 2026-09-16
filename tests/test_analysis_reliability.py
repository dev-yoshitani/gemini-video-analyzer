import copy
import json
import tempfile
import wave
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import gemini_hybrid_analyzer as hybrid
from key_slide_extractor import KeySlideExtractor, is_valid_analysis


def analysis(**changes):
    return dict(
        dict(is_key_slide=True, importance_score=90, frame_type="slide",
             summary="The measurement settings are displayed.",
             detected_text="Baud rate 9600", reason="Important configuration step."),
        **changes,
    )


class FrameReliabilityTests(unittest.TestCase):
    def test_invalid_responses_are_not_treated_as_completed(self):
        for invalid in [None, [], {}, analysis(is_key_slide="false"),
                        analysis(importance_score=101), analysis(importance_score=True),
                        analysis(frame_type=[]), analysis(summary=None),
                        analysis(reason="analysis failed or skipped")]:
            with self.subTest(invalid=invalid):
                self.assertFalse(is_valid_analysis(invalid))
        self.assertTrue(is_valid_analysis(analysis(is_key_slide=False, importance_score=0)))

    def test_invalid_model_json_stops_instead_of_losing_the_frame(self):
        client = mock.Mock()
        client.models.generate_content.return_value = SimpleNamespace(text='{}', candidates=[])
        extractor = KeySlideExtractor("test", language="en")
        with tempfile.TemporaryDirectory() as directory:
            frame = Path(directory) / "scene.jpg"
            frame.write_bytes(b"mock image")
            with self.assertRaisesRegex(RuntimeError, "Scene analysis failed"):
                extractor.analyze_frame_with_gemini(frame, client)
        self.assertEqual(client.models.generate_content.call_count, len(extractor._models_to_try))

    def test_resume_retries_only_failed_cached_frame(self):
        good = analysis()
        failed = analysis(is_key_slide=False, importance_score=0, reason="analysis failed or skipped")
        frames = [dict(filename=str(i), timestamp_str="00:00", path=str(i), analysis=item)
                  for i, item in enumerate([good, failed])]
        extractor = KeySlideExtractor("test", language="ja")
        with (mock.patch("google.genai.Client"),
              mock.patch.object(extractor, "analyze_frame_with_gemini", return_value=analysis()) as run):
            extractor.analyze_all_frames(frames, skip_analyzed=True)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], "1")
        self.assertIs(frames[0]["analysis"], good)

    def test_changed_numeric_result_is_retained_but_exact_duplicate_removed(self):
        frames = [dict(timestamp_sec=i, analysis=analysis(detected_text=value))
                  for i, value in enumerate(["Baud rate 9600", "Baud rate 115200", "Baud rate 115200"])]
        selected = KeySlideExtractor("test").deduplicate_frames(frames)
        self.assertEqual([frame["timestamp_sec"] for frame in selected], [0, 1])

    def test_removed_frame_cannot_remove_another_distinct_frame(self):
        frames = [dict(timestamp_sec=i, analysis=analysis(importance_score=score, detected_text=""))
                  for i, score in enumerate([80, 95, 70])]
        extractor = KeySlideExtractor("test")
        # A resembles B and C, but B and C are distinct: keep both B and C.
        with mock.patch.object(extractor, "_text_similarity", side_effect=[0.8, 0.5]):
            result = extractor.deduplicate_frames(frames)
        self.assertEqual([frame["timestamp_sec"] for frame in result], [1, 2])

    def test_translation_api_error_preserves_original_analysis(self):
        frames = [dict(analysis=analysis(detected_text="測定結果"))]
        original = copy.deepcopy(frames)
        client = mock.Mock()
        client.models.generate_content.side_effect = ValueError("invalid response")
        with self.assertRaises(ValueError):
            KeySlideExtractor("test", language="en")._normalize_english_analyses(frames, client)
        self.assertEqual(frames, original)


class PdfCompletionTests(unittest.TestCase):
    def test_incomplete_pdf_does_not_replace_existing_report(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "report.pdf"
            target.write_bytes(b"existing report")

            def generate(output_filepath, **kwargs):
                Path(output_filepath).write_bytes(b"%PDF-1.4\ntruncated")
                return output_filepath

            with self.assertRaisesRegex(RuntimeError, "解析データを保持"):
                hybrid._publish_pdf(generate, target)
            self.assertEqual(target.read_bytes(), b"existing report")
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_text_fallback_preserves_pipeline_progress_until_successful_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, audio, output = root / "video.mp4", root / "audio.wav", root / "result"
            source.write_bytes(b"video")
            with wave.open(str(audio), "wb") as wav:
                wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                wav.writeframes(b"\x01\x00" * 16000)

            def scenes(_source, folder, **kwargs):
                folder.mkdir(parents=True)
                (folder / "scene.jpg").write_bytes(b"image")
                scene = SimpleNamespace(timestamp_sec=0, timestamp="00:00:00", image="Extracted Scenes/scene.jpg", change_score=1)
                scene.to_dict = lambda: dict(timestamp_sec=0, timestamp="00:00:00", image="Extracted Scenes/scene.jpg", change_score=1)
                return [scene], dict(duration_sec=1)

            def analyze(_self, frames, **kwargs):
                for frame in frames:
                    frame["analysis"] = analysis()
                kwargs["progress_callback"](frames)
                return frames

            def text_fallback(output_filepath, **kwargs):
                path = Path(output_filepath).with_suffix(".txt")
                path.write_text(kwargs["full_text"], encoding="utf-8")
                return str(path)

            with (mock.patch.object(hybrid, "_get_api_key", return_value="test"),
                  mock.patch.object(hybrid, "_pending_jobs_dir", return_value=root / "pending"),
                  mock.patch("local_video_analyzer.extract_scenes", side_effect=scenes),
                  mock.patch("local_video_analyzer.deduplicate_image_candidates", side_effect=lambda f, **kw: (f, 0)),
                  mock.patch.object(KeySlideExtractor, "analyze_all_frames", analyze),
                  mock.patch("audio_transcriber.transcribe_with_gemini", return_value=("Transcript to keep", "")) as transcribe,
                  mock.patch("audio_transcriber.generate_title_from_text", return_value="Report"),
                  mock.patch("audio_transcriber.create_pdf", side_effect=text_fallback)):
                with self.assertRaisesRegex(RuntimeError, "PDF could not be completed"):
                    hybrid.analyze_with_gemini(source, audio_path=audio, output_dir=output,
                                               language="en", require_consent=False)
                state = json.loads((output / hybrid.STATE_FILENAME).read_text(encoding="utf-8"))
                self.assertEqual(state["status"], "interrupted")
                self.assertNotIn("PDF生成", state["completed_stages"])
                self.assertEqual((output / "Transcript.txt").read_text().strip(), "Transcript to keep")
                self.assertTrue((output / "Frame_Analysis_Progress.json").exists())

                def complete(output_filepath, **kwargs):
                    Path(output_filepath).write_bytes(b"%PDF-1.4\n%%EOF\n")
                    return output_filepath

                with mock.patch("audio_transcriber.create_pdf", side_effect=complete):
                    result = hybrid.resume_analysis(output / hybrid.STATE_FILENAME, require_consent=False)
                self.assertTrue(Path(result["pdf"]).exists())
                self.assertEqual(transcribe.call_count, 1)
                self.assertEqual(list(output.iterdir()), [Path(result["pdf"])])


if __name__ == "__main__":
    unittest.main()
