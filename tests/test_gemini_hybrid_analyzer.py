import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import gemini_hybrid_analyzer as hybrid

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None
    np = None


class HybridAnalyzerTests(unittest.TestCase):
    def test_candidate_limit_keeps_first_and_strongest_changes(self):
        scenes = [
            SimpleNamespace(timestamp_sec=0.0, change_score=1.0),
            SimpleNamespace(timestamp_sec=1.0, change_score=0.2),
            SimpleNamespace(timestamp_sec=2.0, change_score=0.9),
            SimpleNamespace(timestamp_sec=3.0, change_score=0.8),
        ]
        selected = hybrid._limit_candidates(scenes, 3)
        self.assertEqual([scene.timestamp_sec for scene in selected], [0.0, 2.0, 3.0])

    def test_cloud_analysis_stops_when_user_does_not_consent(self):
        with tempfile.TemporaryDirectory() as temporary:
            video = Path(temporary) / "video.mp4"
            video.write_bytes(b"placeholder")
            with mock.patch("builtins.input", return_value="n"):
                with self.assertRaisesRegex(RuntimeError, "キャンセル"):
                    hybrid.analyze_with_gemini(video, require_consent=True)

    @unittest.skipIf(cv2 is None, "OpenCV is not installed")
    def test_end_to_end_pipeline_creates_text_only_pdf_without_real_api(self):
        from key_slide_extractor import KeySlideExtractor

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = root / "video.avi"
            output = root / "result"
            output.mkdir()
            # ユーザー指定音声が出力先の中にあっても、完了時に削除しない。
            audio = output / "user_audio.wav"
            audio.write_bytes(b"mock audio")

            writer = cv2.VideoWriter(
                str(video), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (320, 180)
            )
            self.assertTrue(writer.isOpened())
            try:
                for label, color in [("A", (240, 240, 240)), ("B", (70, 180, 240))]:
                    frame = np.full((180, 320, 3), color, dtype=np.uint8)
                    cv2.putText(
                        frame, label, (125, 115), cv2.FONT_HERSHEY_SIMPLEX,
                        3.0, (20, 20, 20), 7,
                    )
                    for _ in range(15):
                        writer.write(frame)
            finally:
                writer.release()

            def fake_frame_analysis(
                _self,
                frames,
                transcript_text=None,
                progress_callback=None,
                skip_analyzed=False,
            ):
                for index, frame in enumerate(frames):
                    frame["analysis"] = {
                        "is_key_slide": True,
                        "importance_score": 90 - index,
                        "frame_type": "slide",
                        "summary": "画面内容を説明する解析結果です。",
                        "detected_text": "主要テキスト",
                        "reason": "重要な場面のため",
                    }
                    if progress_callback:
                        progress_callback(frames[:index + 1])
                return frames

            with (
                mock.patch.object(hybrid, "_get_api_key", return_value="test-api-key"),
                mock.patch("audio_transcriber.transcribe_with_gemini",
                           return_value=("高精度な文字起こし結果です。", "")),
                mock.patch("audio_transcriber.generate_title_from_text",
                           return_value="テスト解析レポート"),
                mock.patch.object(KeySlideExtractor, "analyze_all_frames", fake_frame_analysis),
            ):
                result = hybrid.analyze_with_gemini(
                    video,
                    audio_path=audio,
                    output_dir=output,
                    require_consent=False,
                )

            self.assertTrue(Path(result["pdf"]).is_file())
            self.assertGreaterEqual(result["key_slide_count"], 1)
            self.assertIsNone(result["analysis_json"])
            self.assertEqual(
                {item.resolve() for item in output.iterdir()},
                {audio.resolve(), Path(result["pdf"]).resolve()},
            )

    @unittest.skipIf(cv2 is None, "OpenCV is not installed")
    def test_resume_reuses_completed_transcription(self):
        from key_slide_extractor import KeySlideExtractor

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = root / "video.avi"
            audio = root / "audio.wav"
            output = root / "result"
            pending = root / "pending"
            audio.write_bytes(b"mock audio")

            writer = cv2.VideoWriter(
                str(video), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, (320, 180)
            )
            self.assertTrue(writer.isOpened())
            try:
                frame = np.full((180, 320, 3), (240, 240, 240), dtype=np.uint8)
                for _ in range(20):
                    writer.write(frame)
            finally:
                writer.release()

            with (
                mock.patch.object(hybrid, "_get_api_key", return_value="test-api-key"),
                mock.patch.object(hybrid, "_pending_jobs_dir", return_value=pending),
                mock.patch("audio_transcriber.transcribe_with_gemini",
                           return_value=("再利用する文字起こしです。", "")) as transcribe,
                mock.patch.object(
                    KeySlideExtractor,
                    "analyze_all_frames",
                    side_effect=RuntimeError("一時停止"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "一時停止"):
                    hybrid.analyze_with_gemini(
                        video,
                        audio_path=audio,
                        output_dir=output,
                        require_consent=False,
                    )
                transcribe.assert_called_once()

            state_path = output / hybrid.STATE_FILENAME
            self.assertTrue(state_path.is_file())
            self.assertTrue(any(pending.glob("*.json")))

            def complete_frames(
                _self,
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
                        "summary": "再開後の解析結果です。",
                        "detected_text": "再開",
                        "reason": "重要な場面",
                    }
                if progress_callback:
                    progress_callback(frames)
                return frames

            with (
                mock.patch.object(hybrid, "_get_api_key", return_value="test-api-key"),
                mock.patch.object(hybrid, "_pending_jobs_dir", return_value=pending),
                mock.patch("audio_transcriber.transcribe_with_gemini") as transcribe_again,
                mock.patch("audio_transcriber.generate_title_from_text",
                           return_value="再開テスト"),
                mock.patch.object(KeySlideExtractor, "analyze_all_frames", complete_frames),
            ):
                result = hybrid.resume_analysis(state_path, require_consent=False)

            transcribe_again.assert_not_called()
            self.assertTrue(result["resumed"])
            self.assertTrue(Path(result["pdf"]).is_file())
            self.assertFalse(state_path.exists())
            self.assertEqual(
                [item.resolve() for item in output.iterdir()],
                [Path(result["pdf"]).resolve()],
            )


if __name__ == "__main__":
    unittest.main()
