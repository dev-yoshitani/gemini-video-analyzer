"""Process-local cooperative controls shared by CLI, GUI workers and API retries."""
from __future__ import annotations

import threading
import time


class JobCancelled(RuntimeError):
    pass


class JobControl:
    def __init__(self, emit=None):
        self.cancel = threading.Event()
        self.stop = threading.Event()
        self.pause = threading.Event()
        self.emit = emit or (lambda **event: None)
        self.answer = threading.Event()
        self.accepted = False

    def checkpoint(self):
        if self.cancel.is_set():
            raise JobCancelled("Stopped safely; completed work is saved. / 中断しました。完了済みの処理は保存されています。")

    def confirm(self, **details):
        self.answer.clear()
        self.emit(kind="consent", **details)
        while not self.answer.wait(0.2):
            self.checkpoint()
        return self.accepted


active = None


def checkpoint():
    if active is not None:
        active.checkpoint()


def notify(**event):
    if active is not None:
        active.emit(**event)


def wait(seconds):
    if active is None:
        time.sleep(seconds)
    elif active.cancel.wait(seconds):
        active.checkpoint()
