"""Native desktop interface; all capture/network work runs in a separate process."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading

BASE = Path(__file__).resolve().parent
EVENT_PREFIX = "@@VIDEO_EVENT@@"
DEFAULTS = {"language": "ja", "output_root": str(BASE / "output" / "local_recordings"),
            "max_candidates": 30, "max_audio_minutes": 120}
RECORDING_MODES = ("record", "record_audio", "record_audio_transcribe")
AUDIO_EXTENSIONS = frozenset((".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg",
                              ".oga", ".opus", ".wma", ".aif", ".aiff"))


@dataclass
class WorkerJob:
    number: int
    mode: str
    settings: dict
    capturing: bool = False
    terminal: bool = False
    exited: bool = False
    status: str = ""
    result_path: str | None = None
    source: str = ""


def validate_settings(value):
    result = {**DEFAULTS, **value}
    if result["language"] not in ("ja", "en"):
        raise ValueError("Invalid language")
    result["max_candidates"] = int(result["max_candidates"])
    result["max_audio_minutes"] = int(result["max_audio_minutes"])
    if not 1 <= result["max_candidates"] <= 300 or not 1 <= result["max_audio_minutes"] <= 1440:
        raise ValueError("画像は1〜300枚、音声は1〜1440分 / Images: 1–300; audio: 1–1440 minutes")
    result["output_root"] = str(Path(result["output_root"]).expanduser().resolve())
    return result


def run_worker():
    import workflow_control
    from workflow_control import JobControl

    def emit(**event):
        print("\n" + EVENT_PREFIX + json.dumps(event, ensure_ascii=False), flush=True)

    job = json.loads(sys.stdin.readline())
    control = JobControl(emit)
    workflow_control.active = control

    def commands():
        for line in sys.stdin:
            try:
                command = json.loads(line)
                action = command.get("command")
                if action == "stop":
                    control.stop.set()
                    control.pause.clear()
                elif action == "pause":
                    control.pause.set() if command.get("value") else control.pause.clear()
                elif action == "cancel":
                    control.cancel.set()
                    control.stop.set()
                elif action == "consent":
                    control.accepted = bool(command.get("accepted"))
                    control.answer.set()
            except (ValueError, TypeError):
                continue
        # Window/process disappeared: preserve completed chunks and stop safely.
        control.cancel.set()
        control.stop.set()

    try:
        # On Windows, NumPy/OpenBLAS initialization can deadlock if a pipe
        # reader is already blocked on stdin. Load it before command listening.
        import numpy  # noqa: F401

        threading.Thread(target=commands, daemon=True).start()
        from gemini_hybrid_analyzer import analyze_with_gemini, resume_analysis
        settings = validate_settings(job["settings"])
        language = settings["language"]
        options = {"max_candidates": settings["max_candidates"],
                   "max_key_slides": settings["max_candidates"],
                   "max_audio_minutes": settings["max_audio_minutes"]}
        mode = job["mode"]
        if mode == "record":
            from local_screen_recorder import record_and_analyze
            result = record_and_analyze(Path(settings["output_root"]), capture_mode="all",
                                        capture_region=job.get("region"), language=language,
                                        control=control, analysis_options=options)
        elif mode == "record_audio":
            from local_screen_recorder import record_audio
            result = record_audio(Path(settings["output_root"]), language=language, control=control, auto_compress_storage=True)
        elif mode == "record_audio_transcribe":
            from local_screen_recorder import record_audio
            rec_result = record_audio(Path(settings["output_root"]), language=language, control=control, auto_compress_storage=False)
            control.checkpoint()
            from gemini_hybrid_analyzer import transcribe_audio_only
            result = transcribe_audio_only(rec_result["audio"], language=language,
                                           max_audio_minutes=settings["max_audio_minutes"])
            result["recording_dir"] = rec_result["recording_dir"]
        elif mode == "transcribe_audio":
            from gemini_hybrid_analyzer import transcribe_audio_only
            result = transcribe_audio_only(job["path"], language=language,
                                           max_audio_minutes=settings["max_audio_minutes"],
                                           preserve_source=True)
        elif mode == "resume":
            result = resume_analysis(job["path"], language=language,
                                     max_candidates=settings["max_candidates"],
                                     max_audio_minutes=settings["max_audio_minutes"])
        elif mode == "recover":
            from recording_recovery import recover_recording
            media = recover_recording(job["path"])
            emit(kind="saved_recording", **{k: str(v) for k, v in media.items()})
            if "video" in media:
                result = analyze_with_gemini(media["video"], audio_path=media.get("audio"),
                                             language=language, **options)
            else:
                from gemini_hybrid_analyzer import transcribe_audio_only
                result = transcribe_audio_only(media["audio"], language=language,
                                               max_audio_minutes=settings["max_audio_minutes"])
        else:
            from launcher import _find_sidecar_audio
            result = analyze_with_gemini(job["path"], audio_path=_find_sidecar_audio(job["path"]),
                                         language=language, **options)
        emit(kind="complete", result=result)
        return 0
    except Exception as exc:
        emit(kind="error", message=str(exc))
        return 1


class DesktopApp:
    def __init__(self, root, language=None, initial_video=None):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk, self.root = tk, ttk, root
        self.settings_path = BASE / ".app_settings.json"
        try:
            self.settings = validate_settings(json.loads(self.settings_path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            self.settings = DEFAULTS.copy()
        if language is not None:
            self.settings["language"] = language
        self.process = None
        self.workers = {}
        self.worker_number = 0
        self.events = queue.Queue()
        self.paused = False
        self.recording = False
        self.finishing = False
        self.result_path = self.settings.get("last_result")
        self.api_key = None
        self.terminal_event = False
        self.root.title("Video Analyzer")
        height = min(800, max(640, self.root.winfo_screenheight() - 100))
        self.root.geometry(f"850x{height}")
        self.root.minsize(700, 640)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.report_callback_exception = self.callback_error
        self.build()
        self.refresh_pending()
        self.root.after(100, self.poll)
        if initial_video:
            self.root.after(300, lambda: self.start_media(initial_video))

    def tr(self, ja, en):
        return en if self.settings["language"] == "en" else ja

    def callback_error(self, _kind, error, _traceback):
        from tkinter import messagebox
        message = str(error)
        if self.api_key:
            message = message.replace(self.api_key, "[REDACTED]")
        messagebox.showerror("Video Analyzer", message)

    def build(self):
        for child in self.root.winfo_children():
            child.destroy()
        tk, ttk = self.tk, self.ttk
        body = ttk.Frame(self.root, padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=self.tr("録画から、使える記録へ", "Record. Understand. Keep."),
                  font=("Yu Gothic UI", 21, "bold")).pack(anchor="w")
        ttk.Label(body, text=self.tr("音声と候補画像は、確認後にGeminiへ送信します。",
                  "Audio and selected images are sent to Gemini only after confirmation.")).pack(anchor="w", pady=(4, 16))
        row = ttk.Frame(body)
        row.pack(fill="x")
        for ja, en, action in [("● 録画する", "● Record", self.record),
                              ("録音して文字起こし", "Record audio & transcribe", self.record_audio_prompt),
                              ("動画を選ぶ", "Choose video", self.choose_video),
                              ("音声を選ぶ", "Choose audio", self.choose_audio),
                              ("結果を見る", "View results", self.open_result),
                              ("設定", "Settings", self.configure)]:
            ttk.Button(row, text=self.tr(ja, en), command=action).pack(side="left", padx=(0, 8))
        self.status = tk.StringVar(value=self.tr("準備完了", "Ready"))
        ttk.Label(body, textvariable=self.status, wraplength=790).pack(anchor="w", pady=(20, 8))
        self.progress = ttk.Progressbar(body, maximum=100)
        self.progress.pack(fill="x")
        controls = ttk.Frame(body)
        controls.pack(fill="x", pady=8)
        self.pause_button = ttk.Button(controls, text=self.tr("一時停止／再開", "Pause / Resume"), command=self.pause, state="disabled")
        self.pause_button.pack(side="left")
        self.stop_button = ttk.Button(controls, text=self.tr("録画を終了して解析", "Finish recording & analyze"), command=self.finish_recording, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        ttk.Button(controls, text=self.tr("処理を中断（保存）", "Stop safely"), command=self.cancel).pack(side="left")
        ttk.Label(body, text=self.tr("バックグラウンド解析 — 文字起こし中も次を録音できます",
                                    "Background analysis — record the next session while transcribing")).pack(anchor="w", pady=(8, 4))
        self.worker_list = tk.Listbox(body, height=3, exportselection=False)
        self.worker_list.pack(fill="x")
        self.worker_list.bind("<Double-Button-1>", lambda event: self.open_worker_result())
        row = ttk.Frame(body)
        row.pack(fill="x", pady=4)
        ttk.Button(row, text=self.tr("選択した結果を開く", "Open selected result"), command=self.open_worker_result).pack(side="left")
        ttk.Button(row, text=self.tr("選択した解析を中断", "Stop selected analysis"), command=self.cancel_worker).pack(side="left", padx=8)
        self.update_worker_list()
        ttk.Label(body, text=self.tr("未完了の処理・録画の復旧", "Unfinished jobs / recording recovery")).pack(anchor="w", pady=(12, 4))
        self.pending = tk.Listbox(body, height=3, exportselection=False)
        self.pending.pack(fill="x")
        self.pending.bind("<Double-Button-1>", lambda event: self.resume_selected())
        row = ttk.Frame(body)
        row.pack(fill="x", pady=4)
        ttk.Button(row, text=self.tr("選択した処理を再開", "Resume selected"), command=self.resume_selected).pack(side="left")
        ttk.Button(row, text=self.tr("更新", "Refresh"), command=self.refresh_pending).pack(side="left", padx=8)
        ttk.Label(body, text=self.tr("処理ログ", "Activity log")).pack(anchor="w", pady=(8, 4))
        self.log = tk.Text(body, height=5, wrap="word", state="disabled", font=("Consolas", 10))
        self.log.pack(fill="both", expand=True)

    def busy(self):
        return any(not job.exited or process.poll() is None for process, job in self.workers.items())

    def recording_busy(self):
        # Keep the capture slot occupied until the audio/video is fully saved.
        return any(job.capturing and not job.exited for job in self.workers.values())

    def update_worker_list(self):
        selected = self.worker_list.curselection()
        previous = self.worker_rows[selected[0]] if selected and selected[0] < len(getattr(self, "worker_rows", [])) else None
        self.worker_rows = list(self.workers)
        self.worker_list.delete(0, "end")
        for process in self.worker_rows:
            job = self.workers[process]
            label = job.source or self.tr("録音・解析", "Recording / analysis")
            self.worker_list.insert("end", f"#{job.number} {label} — {job.status}")
        if previous in self.worker_rows:
            self.worker_list.selection_set(self.worker_rows.index(previous))
            self.worker_list.see(self.worker_rows.index(previous))
        elif self.worker_rows:
            self.worker_list.see(len(self.worker_rows) - 1)

    def selected_worker(self):
        selected = self.worker_list.curselection()
        return self.worker_rows[selected[0]] if selected else None

    def open_worker_result(self):
        process = self.selected_worker()
        if process is not None:
            target = self.workers[process].result_path
            if target and Path(target).exists():
                os.startfile(target)

    def cancel_worker(self):
        process = self.selected_worker()
        if process is None:
            return
        job = self.workers[process]
        if not job.exited and not job.terminal and not job.capturing:
            self.send("cancel", process=process)
            job.status = self.tr("安全に中断中…", "Stopping safely…")
            self.update_worker_list()

    def choose_video(self):
        from tkinter import filedialog
        if self.busy():
            return
        path = filedialog.askopenfilename(filetypes=[("Video", "*.mp4 *.avi *.mkv *.mov *.webm"), ("All", "*.*")])
        if path:
            self.start("analyze", path)

    def choose_audio(self):
        from tkinter import filedialog
        if self.busy():
            return
        patterns = " ".join("*" + extension for extension in sorted(AUDIO_EXTENSIONS))
        path = filedialog.askopenfilename(
            title=self.tr("文字起こしする音声を選択", "Choose audio to transcribe"),
            filetypes=[(self.tr("音声ファイル", "Audio files"), patterns), ("All", "*.*")])
        if path:
            self.start("transcribe_audio", path)

    def start_media(self, path):
        mode = "transcribe_audio" if Path(path).suffix.lower() in AUDIO_EXTENSIONS else "analyze"
        self.start(mode, path)

    def show_ffmpeg_guide(self, parent=None):
        from tkinter import messagebox
        window = self.tk.Toplevel(parent or self.root)
        window.title(self.tr("FFmpegの導入案内", "FFmpeg Setup Guide"))
        window.transient(parent or self.root)
        window.grab_set()
        window.resizable(False, False)

        ttk = self.ttk
        frame = ttk.Frame(window, padding=20)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text=self.tr("FFmpegを導入すると、音声を約1/48（Opus/M4A）に超軽量化できます。\n（未導入でも16 kHz WAVで正常に録音・文字起こし可能です）",
                         "Installing FFmpeg enables ultra-lightweight Opus/M4A compression (~1/48 size).\n(Without FFmpeg, 16 kHz WAV fallback works automatically.)"),
            font=("Yu Gothic UI", 10),
            justify="left",
        ).pack(anchor="w", pady=(0, 14))

        # WinGet command
        ttk.Label(frame, text=self.tr("方法1: PowerShellでコマンドを実行（推奨）:", "Method 1: Run in PowerShell (Recommended):"),
                  font=("Yu Gothic UI", 9, "bold")).pack(anchor="w")
        cmd_frame = ttk.Frame(frame)
        cmd_frame.pack(fill="x", pady=(4, 12))
        cmd_text = "winget install Gyan.FFmpeg"
        entry = ttk.Entry(cmd_frame, width=32)
        entry.insert(0, cmd_text)
        entry.configure(state="readonly")
        entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        def copy_cmd():
            self.root.clipboard_clear()
            self.root.clipboard_append(cmd_text)
            messagebox.showinfo("FFmpeg", self.tr("コマンドをコピーしました。", "Command copied to clipboard."), parent=window)
        ttk.Button(cmd_frame, text=self.tr("コピー", "Copy"), command=copy_cmd).pack(side="left")

        # Method 2: Portable placement
        app_bin = Path(__file__).resolve().parent / "bin"
        ttk.Label(frame, text=self.tr("方法2: 下記フォルダに ffmpeg.exe を配置:",
                                      "Method 2: Place ffmpeg.exe in the app folder:"),
                  font=("Yu Gothic UI", 9, "bold")).pack(anchor="w")
        bin_frame = ttk.Frame(frame)
        bin_frame.pack(fill="x", pady=(4, 14))
        path_entry = ttk.Entry(bin_frame, width=32)
        path_entry.insert(0, str(app_bin))
        path_entry.configure(state="readonly")
        path_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        def open_bin():
            app_bin.mkdir(parents=True, exist_ok=True)
            os.startfile(str(app_bin))
        ttk.Button(bin_frame, text=self.tr("フォルダを開く", "Open Folder"), command=open_bin).pack(side="left")

        ttk.Button(frame, text=self.tr("閉じる", "Close"), command=window.destroy).pack(anchor="e", pady=(8, 0))

    def record_audio_prompt(self):
        from tkinter import messagebox
        if self.recording_busy():
            return
        if messagebox.askokcancel(self.tr("録音の確認", "Audio recording consent"), self.tr(
                "PCで再生される音声だけを録音します。画面やマイクは記録しません。\n"
                "確認後、すぐに録音を開始します。録音中にPCで音声を再生してください。\n"
                "停止後、送信確認を経てGeminiで文字起こしし、音声とPDFを保存します。",
                "Record PC playback audio only, not the screen or microphone.\n"
                "Recording starts immediately after confirmation. Play PC audio while recording.\n"
                "After stopping and upload consent, Gemini creates a transcript PDF.")):
            self.start("record_audio_transcribe")

    def record(self):
        from tkinter import messagebox, simpledialog
        if self.recording_busy():
            return
        if not messagebox.askokcancel(self.tr("録画の確認", "Recording consent"), self.tr(
                "選択した画面とPC音声を録画します。通知・個人情報に注意してください。\n"
                "確認後、すぐに録画を開始します。録画中にPCで音声を再生してください。",
                "Your selected screen and PC audio will be recorded. Hide private information.\n"
                "Recording starts immediately after confirmation. Play PC audio while recording.")):
            return
        try:
            from local_screen_recorder import _available_monitors, _select_region_with_mouse
            monitors = _available_monitors(self.settings["language"])
            prompt = self.tr("0: 全画面、-1: 範囲選択、1以降: モニター番号", "0: all screens, -1: select area, 1+: monitor number")
            choice = simpledialog.askinteger(self.tr("録画範囲", "Capture area"), prompt, initialvalue=0,
                                              minvalue=-1, maxvalue=len(monitors) - 1)
            if choice is None:
                return
            region = (_select_region_with_mouse(monitors[0], self.settings["language"])
                      if choice == -1 else monitors[choice])
            self.start("record", region=region)
        except Exception as exc:
            messagebox.showerror("Video Analyzer", str(exc))

    def start(self, mode, path=None, region=None):
        from tkinter import messagebox, simpledialog
        blocked = self.recording_busy() if mode in RECORDING_MODES else self.busy()
        if blocked:
            return
        # Secret is never written into settings, command-line arguments or logs.
        try:
            requires_api_key = mode not in ("record_audio",)
            if requires_api_key:
                from dotenv import load_dotenv
                load_dotenv(BASE / ".env")
                key = self.api_key or os.environ.get("GEMINI_API_KEY", "")
                if not key:
                    key = simpledialog.askstring("Gemini API", self.tr("APIキー（この起動中だけ保持）", "API key (held for this session only)"), show="*")
                if not key or not key.strip():
                    return
                self.api_key = key.strip()
                env = dict(os.environ, GEMINI_API_KEY=self.api_key, PYTHONUTF8="1", PYTHONUNBUFFERED="1")
            else:
                env = dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1")
            executable = Path(sys.executable).with_name("python.exe") if os.name == "nt" else Path(sys.executable)
            process = subprocess.Popen([str(executable), str(Path(__file__).resolve()), "--worker"],
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                            cwd=BASE, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                process.stdin.write(json.dumps({"mode": mode, "path": path, "region": region, "settings": self.settings}) + "\n")
                process.stdin.flush()
            except (OSError, ValueError):
                process.stdin.close()
                process.terminate()  # Only this failed, not-yet-initialized worker.
                process.wait(timeout=5)
                process.stdout.close()
                raise
            self.worker_number += 1
            self.workers[process] = WorkerJob(
                self.worker_number, mode, self.settings.copy(), capturing=mode in RECORDING_MODES,
                status=self.tr("開始しています…", "Starting…"), source=Path(path).name if path else "")
            # Retain running jobs and only a small, useful history of completed jobs.
            finished = [p for p, job in self.workers.items() if job.exited]
            for old in finished[:-20]:
                del self.workers[old]
            self.process = process
            self.current_mode = mode
            self.terminal_event = False
            self.recording = mode in RECORDING_MODES
            self.finishing = False
            self.stop_button.configure(text=(
                self.tr("録音を終了して文字起こし", "Finish audio & transcribe")
                if mode == "record_audio_transcribe" else
                self.tr("録画を終了して解析", "Finish recording & analyze")),
                state="normal" if self.recording else "disabled")
            self.pause_button.configure(state="disabled")
            self.paused = False
            self.status.set(self.tr("開始しています…", "Starting…"))
            self.progress["value"] = 0
            self.update_worker_list()

            def read():
                try:
                    for line in process.stdout:
                        self.events.put((process, line))
                except (OSError, ValueError) as exc:
                    self.events.put((process, f"Worker output error: {exc}\n"))
                finally:
                    self.events.put((process, None))
            threading.Thread(target=read, daemon=True).start()
        except Exception as exc:
            messagebox.showerror("Video Analyzer", str(exc))

    def send(self, command, process=None, **fields):
        process = process if process is not None else self.process
        if process is not None and process.poll() is None:
            try:
                process.stdin.write(json.dumps({"command": command, **fields}) + "\n")
                process.stdin.flush()
            except (OSError, ValueError):
                pass

    def finish_recording(self):
        if not self.recording or self.finishing or not self.busy():
            return
        self.finishing = True
        self.paused = False
        self.pause_button.configure(state="disabled")
        self.stop_button.configure(state="disabled")
        self.send("stop")
        self.status.set(self.tr("録音・録画を終了して保存しています…", "Finishing and saving the recording…"))

    def pause(self):
        if self.recording:
            self.paused = not self.paused
            self.send("pause", value=self.paused)

    def cancel(self):
        if self.process in self.workers and not self.workers[self.process].exited and not self.workers[self.process].terminal:
            self.send("cancel")
            self.status.set(self.tr("安全に中断中…通信中の場合は応答を待ちます。", "Stopping safely… An active network call may need to finish."))

    def poll(self):
        from tkinter import messagebox
        while True:
            try:
                line = self.events.get_nowait()
            except queue.Empty:
                break
            if isinstance(line, tuple):
                process, line = line
            else:
                process = self.process
            job = self.workers.get(process)
            if job is None or job.exited:
                continue
            foreground = process is self.process
            if line is None:
                job.exited = True
                job.capturing = False
                # Close only this worker's pipes; never touch another recorder.
                for pipe in (process.stdin, process.stdout):
                    try:
                        pipe.close()
                    except (OSError, ValueError):
                        pass
                if not job.terminal:
                    job.status = self.tr("処理が終了しました。ログと未完了一覧を確認してください。", "Worker exited. Check the log and unfinished jobs.")
                if foreground:
                    self.recording = False
                    self.pause_button.configure(state="disabled")
                    self.stop_button.configure(state="disabled")
                    if not job.terminal:
                        self.status.set(job.status)
                self.update_worker_list()
                self.refresh_pending()
                continue
            if not line.startswith(EVENT_PREFIX):
                self.log.configure(state="normal")
                safe_line = line.replace(self.api_key, "[REDACTED]") if self.api_key else line
                if safe_line.strip():
                    self.log.insert("end", f"[#{job.number}] {safe_line}")
                if int(self.log.index("end-1c").split(".")[0]) > 1200:
                    self.log.delete("1.0", "200.0")
                self.log.see("end")
                self.log.configure(state="disabled")
                continue
            try:
                event = json.loads(line[len(EVENT_PREFIX):])
            except ValueError:
                continue
            kind = event.get("kind")
            if job.terminal:
                continue  # Late progress must not overwrite the final result.
            if kind == "consent":
                if foreground:
                    self.recording = False
                    self.pause_button.configure(state="disabled")
                    self.stop_button.configure(state="disabled")
                if event.get("audio_only") or job.mode in ("record_audio_transcribe", "transcribe_audio"):
                    consent_prompt = self.tr(
                        f"音声をGeminiへ送信します。画像は送信しません。\n"
                        f"上限: 音声{job.settings['max_audio_minutes']}分\n"
                        "API利用料金が発生する場合があります。送信しますか？",
                        f"Send audio to Gemini? Images are not uploaded.\n"
                        f"Limit: {job.settings['max_audio_minutes']} minutes.\nAPI charges may apply.",
                    )
                else:
                    consent_prompt = self.tr(
                        f"音声と候補画像をGeminiへ送信します。元動画全体は送信しません。\n"
                        f"上限: 画像{job.settings['max_candidates']}枚・音声{job.settings['max_audio_minutes']}分\n"
                        "API利用料金が発生する場合があります。送信しますか？",
                        f"Send audio and selected images to Gemini? The full video is not uploaded.\n"
                        f"Limits: {job.settings['max_candidates']} images / {job.settings['max_audio_minutes']} minutes.\nAPI charges may apply.",
                    )
                job.status = self.tr("送信確認待ち", "Awaiting upload consent")
                self.update_worker_list()
                accepted = messagebox.askyesno(self.tr("クラウド送信の確認", "Cloud upload consent"),
                                              f"#{job.number} {job.source}\n\n{consent_prompt}")
                self.send("consent", process=process, accepted=accepted)
                job.status = self.tr("送信・解析中", "Uploading / analyzing") if accepted else self.tr("送信をキャンセル", "Upload declined")
            elif kind == "recording":
                if not foreground or self.finishing or not job.capturing or job.terminal:
                    continue
                self.pause_button.configure(state="normal")
                self.stop_button.configure(state="normal")
                is_audio_only = getattr(self, "current_mode", "") in ("record_audio", "record_audio_transcribe")
                rec_label = self.tr("録音中", "Recording Audio") if is_audio_only else self.tr("録画中", "Recording")
                label = self.tr("一時停止", "Paused") if event["paused"] else rec_label
                warning = self.tr(" — 無音が続いています。再生・デバイスを確認してください。", " — No audio detected. Check playback/device.") if event["silent"] else ""
                job.status = f"{label} {event['seconds']:.0f}s | {self.tr('音量', 'Audio')} {event['level'] * 100:.0f}%{warning}"
                self.progress["value"] = event["level"] * 100
            elif kind == "progress":
                label = self.tr("文字起こし", "Transcription") if event["stage"] == "transcription" else self.tr("画面解析", "Scene analysis")
                job.status = f"{label}: {event['current']} / {event['total']}"
                if foreground:
                    self.progress["value"] = 100 * event["current"] / max(1, event["total"])
            elif kind == "waiting":
                job.status = self.tr(f"API制限・混雑のため{event['seconds']}秒待機中", f"API busy / rate limited; waiting {event['seconds']} seconds")
            elif kind == "stage":
                if foreground and event["stage"] in ("録画保存", "録音保存"):
                    self.recording = False
                    self.pause_button.configure(state="disabled")
                    self.stop_button.configure(state="disabled")
                stages = {"録画保存": "Saving recording", "録音保存": "Saving audio", "シーン候補抽出": "Selecting scenes", "音声準備": "Preparing audio", "文字起こし": "Transcribing", "画像解析": "Analyzing scenes", "PDF生成": "Creating PDF"}
                job.status = self.tr(event["stage"], stages.get(event["stage"], "Preparing"))
            elif kind == "saved_recording":
                target = event.get("video") or event.get("audio")
                if target:
                    job.result_path = str(Path(target).parent)
                    job.source = Path(target).parent.name
                job.capturing = False
                job.status = self.tr("保存完了・解析準備中（次を録音できます）", "Saved / preparing analysis (ready for next recording)")
                if foreground:
                    self.recording = False
                    self.pause_button.configure(state="disabled")
                    self.stop_button.configure(state="disabled")
            elif kind in ("error", "complete"):
                if kind == "complete":
                    job.capturing = False
                # A startup/capture failure keeps the slot until worker exit.
                job.terminal = True
                if foreground:
                    self.recording = False
                    self.pause_button.configure(state="disabled")
                    self.stop_button.configure(state="disabled")
                    self.terminal_event = True
                if kind == "complete":
                    res = event.get("result", {})
                    job.result_path = res.get("pdf") or res.get("audio") or res.get("recording_dir") or job.result_path
                    self.result_path = job.result_path or self.result_path
                    self.settings["last_result"] = self.result_path
                    try:
                        from timeline_analysis import atomic_json
                        atomic_json(self.settings_path, self.settings)
                    except OSError:
                        pass  # A saved PDF remains usable even if preferences are read-only.
                    if res.get("mode") == "audio_only":
                        job.status = self.tr("録音完了", "Recording complete")
                    else:
                        job.status = self.tr("完了（結果を開けます）", "Complete (result available)")
                    if foreground:
                        self.progress["value"] = 100
                else:
                    job.status = self.tr("中断・エラー（保存済みの処理は再開できます）", "Stopped / error (saved work can be resumed)")
                    message = event["message"]
                    message = message.replace(self.api_key, "[REDACTED]") if self.api_key else message
                    messagebox.showerror("Video Analyzer", f"#{job.number} {job.source}\n{message}")
                self.refresh_pending()
            if foreground:
                self.status.set(job.status)
            self.update_worker_list()
        self.root.after(100, self.poll)

    def refresh_pending(self):
        from gemini_hybrid_analyzer import list_pending_analyses
        self.jobs = [("resume", job["state_path"], Path(job["source"]).name) for job in list_pending_analyses()]
        root = Path(self.settings["output_root"])
        recovered_folders = set()
        for manifest in root.glob("*/.recording_parts/video_parts.json"):
            folder = manifest.parent.parent
            recovered_folders.add(folder.resolve())
            self.jobs.append(("recover", str(folder), self.tr("録画を復旧: ", "Recover recording: ") + folder.name))
        for manifest in root.glob("*/.recording_parts/audio_parts.json"):
            folder = manifest.parent.parent
            if folder.resolve() not in recovered_folders:
                recovered_folders.add(folder.resolve())
                self.jobs.append(("recover", str(folder), self.tr("録音を復旧: ", "Recover audio: ") + folder.name))
        self.pending.delete(0, "end")
        for _, _, label in self.jobs:
            self.pending.insert("end", label)

    def resume_selected(self):
        from tkinter import messagebox
        selected = self.pending.curselection()
        if selected:
            mode, path, _ = self.jobs[selected[0]]
            if mode == "recover" and not messagebox.askyesno("Video Analyzer", self.tr(
                    "保存が完了した区間から復旧します。強制終了直前の最大約60秒は復旧できない場合があります。\n元の分割ファイルは保護のため残します。続けますか？",
                    "Recover finalized chunks? Up to about 60 seconds immediately before a crash may be unavailable.\nOriginal chunks will be retained.")):
                return
            self.start(mode, path)

    def open_result(self):
        path = Path(self.result_path or self.settings["output_root"])
        path.mkdir(parents=True, exist_ok=True) if not path.exists() and not path.suffix else None
        if path.exists():
            os.startfile(str(path))

    def configure(self):
        from tkinter import filedialog, messagebox, ttk
        if self.busy():
            return
        window = self.tk.Toplevel(self.root)
        window.title(self.tr("設定", "Settings"))
        window.transient(self.root)
        window.grab_set()
        fields = {}
        for index, (key, ja, en) in enumerate([("language", "言語", "Language"), ("output_root", "録画保存先", "Recording folder"),
                                              ("max_candidates", "画像送信上限（枚）", "Image limit"),
                                              ("max_audio_minutes", "音声時間上限（分）", "Audio limit (minutes)")]):
            ttk.Label(window, text=self.tr(ja, en)).grid(row=index, column=0, sticky="w", padx=12, pady=8)
            variable = self.tk.StringVar(value=str(self.settings[key]))
            fields[key] = variable
            widget = ttk.Combobox(window, textvariable=variable, values=("ja", "en"), state="readonly") if key == "language" else ttk.Entry(window, textvariable=variable, width=48)
            widget.grid(row=index, column=1, padx=12)
        def folder():
            selected = filedialog.askdirectory(parent=window)
            if selected:
                fields["output_root"].set(selected)
        ttk.Button(window, text=self.tr("フォルダ選択", "Browse folder"), command=folder).grid(row=1, column=2)
        from audio_compression import get_compression_status
        status = get_compression_status(self.settings["language"])
        row_status = ttk.Frame(window)
        row_status.grid(row=4, columnspan=3, sticky="w", padx=12, pady=4)
        if status["has_ffmpeg"]:
            txt = self.tr(f"音声圧縮: {status['mode_description']} ({status['engine']})",
                          f"Audio compression: {status['mode_description']} ({status['engine']})")
            ttk.Label(row_status, text=txt, foreground="#166534").pack(side="left")
        else:
            txt = self.tr("音声圧縮: 16 kHz WAVフォールバック (FFmpeg未検出)",
                          "Audio compression: 16 kHz WAV fallback (No FFmpeg)")
            ttk.Label(row_status, text=txt, foreground="#92400e").pack(side="left")
            ttk.Button(row_status, text=self.tr("FFmpeg導入案内", "FFmpeg Setup"),
                       command=lambda: self.show_ffmpeg_guide(window)).pack(side="left", padx=8)

        ttk.Label(window, text=self.tr("上限は送信量の制御です。料金の上限保証ではありません。", "Limits control payload volume, not the final API bill.")).grid(row=5, columnspan=3, padx=12, pady=8)
        def save():
            try:
                self.settings = validate_settings({**self.settings, **{key: var.get() for key, var in fields.items()}})
                from timeline_analysis import atomic_json
                atomic_json(self.settings_path, self.settings)
                window.destroy()
                self.build()
                self.refresh_pending()
            except (ValueError, OSError) as exc:
                messagebox.showerror("Settings", str(exc), parent=window)
        ttk.Button(window, text=self.tr("保存", "Save"), command=save).grid(row=6, column=1, pady=14)

    def close(self):
        from tkinter import messagebox
        if self.busy():
            if messagebox.askyesno("Video Analyzer", self.tr("処理中です。安全な中断を要求しますか？\n終了するまでウィンドウは開いたままになります。", "Request a safe stop? The window will stay open until work stops.")):
                for process, job in self.workers.items():
                    if not job.exited and not job.terminal:
                        self.send("cancel", process=process)
                        job.status = self.tr("安全に中断中…", "Stopping safely…")
                self.status.set(self.tr("すべての処理を安全に中断中…", "Stopping all jobs safely…"))
                self.update_worker_list()
            return
        self.root.destroy()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--language", choices=("ja", "en"))
    parser.add_argument("video", nargs="?", help="Video or audio file")
    args = parser.parse_args(argv)
    if args.worker:
        return run_worker()
    try:
        import tkinter as tk
        root = tk.Tk()
        DesktopApp(root, args.language, args.video)
    except Exception as exc:
        message = ("Video Analyzer could not start. / 起動できませんでした。\n"
                   "Install requirements-app.txt using Python with Tk.\n"
                   "詳細確認: python desktop_app.py\n\n" + str(exc))
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "Video Analyzer", 0x10)
        elif sys.stderr is not None:
            print(message, file=sys.stderr)
        return 1
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
