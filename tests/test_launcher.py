import sys
import unittest
from unittest import mock

import launcher


class LauncherTests(unittest.TestCase):
    def test_dragged_video_starts_high_accuracy_analysis(self):
        with mock.patch.object(launcher, "_run_hybrid_video", return_value=7) as run:
            self.assertEqual(launcher.main(["sample.mp4"]), 7)
        run.assert_called_once_with("sample.mp4")

    def test_menu_can_exit_without_starting_a_tool(self):
        with mock.patch("builtins.input", return_value="0"):
            self.assertEqual(launcher.main([]), 0)

    def test_resume_menu_reports_when_no_jobs_exist(self):
        with mock.patch(
            "gemini_hybrid_analyzer.list_pending_analyses", return_value=[]
        ):
            self.assertEqual(launcher._run_pending_analysis(), 0)

    def test_legacy_transcription_uses_the_resolved_python_runner(self):
        with mock.patch(
            "launcher.subprocess.run", return_value=mock.Mock(returncode=7)
        ) as run:
            self.assertEqual(launcher._run_gemini(), 7)
        run.assert_called_once_with(
            [sys.executable, str(launcher.BASE_DIR / "audio_transcriber.py")],
            cwd=launcher.BASE_DIR,
        )

    def test_menu_option_four_starts_legacy_transcription(self):
        with (
            mock.patch("builtins.input", return_value="4"),
            mock.patch.object(launcher, "_run_gemini", return_value=7) as run,
        ):
            self.assertEqual(launcher.main([]), 7)
        run.assert_called_once_with()

    def test_menu_does_not_offer_removed_local_analysis_modes(self):
        with mock.patch("builtins.input", return_value="0"), mock.patch("builtins.print") as output:
            self.assertEqual(launcher.main([]), 0)
        menu_text = "\n".join(" ".join(map(str, call.args)) for call in output.call_args_list)
        self.assertNotIn("完全ローカル解析", menu_text)
        self.assertIn("未完了の高精度AI解析", menu_text)


if __name__ == "__main__":
    unittest.main()
