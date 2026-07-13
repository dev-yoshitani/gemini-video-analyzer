import tempfile
import unittest
from pathlib import Path

import local_video_analyzer as analyzer


@unittest.skipIf(analyzer.cv2 is None, "OpenCV is not installed")
class SignatureTests(unittest.TestCase):
    def test_identical_frames_have_zero_distance(self):
        frame = analyzer.np.full((180, 320, 3), 220, dtype=analyzer.np.uint8)
        signature = analyzer.make_signature(frame)
        self.assertEqual(analyzer.signature_distance(signature, signature)["score"], 0.0)

    def test_changed_slide_is_not_a_duplicate(self):
        first = analyzer.np.full((180, 320, 3), 245, dtype=analyzer.np.uint8)
        second = first.copy()
        analyzer.cv2.putText(second, "NEW SLIDE", (30, 90), analyzer.cv2.FONT_HERSHEY_SIMPLEX,
                             1.5, (0, 0, 0), 4)
        first_signature = analyzer.make_signature(first)
        second_signature = analyzer.make_signature(second)
        self.assertGreater(
            analyzer.signature_distance(first_signature, second_signature)["score"], 0.10
        )
        self.assertFalse(
            analyzer.is_near_duplicate(second_signature, [first_signature], 0.10)
        )

    def test_small_subject_motion_is_not_treated_as_a_scene_change(self):
        first = analyzer.np.full((180, 320, 3), 230, dtype=analyzer.np.uint8)
        second = first.copy()
        analyzer.cv2.rectangle(first, (120, 50), (180, 160), (50, 50, 50), -1)
        analyzer.cv2.rectangle(second, (135, 50), (195, 160), (50, 50, 50), -1)
        metrics = analyzer.signature_distance(
            analyzer.make_signature(first), analyzer.make_signature(second)
        )
        self.assertGreater(metrics["score"], 0.22)
        self.assertFalse(analyzer.is_meaningful_scene_change(metrics, 0.22))

    def test_scene_extraction_keeps_changes_and_drops_returned_duplicate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video_path = root / "synthetic.avi"
            writer = analyzer.cv2.VideoWriter(
                str(video_path),
                analyzer.cv2.VideoWriter_fourcc(*"MJPG"),
                12.0,
                (320, 180),
            )
            self.assertTrue(writer.isOpened())
            try:
                # A -> B -> A（重複）-> C。時間ベースでは72枚だが、期待出力は3枚。
                for label, color in [
                    ("A", (245, 245, 245)),
                    ("B", (80, 180, 240)),
                    ("A", (245, 245, 245)),
                    ("C", (120, 220, 100)),
                ]:
                    frame = analyzer.np.full((180, 320, 3), color, dtype=analyzer.np.uint8)
                    analyzer.cv2.putText(
                        frame, label, (125, 115), analyzer.cv2.FONT_HERSHEY_SIMPLEX,
                        3.0, (20, 20, 20), 7,
                    )
                    for _ in range(18):
                        writer.write(frame)
            finally:
                writer.release()

            scenes, metadata = analyzer.extract_scenes(
                video_path,
                root / "scenes",
                scan_fps=4.0,
                scene_threshold=0.18,
                duplicate_threshold=0.10,
                min_scene_duration=0.5,
            )

            self.assertEqual(len(scenes), 3)
            self.assertEqual(metadata["rejected_duplicates"], 1)
            self.assertEqual(len(list((root / "scenes").glob("*.jpg"))), 3)


class TranscriptTests(unittest.TestCase):
    def test_transcript_is_grouped_by_scene_time(self):
        scenes = [
            analyzer.Scene(1, 0.0, "00:00:00.000", "scenes/1.jpg", 1.0),
            analyzer.Scene(2, 5.0, "00:00:05.000", "scenes/2.jpg", 0.5),
        ]
        segments = [
            analyzer.TranscriptSegment(1.0, 2.0, "最初の説明"),
            analyzer.TranscriptSegment(5.2, 6.0, "次の説明"),
        ]
        analyzer.attach_transcript_to_scenes(scenes, segments)
        self.assertEqual(scenes[0].transcript, "最初の説明")
        self.assertEqual(scenes[1].transcript, "次の説明")

    def test_output_files_are_ai_ready(self):
        scenes = [analyzer.Scene(1, 0.0, "00:00:00.000", "scenes/1.jpg", 1.0, "説明")]
        segments = [analyzer.TranscriptSegment(0.0, 1.0, "説明")]
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary)
            analyzer._write_transcript_files(output_dir, segments)
            markdown = analyzer._write_ai_markdown(
                output_dir,
                Path("sample.mp4"),
                scenes,
                segments,
                {"duration_sec": 1.0},
            )
            self.assertIn("![Scene 001](scenes/1.jpg)", markdown.read_text(encoding="utf-8"))
            self.assertIn("00:00:00,000 --> 00:00:01,000",
                          (output_dir / "transcript.srt").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
