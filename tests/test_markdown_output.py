import os
import tempfile
import unittest
from unittest import mock

import audio_transcriber


class CreateMarkdownTests(unittest.TestCase):
    def test_writes_title_metadata_and_transcript(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = os.path.join(temp_dir, "transcript.md")
            transcript = "最初の段落。\n\nSecond paragraph."

            with mock.patch.object(audio_transcriber, "GEMINI_MODEL", "test-model"):
                returned_path = audio_transcriber.create_markdown(
                    transcript,
                    output_path,
                    title="テストタイトル",
                    audio_filename="sample.wav",
                )

            self.assertEqual(returned_path, output_path)
            with open(output_path, encoding="utf-8") as output_file:
                content = output_file.read()

            self.assertIn("# テストタイトル", content)
            self.assertIn("- ソースファイル: sample.wav", content)
            self.assertIn("- モデル: test-model", content)
            self.assertIn("## 文字起こし全文", content)
            self.assertIn(transcript, content)
            self.assertTrue(content.endswith("\n"))


if __name__ == "__main__":
    unittest.main()
