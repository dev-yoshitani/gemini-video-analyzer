import tempfile
import unittest
from pathlib import Path
from unittest import mock

import local_screen_recorder as recorder


@unittest.skipIf(recorder.cv2 is None or recorder.np is None, "OpenCV is not installed")
class DesktopRecorderTests(unittest.TestCase):
    def test_capture_region_is_normalized_to_even_dimensions(self):
        region = recorder._normalize_capture_region({
            "left": 11,
            "top": 13,
            "width": 321,
            "height": 181,
        })
        self.assertEqual(region, {"left": 11, "top": 13, "width": 320, "height": 180})

    def test_user_can_choose_a_single_monitor(self):
        monitors = [
            {"left": 0, "top": 0, "width": 3200, "height": 1080},
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": 1920, "top": 0, "width": 1280, "height": 1024},
        ]
        with (
            mock.patch.object(recorder, "_available_monitors", return_value=monitors),
            mock.patch("builtins.input", side_effect=["2", "2"]),
        ):
            region, label = recorder.choose_capture_region("ask")
        self.assertEqual(region, monitors[2])
        self.assertIn("モニター2", label)

    def test_opens_a_local_video_writer_without_capturing_the_screen(self):
        # 現在のプロジェクト配下（日本語パスを含む）でも動画保存できることを確認する。
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            desktop = recorder.DesktopRecorder(Path(temporary) / "screen", fps=10.0)
            writer, output_path = desktop._open_writer(320, 180)
            try:
                frame = recorder.np.zeros((180, 320, 3), dtype=recorder.np.uint8)
                writer.write(frame)
            finally:
                writer.release()

            self.assertTrue(output_path.is_file())
            self.assertGreater(output_path.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
