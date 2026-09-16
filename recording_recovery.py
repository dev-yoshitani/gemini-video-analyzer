"""Closed recording chunks survive an interrupted process; originals are never deleted here."""
from __future__ import annotations

import os
import wave
from pathlib import Path

from timeline_analysis import atomic_json


def merge_audio(parts, target):
    target = Path(target)
    temporary = target.with_name(target.stem + ".assembling.wav")
    params = None
    total = 0
    parts = list(parts)
    if not parts:
        raise ValueError("No recoverable audio")
    with wave.open(os.fspath(parts[0]), "rb") as first:
        params = (first.getnchannels(), first.getsampwidth(), first.getframerate())
    try:
        with wave.open(os.fspath(temporary), "wb") as out:
            out.setnchannels(params[0])
            out.setsampwidth(params[1])
            out.setframerate(params[2])
            for part in parts:
                with wave.open(os.fspath(part), "rb") as source:
                    current = (source.getnchannels(), source.getsampwidth(), source.getframerate())
                    if params is None:
                        params = current
                        out.setnchannels(params[0])
                        out.setsampwidth(params[1])
                        out.setframerate(params[2])
                    if params != current:
                        raise ValueError("Audio format changed during recording")
                    while True:
                        data = source.readframes(65536)
                        if not data:
                            break
                        total += len(data)
                        out.writeframesraw(data)
        if not total:
            raise ValueError("No recoverable audio")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def merge_video(parts, target, fps):
    import cv2
    target = Path(target).with_suffix(".mp4")
    temporary = target.with_name(target.stem + ".assembling.mp4")
    writer = None
    total = 0
    size = None
    try:
        for part in parts:
            source = cv2.VideoCapture(os.fspath(part))
            part_frames = 0
            try:
                while True:
                    ok, frame = source.read()
                    if not ok:
                        break
                    current = (frame.shape[1], frame.shape[0])
                    if writer is None:
                        size = current
                        writer = cv2.VideoWriter(os.fspath(temporary), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
                        if not writer.isOpened():
                            raise RuntimeError("Could not create merged recording")
                    if current != size:
                        raise ValueError("Recording dimensions changed")
                    writer.write(frame)
                    total += 1
                    part_frames += 1
                if not part_frames:
                    raise ValueError(f"Unreadable recording chunk: {part.name}")
            finally:
                source.release()
        if writer is not None:
            writer.release()
            writer = None
        if not total:
            raise ValueError("No recoverable screen frames")
        os.replace(temporary, target)
    finally:
        if writer is not None:
            writer.release()
        temporary.unlink(missing_ok=True)
    return target


def save_parts(folder, kind, parts, **metadata):
    atomic_json(Path(folder) / f"{kind}_parts.json",
                {"parts": [Path(p).name for p in parts], **metadata})


def recover_recording(folder):
    """Use only finalized manifest entries, excluding a possibly torn active chunk."""
    import json
    folder = Path(folder).resolve()
    parts_dir = folder / ".recording_parts"
    result = {}
    for kind in ("audio", "video"):
        manifest = parts_dir / f"{kind}_parts.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        parts = []
        for name in data["parts"]:
            if Path(name).name != name:
                raise ValueError("Invalid recording chunk path")
            part = (parts_dir / name).resolve()
            if part.parent != parts_dir.resolve():
                raise ValueError("Invalid recording chunk path")
            parts.append(part)
        # Never overwrite a user's existing complete recording during recovery.
        import uuid
        stem = f"Recovered_{kind}_{uuid.uuid4().hex[:8]}"
        result[kind] = (merge_audio(parts, folder / f"{stem}.wav") if kind == "audio"
                        else merge_video(parts, folder / f"{stem}.mp4", data["fps"]))
    return result
