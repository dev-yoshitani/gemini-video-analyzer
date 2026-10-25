"""Concurrent recording/transcription routing, without hardware or live APIs."""
import json
import queue
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

import desktop_app


def headless_app():
    app = desktop_app.DesktopApp.__new__(desktop_app.DesktopApp)
    app.settings = desktop_app.DEFAULTS.copy()
    app.settings_path = Path("unused-test-settings.json")
    app.process = None
    app.workers = {}
    app.worker_number = 0
    app.events = queue.Queue()
    app.recording = app.finishing = app.paused = app.terminal_event = False
    app.api_key = "fake-test-key"
    app.result_path = None
    app.root = mock.Mock()
    app.status = mock.Mock()
    app.progress = {}
    app.pause_button = mock.Mock()
    app.stop_button = mock.Mock()
    app.log = mock.Mock()
    app.log.index.return_value = "1.0"
    app.worker_list = mock.Mock()
    app.worker_list.curselection.return_value = ()
    app.worker_rows = []
    app.refresh_pending = mock.Mock()
    return app


def add_job(app, capturing=False, foreground=True):
    process = mock.Mock()
    process.poll.return_value = None
    app.worker_number += 1
    app.workers[process] = desktop_app.WorkerJob(
        app.worker_number, "record_audio_transcribe", app.settings.copy(), capturing=capturing)
    if foreground:
        app.process = process
        app.current_mode = "record_audio_transcribe"
        app.recording = capturing
    return process


def event(app, process, kind, **fields):
    app.events.put((process, desktop_app.EVENT_PREFIX + json.dumps({"kind": kind, **fields})))


class ConcurrentRecordingTests(unittest.TestCase):
    def test_saved_recording_releases_slot_while_analysis_is_busy(self):
        app = headless_app()
        process = add_job(app, capturing=True)
        app.finishing = True
        event(app, process, "stage", stage="録音保存")
        app.poll()
        self.assertTrue(app.recording_busy(), "Saving must still reserve the device")
        event(app, process, "saved_recording", audio="recording/PC音声.wav")
        app.poll()
        self.assertFalse(app.recording_busy())
        self.assertTrue(app.busy())
        self.assertFalse(app.recording)

    def test_new_recording_starts_during_prior_transcription(self):
        app = headless_app()
        previous = add_job(app)
        process = mock.Mock()
        process.poll.return_value = None
        process.stdout = []
        with mock.patch("desktop_app.subprocess.Popen", return_value=process), mock.patch("desktop_app.threading.Thread"):
            app.start("record_audio_transcribe")
        self.assertIs(app.process, process)
        self.assertIn(previous, app.workers)
        self.assertEqual(len(app.workers), 2)
        self.assertTrue(app.recording)
        self.assertTrue(app.recording_busy())
        self.assertEqual(app.stop_button.configure.call_args_list[0].kwargs["state"], "normal")
        self.assertNotIn("fake-test-key", process.stdin.write.call_args.args[0])

    def test_second_capture_is_blocked_even_while_first_is_saving(self):
        for finishing in (False, True):
            with self.subTest(finishing=finishing):
                app = headless_app()
                add_job(app, capturing=True)
                app.finishing = finishing
                app.recording = not finishing
                with mock.patch("desktop_app.subprocess.Popen") as popen:
                    app.start("record_audio_transcribe")
                    app.start("record")
                popen.assert_not_called()

    def test_old_progress_waiting_stage_complete_and_exit_preserve_new_recording(self):
        app = headless_app()
        old = add_job(app)
        current = add_job(app, capturing=True)
        app.progress["value"] = 42
        event(app, old, "progress", stage="transcription", current=1, total=3)
        event(app, old, "waiting", seconds=60)
        event(app, old, "stage", stage="PDF生成")
        event(app, old, "complete", result={"pdf": "old.pdf"})
        app.events.put((old, None))
        with mock.patch("timeline_analysis.atomic_json"):
            app.poll()
        self.assertIs(app.process, current)
        self.assertTrue(app.recording)
        self.assertTrue(app.recording_busy())
        app.stop_button.configure.assert_not_called()
        app.pause_button.configure.assert_not_called()
        app.status.set.assert_not_called()
        self.assertEqual(app.progress["value"], 42)
        self.assertEqual(app.workers[old].result_path, "old.pdf")
        self.assertTrue(app.workers[old].exited)

    def test_old_error_does_not_disable_new_recording(self):
        app = headless_app()
        old = add_job(app)
        add_job(app, capturing=True)
        event(app, old, "error", message="API failed: fake-test-key")
        with mock.patch("tkinter.messagebox.showerror") as error:
            app.poll()
        self.assertTrue(app.recording)
        app.stop_button.configure.assert_not_called()
        app.status.set.assert_not_called()
        self.assertNotIn("fake-test-key", error.call_args.args[1])
        self.assertIn("#1", error.call_args.args[1])

    def test_consent_is_routed_to_its_own_worker_and_uses_job_limits(self):
        for accepted in (True, False):
            with self.subTest(accepted=accepted):
                app = headless_app()
                old = add_job(app)
                app.workers[old].settings["max_audio_minutes"] = 15
                current = add_job(app, capturing=True)
                event(app, old, "consent", audio_only=True)
                with mock.patch("tkinter.messagebox.askyesno", return_value=accepted) as consent:
                    app.poll()
                command = json.loads(old.stdin.write.call_args.args[0])
                self.assertEqual(command, {"command": "consent", "accepted": accepted})
                current.stdin.write.assert_not_called()
                self.assertIn("15分", consent.call_args.args[1])
                self.assertIn("#1", consent.call_args.args[1])
                app.stop_button.configure.assert_not_called()
                self.assertTrue(app.recording)

    def test_stop_pause_and_cancel_target_current_recording_only(self):
        app = headless_app()
        old = add_job(app)
        current = add_job(app, capturing=True)
        app.pause()
        app.finish_recording()
        app.cancel()
        actions = [json.loads(call.args[0])["command"] for call in current.stdin.write.call_args_list]
        self.assertEqual(actions, ["pause", "stop", "cancel"])
        old.stdin.write.assert_not_called()

    def test_cancel_selected_analysis_does_not_cancel_recorder(self):
        app = headless_app()
        old = add_job(app)
        current = add_job(app, capturing=True)
        app.worker_rows = [old, current]
        app.worker_list.curselection.return_value = (0,)
        app.cancel_worker()
        self.assertEqual(json.loads(old.stdin.write.call_args.args[0])["command"], "cancel")
        current.stdin.write.assert_not_called()
        app.worker_list.curselection.return_value = (1,)
        app.cancel_worker()
        current.stdin.write.assert_not_called()

    def test_close_safely_cancels_all_active_jobs_and_waits(self):
        app = headless_app()
        first = add_job(app)
        second = add_job(app, capturing=True)
        with mock.patch("tkinter.messagebox.askyesno", return_value=True):
            app.close()
        for process in (first, second):
            self.assertEqual(json.loads(process.stdin.write.call_args.args[0])["command"], "cancel")
        app.root.destroy.assert_not_called()

    def test_spawn_failure_preserves_prior_worker(self):
        app = headless_app()
        old = add_job(app)
        with mock.patch("desktop_app.subprocess.Popen", side_effect=OSError("spawn failed")), mock.patch("tkinter.messagebox.showerror"):
            app.start("record_audio_transcribe")
        self.assertIs(app.process, old)
        self.assertEqual(list(app.workers), [old])

    def test_done_background_results_can_be_opened_individually(self):
        app = headless_app()
        old = add_job(app)
        add_job(app, capturing=True)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "result.pdf"
            path.touch()
            app.workers[old].result_path = str(path)
            app.worker_rows = list(app.workers)
            app.worker_list.curselection.return_value = (0,)
            with mock.patch("desktop_app.os.startfile", create=True) as opened:
                app.open_worker_result()
            opened.assert_called_once_with(str(path))


class WorkerOverlapIntegrationTests(unittest.TestCase):
    def test_two_real_workers_overlap_without_audio_capture_or_cloud(self):
        # Execute the real subprocess command protocol with replaced I/O providers.
        child = r'''
import sys, time
from pathlib import Path
from types import ModuleType
import desktop_app, workflow_control
recorder = ModuleType("local_screen_recorder")
def record_audio(*args, control=None, **kwargs):
    control.emit(kind="recording", seconds=0, level=.2, paused=False, silent=False)
    if not control.stop.wait(15):
        raise RuntimeError("Missing stop command")
    control.emit(kind="saved_recording", audio="synthetic/PC Audio.wav")
    return {"audio": "synthetic/PC Audio.wav", "recording_dir": "synthetic"}
recorder.record_audio = record_audio
sys.modules["local_screen_recorder"] = recorder
hybrid = ModuleType("gemini_hybrid_analyzer")
def forbidden(*a, **kw):
    raise AssertionError("Live provider forbidden")
hybrid.analyze_with_gemini = hybrid.resume_analysis = forbidden
def transcribe(*a, **kw):
    control = workflow_control.active
    if not control.confirm(audio_only=True):
        raise RuntimeError("Upload declined")
    control.emit(kind="progress", stage="transcription", current=0, total=1)
    deadline = time.monotonic() + 15
    while not Path(sys.argv[1]).exists():
        control.checkpoint()
        if time.monotonic() > deadline:
            raise RuntimeError("Test gate timeout")
        time.sleep(.03)
    return {"pdf": "synthetic.pdf", "success": True}
hybrid.transcribe_audio_only = transcribe
sys.modules["gemini_hybrid_analyzer"] = hybrid
raise SystemExit(desktop_app.run_worker())
'''
        app = headless_app()
        real_popen = desktop_app.subprocess.Popen
        executable = str(Path(desktop_app.sys.executable).with_name("python.exe")) if desktop_app.os.name == "nt" else desktop_app.sys.executable
        processes = []
        with tempfile.TemporaryDirectory() as temp:
            gate = Path(temp) / "complete"

            def popen(_args, **kwargs):
                process = real_popen([executable, "-u", "-c", child, str(gate)], **kwargs)
                processes.append(process)
                return process

            def until(predicate):
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    app.poll()
                    if predicate():
                        return
                    time.sleep(.03)
                self.fail("Worker integration timed out")

            try:
                with mock.patch("desktop_app.subprocess.Popen", side_effect=popen), mock.patch("tkinter.messagebox.askyesno", return_value=True), mock.patch("tkinter.messagebox.showerror") as error, mock.patch("timeline_analysis.atomic_json"):
                    app.start("record_audio_transcribe")
                    first = app.process
                    until(lambda: app.progress.get("value") == 20)
                    app.finish_recording()
                    until(lambda: app.workers[first].status == "文字起こし: 0 / 1")
                    self.assertFalse(app.recording_busy())
                    self.assertIsNone(first.poll())
                    app.start("record_audio_transcribe")
                    second = app.process
                    self.assertIsNot(first, second)
                    until(lambda: app.progress.get("value") == 20)
                    app.stop_button.configure.reset_mock()
                    gate.touch()
                    until(lambda: app.workers[first].exited)
                    self.assertTrue(app.recording)
                    self.assertIsNone(second.poll())
                    app.stop_button.configure.assert_not_called()
                    app.finish_recording()
                    until(lambda: app.workers[second].exited)
                    self.assertTrue(all(job.terminal for job in app.workers.values()))
                    error.assert_not_called()
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.terminate()
                    process.wait(timeout=5)
                    for pipe in (process.stdin, process.stdout):
                        if not pipe.closed:
                            pipe.close()
