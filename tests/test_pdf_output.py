import tempfile
import unittest
from pathlib import Path
from unittest import mock

import audio_transcriber


class PdfOutputTests(unittest.TestCase):
    def test_pdf_contains_analysis_text_without_embedding_images(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "report.pdf"
            slide = {
                "timestamp_str": "00:00:05.000",
                "saved_path": __file__,
                "analysis": {
                    "importance_score": 88,
                    "summary": "重要な場面の解析結果です。",
                    "detected_text": "画面内の主要テキスト",
                },
            }
            with mock.patch("fpdf.FPDF.image", side_effect=AssertionError("画像は不要")):
                returned = audio_transcriber.create_pdf(
                    "文字起こし全文です。",
                    "",
                    str(output),
                    audio_filename="sample.mp4",
                    key_slides=[slide],
                    document_title="AI動画解析レポート",
                )
            self.assertEqual(Path(returned), output)
            self.assertTrue(output.is_file())
            self.assertGreater(output.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
