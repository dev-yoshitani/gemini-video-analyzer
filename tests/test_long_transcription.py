"""Long transcripts and output-limit recovery, with all Gemini calls replaced."""
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import wave

import audio_transcriber as transcriber
import gemini_hybrid_analyzer as hybrid
import timeline_analysis as timeline


class LongTranscriptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.audio = self.root / "speech.wav"
        with wave.open(str(self.audio), "wb") as output:
            output.setparams((1, 2, 100, 0, "NONE", "not compressed"))
            output.writeframes(b"\x01\x00" * 100)
        self.original = self.audio.read_bytes()
        self.long_text = ("その値は10ではない。条件は温度20度未満です。\n" * 6000) + "末尾の発言も保持する。"
        self.client = mock.Mock()
        self.client.files.upload.return_value = SimpleNamespace(
            name="files/synthetic", state=SimpleNamespace(name="ACTIVE"))
        self.client.models.generate_content.return_value = self.response(self.long_text)

    @staticmethod
    def response(text, finish="STOP"):
        return SimpleNamespace(text=text, candidates=[SimpleNamespace(finish_reason=finish, content=None)])

    def provider(self):
        stack = ExitStack()
        stack.enter_context(mock.patch("google.genai.Client", return_value=self.client))
        stack.enter_context(mock.patch.object(transcriber, "compress_audio_for_upload",
                                              side_effect=lambda path, **kw: (str(path), False)))
        return stack

    def pipeline(self, pdf):
        stack = self.provider()
        stack.enter_context(mock.patch.object(hybrid, "_get_api_key", return_value="fake"))
        stack.enter_context(mock.patch.object(hybrid, "_ask_cloud_consent", return_value=True))
        stack.enter_context(mock.patch.object(hybrid, "_pending_jobs_dir", return_value=self.root / "pending"))
        stack.enter_context(mock.patch.object(transcriber, "generate_title_from_text", return_value="長い会議"))
        stack.enter_context(mock.patch.object(transcriber, "create_pdf", side_effect=pdf))
        return stack

    @staticmethod
    def pdf(output_filepath, **kwargs):
        Path(output_filepath).write_bytes(b"%PDF-1.4\n%%EOF\n")
        return output_filepath

    def test_plain_long_text_is_not_json_parsed_or_rewritten(self):
        with self.provider(), mock.patch.object(transcriber, "remove_fillers", side_effect=AssertionError("no destructive rewrite")):
            text, segments = transcriber.transcribe_with_gemini(str(self.audio), "fake", language="ja")
        self.assertEqual((text, segments), (self.long_text, ""))
        self.client.models.generate_content.assert_called_once()
        config = self.client.models.generate_content.call_args.kwargs["config"]
        self.assertEqual(config.max_output_tokens, 65536)
        prompt = self.client.models.generate_content.call_args.kwargs["contents"][0]
        self.assertIn("要約・省略せず", prompt)
        self.assertNotIn("JSON array", prompt)
        self.client.files.delete.assert_called_once_with(name="files/synthetic")

    def test_thinking_compatibility_retry_keeps_large_output_budget(self):
        self.client.models.generate_content.side_effect = [
            ValueError("thinking_config is not supported"), self.response(self.long_text)]
        with self.provider():
            text, _ = transcriber.transcribe_with_gemini(str(self.audio), "fake")
        self.assertEqual(text, self.long_text)
        calls = self.client.models.generate_content.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(call.kwargs["config"].max_output_tokens == 65536 for call in calls))
        self.assertIsNotNone(calls[0].kwargs["config"].thinking_config)
        self.assertIsNone(calls[1].kwargs["config"].thinking_config)

    def test_unrelated_error_never_retries_without_output_config(self):
        self.client.models.generate_content.side_effect = ValueError("invalid media")
        with self.provider():
            self.assertEqual(transcriber.transcribe_with_gemini(str(self.audio), "fake"), (None, None))
        calls = self.client.models.generate_content.call_args_list
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(call.kwargs["config"].max_output_tokens == 65536 for call in calls))

    def test_output_limit_keeps_partial_text_in_typed_error(self):
        self.client.models.generate_content.return_value = self.response(self.long_text, "MAX_TOKENS")
        with self.provider():
            with self.assertRaises(transcriber.TranscriptOutputLimitError) as error:
                transcriber.transcribe_with_gemini(str(self.audio), "fake")
        self.assertEqual(error.exception.partial_text, self.long_text)
        self.client.files.delete.assert_called_once_with(name="files/synthetic")
        self.assertEqual(self.audio.read_bytes(), self.original)

    def test_empty_output_limited_response_has_clear_error(self):
        self.client.models.generate_content.return_value = self.response(None, "MAX_TOKENS")
        with self.provider():
            with self.assertRaises(transcriber.TranscriptOutputLimitError) as error:
                transcriber.transcribe_with_gemini(str(self.audio), "fake")
        self.assertEqual(error.exception.partial_text, "")
        self.client.files.delete.assert_called_once_with(name="files/synthetic")

    def test_cut_off_timestamp_json_is_not_saved_as_plain_transcript(self):
        self.client.models.generate_content.return_value = self.response('[{"start":0,"text":"cut off', "MAX_TOKENS")
        with self.provider():
            with self.assertRaises(transcriber.TranscriptOutputLimitError) as error:
                transcriber.transcribe_with_gemini(str(self.audio), "fake", timestamps=True, duration_seconds=1)
        self.assertEqual(error.exception.partial_text, "")

    def test_audio_only_timeline_helper_does_not_require_timestamps(self):
        provider = mock.Mock(return_value=(self.long_text, ""))
        text, segments = timeline.transcribe_whole(self.audio, "fake", provider, timestamps=False)
        self.assertEqual((text, segments), (self.long_text, []))
        provider.assert_called_once_with(str(self.audio), "fake", language="ja", timestamps=False, duration_seconds=1)

    def test_pipeline_passes_entire_long_text_to_pdf_without_chunks(self):
        captured = []

        def pdf(output_filepath, **kwargs):
            captured.append(kwargs["full_text"])
            return self.pdf(output_filepath, **kwargs)

        with self.pipeline(pdf):
            result = hybrid.transcribe_audio_only(self.audio, preserve_source=True)
        self.assertEqual(captured, [self.long_text])
        self.client.models.generate_content.assert_called_once()
        self.assertEqual(self.audio.read_bytes(), self.original)
        self.assertEqual(list(Path(result["output_dir"]).iterdir()), [Path(result["pdf"])])

    def test_limit_failure_saves_incomplete_text_and_resume_cleans_it_after_verified_pdf(self):
        self.client.models.generate_content.return_value = self.response(self.long_text, "MAX_TOKENS")
        pdf = mock.Mock(side_effect=self.pdf)
        with self.pipeline(pdf):
            with self.assertRaises(transcriber.TranscriptOutputLimitError):
                hybrid.transcribe_audio_only(self.audio, preserve_source=True)
            pdf.assert_not_called()
            state_path = Path(hybrid.list_pending_analyses()[0]["state_path"])
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertNotIn("文字起こし", state["completed_stages"])
            self.assertEqual(len(state["partial_transcripts"]), 1)
            partial = Path(state["partial_transcripts"][0])
            saved = partial.read_text(encoding="utf-8")
            self.assertTrue(saved.startswith("【未完了】"))
            self.assertTrue(saved.endswith(self.long_text))
            self.assertEqual(self.audio.read_bytes(), self.original)
            self.client.models.generate_content.return_value = self.response(self.long_text)
            result = hybrid.resume_analysis(state_path)
        self.assertTrue(result["resumed"])
        self.assertTrue(Path(result["pdf"]).exists())
        self.assertFalse(partial.exists())
        self.assertEqual(list(Path(result["output_dir"]).iterdir()), [Path(result["pdf"])])
        self.assertEqual(self.audio.read_bytes(), self.original)

    def test_pdf_failure_reuses_entire_long_transcript_without_provider_call(self):
        with self.pipeline(mock.Mock(side_effect=RuntimeError("PDF failed"))):
            with self.assertRaisesRegex(RuntimeError, "PDF"):
                hybrid.transcribe_audio_only(self.audio, preserve_source=True)
            state_path = hybrid.list_pending_analyses()[0]["state_path"]
        self.client.models.generate_content.reset_mock()
        captured = []

        def pdf(output_filepath, **kwargs):
            captured.append(kwargs["full_text"])
            return self.pdf(output_filepath, **kwargs)

        with self.pipeline(pdf):
            hybrid.resume_analysis(state_path)
        self.client.models.generate_content.assert_not_called()
        self.assertEqual(captured, [self.long_text])
