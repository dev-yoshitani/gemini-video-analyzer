"""Bounded, resumable audio transcription and time-local scene context."""
from __future__ import annotations

import hashlib
import json
import math
import os
import wave
from pathlib import Path

from workflow_control import checkpoint, notify


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def validate_segments(value, duration):
    if not isinstance(value, list):
        raise ValueError("Transcript segments must be a list")
    previous = 0.0
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Invalid transcript segment")
        start, end, text = item.get("start"), item.get("end"), item.get("text")
        if (type(start) not in (int, float) or type(end) not in (int, float)
                or not math.isfinite(start) or not math.isfinite(end)
                or not 0 <= start < end <= duration + 0.25 or start < previous
                or not isinstance(text, str) or not text.strip()):
            raise ValueError("Invalid transcript time range or text")
        previous = start
    return value


def transcribe_chunks(audio, api_key, destination, transcribe, *, language="ja",
                      chunk_seconds=60, max_audio_minutes=120):
    """Cache each chunk by its exact PCM bytes, timing and language; never cache failures."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    segments = []
    with wave.open(os.fspath(audio), "rb") as source:
        rate, count = source.getframerate(), source.getnframes()
        duration = count / rate
        if duration > max_audio_minutes * 60:
            raise ValueError(f"Audio limit exceeded ({duration / 60:.1f} > {max_audio_minutes} min). "
                             "音声時間が上限を超えています。設定を確認してください。")
        if count == 0:
            raise ValueError("Audio is empty / 音声が空です")
        chunk_frames = max(1, int(chunk_seconds * rate))
        total = math.ceil(count / chunk_frames)
        for index in range(total):
            checkpoint()
            start = index * chunk_frames / rate
            data = source.readframes(chunk_frames)
            length = len(data) / (rate * source.getnchannels() * source.getsampwidth())
            digest = hashlib.sha256(data + f"{language}:{start}:{rate}:{source.getnchannels()}:{source.getsampwidth()}:v1".encode()).hexdigest()
            cache = destination / f"{index:05d}.json"
            try:
                saved = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
                if saved.get("digest") == digest:
                    validate_segments(saved["segments"], length)
            except (ValueError, OSError, KeyError, AttributeError, TypeError):
                saved = {}
            notify(kind="progress", stage="transcription", current=index + 1, total=total)
            if saved.get("digest") == digest:
                local = validate_segments(saved["segments"], length)
            else:
                part = destination / f"{index:05d}.wav"
                with wave.open(os.fspath(part), "wb") as target:
                    target.setnchannels(source.getnchannels())
                    target.setsampwidth(source.getsampwidth())
                    target.setframerate(rate)
                    target.writeframes(data)
                try:
                    text, local = transcribe(os.fspath(part), api_key, language=language,
                                             timestamps=True, duration_seconds=length)
                    if local is None:
                        raise RuntimeError("Transcription failed / 文字起こしに失敗しました")
                    # Compatibility for injected/legacy adapters with no fine timestamps.
                    if local == "" and text:
                        local = [{"start": 0, "end": length, "text": text}]
                    validate_segments(local, length)
                    atomic_json(cache, {"digest": digest, "segments": local})
                finally:
                    part.unlink(missing_ok=True)
            segments.extend({"start": item["start"] + start, "end": item["end"] + start,
                             "text": item["text"]} for item in local)
    return "\n".join(item["text"] for item in segments), segments


def scene_context(segments, start, end, previous="", language="ja"):
    relevant = [item for item in segments if item["end"] > max(0, start - 15)
                and item["start"] < end + 15]
    lines = [f"[{item['start']:.1f}-{item['end']:.1f}s] {item['text']}" for item in relevant]
    # Prefer the current scene to lengthy surrounding dialogue.
    text = "\n".join(lines)
    if len(text) > 7000:
        current = [item for item in relevant if item["end"] > start and item["start"] < end]
        text = "\n".join(f"[{i['start']:.1f}-{i['end']:.1f}s] {i['text']}" for i in current)
        text = text[:7000] + "\n[Context truncated / 文脈の一部を省略]"
    return (f"Scene interval: {start:.1f}-{end:.1f}s. Timestamps are approximate.\n"
            "Use only this local dialogue as speech evidence. Distinguish visible facts from speech. "
            "Do not invent missing words.\n" + text + "\nPrevious scene (context only): " + previous[:800])


def balanced_candidates(scenes, maximum):
    """Retain the opening and strongest change in time strata, not periodic screenshots."""
    if maximum < 1:
        raise ValueError("Image limit must be positive")
    scenes = sorted(scenes, key=lambda scene: scene.timestamp_sec)
    if len(scenes) <= maximum:
        return scenes
    if maximum == 1:
        return scenes[:1]
    if maximum == 2:
        return [scenes[0], scenes[-1]]
    remaining = scenes[1:-1]
    selected = [scenes[0], scenes[-1]]
    span = max(remaining[-1].timestamp_sec - remaining[0].timestamp_sec, 0.001)
    bins = [[] for _ in range(maximum - 2)]
    for scene in remaining:
        bucket = min(maximum - 3, int((scene.timestamp_sec - remaining[0].timestamp_sec) / span * (maximum - 2)))
        bins[bucket].append(scene)
    for bucket in bins:
        if bucket:
            selected.append(max(bucket, key=lambda scene: scene.change_score))
    ids = {id(scene) for scene in selected}
    for scene in sorted(remaining, key=lambda scene: scene.change_score, reverse=True):
        if len(selected) == maximum:
            break
        if id(scene) not in ids:
            selected.append(scene)
    return sorted(selected, key=lambda scene: scene.timestamp_sec)


def add_speech_candidates(video, frames, segments, maximum, folder):
    """Reserve visual checks for novel spoken conclusions/settings even on static screens."""
    import re
    from difflib import SequenceMatcher
    import cv2
    cues = re.compile(r"結論|重要|決定|変更|エラー|設定|結果|注意|conclu|important|decid|chang|error|setting|result|\d", re.I)
    if len(frames) >= maximum or any(frame.get("speech_anchor") for frame in frames):
        return frames
    candidates = sorted((item for item in segments if cues.search(item["text"])),
                        key=lambda item: (-len(cues.findall(item["text"])), item["start"]))
    accepted_text = []
    result = list(frames)
    capture = None
    try:
        for item in candidates:
            checkpoint()
            if len(result) >= maximum:
                break
            if any(abs(frame["timestamp_sec"] - item["start"]) < 15 for frame in result):
                continue
            if any(SequenceMatcher(None, item["text"], prior).ratio() > 0.9 for prior in accepted_text):
                continue
            if capture is None:
                capture = cv2.VideoCapture(os.fspath(video))
            capture.set(cv2.CAP_PROP_POS_MSEC, item["start"] * 1000)
            ok, image = capture.read()
            if not ok:
                continue
            folder = Path(folder)
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"speech_{len(result):05d}.jpg"
            ok, encoded = cv2.imencode(".jpg", image)
            if not ok:
                raise RuntimeError("Could not save speech-linked frame")
            path.write_bytes(encoded.tobytes())
            from local_video_analyzer import format_timestamp
            result.append({"path": str(path), "filename": path.name,
                           "timestamp_sec": item["start"], "timestamp_str": format_timestamp(item["start"]),
                           "speech_anchor": True})
            accepted_text.append(item["text"])
    finally:
        if capture is not None:
            capture.release()
    return sorted(result, key=lambda frame: frame["timestamp_sec"])
