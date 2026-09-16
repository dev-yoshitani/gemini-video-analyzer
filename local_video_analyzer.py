"""高精度AI解析で使う、PC内シーン候補抽出モジュール。

動画をGeminiへ送る前に、実際の画面変化だけをPC内で検出する。
完全ローカル文字起こしモードは廃止済みで、このモジュールは直接起動しない。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

try:
    import cv2
    import numpy as np
except ImportError:  # インストール案内を出すため、モジュール自体は読み込めるようにする
    cv2 = None
    np = None


APP_VERSION = "1.0.0"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".wmv", ".m4v"}
SENSITIVITY_THRESHOLDS = {
    "low": 0.30,
    "normal": 0.22,
    "high": 0.16,
}


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass
class FrameSignature:
    gray: Any = field(repr=False)
    histogram: Any = field(repr=False)
    perceptual_hash: Any = field(repr=False)
    edges: Any = field(repr=False)


@dataclass
class Scene:
    index: int
    timestamp_sec: float
    timestamp: str
    image: str
    change_score: float
    transcript: str = ""
    _signature: FrameSignature | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "timestamp_sec": self.timestamp_sec,
            "timestamp": self.timestamp,
            "image": self.image,
            "change_score": self.change_score,
            "transcript": self.transcript,
        }


def _require_vision_dependencies() -> None:
    if cv2 is None or np is None:
        raise RuntimeError(
            "ローカル映像解析用ライブラリがありません。次を実行してください:\n"
            "  pip install -r requirements-local.txt"
        )


def format_timestamp(seconds: float, milliseconds: bool = True) -> str:
    seconds = max(0.0, float(seconds))
    total_ms = int(round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    if milliseconds:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _srt_timestamp(seconds: float) -> str:
    return format_timestamp(seconds).replace(".", ",")


def _safe_stem(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" ._")
    return cleaned[:80] or "video"


def make_signature(frame: Any, ignore_bottom_ratio: float = 0.14) -> FrameSignature:
    """字幕などによる誤検出を減らした、軽量なフレーム指紋を作る。"""
    _require_vision_dependencies()
    height, width = frame.shape[:2]
    useful_height = max(1, int(height * (1.0 - ignore_bottom_ratio)))
    cropped = frame[:useful_height, :]
    resized = cv2.resize(cropped, (320, 180), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)

    hsv = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV)
    histogram = cv2.calcHist([hsv], [0, 1], None, [16, 8], [0, 180, 0, 256])
    cv2.normalize(histogram, histogram, alpha=1.0, norm_type=cv2.NORM_L1)

    hash_source = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
    dct = cv2.dct(np.float32(hash_source))[:8, :8]
    median = float(np.median(dct.flatten()[1:]))
    perceptual_hash = dct > median
    edges = cv2.Canny(gray, 70, 160)
    return FrameSignature(gray, histogram, perceptual_hash, edges)


def signature_distance(first: FrameSignature, second: FrameSignature) -> dict[str, float]:
    """0（同一）から1（大きな変化）の範囲で、複数特徴の差を返す。"""
    _require_vision_dependencies()
    pixel_diff = cv2.absdiff(first.gray, second.gray)
    mean_delta = float(np.mean(pixel_diff) / 255.0)
    changed_ratio = float(np.mean(pixel_diff >= 18))
    histogram_delta = float(
        cv2.compareHist(first.histogram, second.histogram, cv2.HISTCMP_BHATTACHARYYA)
    )
    hash_delta = float(np.mean(first.perceptual_hash != second.perceptual_hash))
    edge_delta = float(np.mean(first.edges != second.edges))

    detail = min(1.0, 0.65 * min(1.0, changed_ratio * 3.0) + 0.35 * min(1.0, mean_delta * 6.0))
    structure = min(1.0, 0.70 * hash_delta + 0.30 * min(1.0, edge_delta * 3.0))
    visual_score = 0.55 * structure + 0.45 * detail
    color_score = 0.65 * histogram_delta + 0.35 * detail
    score = max(visual_score, color_score)
    return {
        "score": round(float(score), 6),
        "pixel": round(mean_delta, 6),
        "changed": round(changed_ratio, 6),
        "histogram": round(histogram_delta, 6),
        "hash": round(hash_delta, 6),
        "edge": round(edge_delta, 6),
    }


def is_near_duplicate(
    signature: FrameSignature,
    existing: Sequence[FrameSignature],
    duplicate_threshold: float,
) -> bool:
    for previous in existing:
        metrics = signature_distance(signature, previous)
        if metrics["score"] <= duplicate_threshold:
            return True

        # 色のハイライト、選択状態、カーソルなどが変化しても、文字や画面構造が
        # ほぼ同じなら同一内容として扱う。新しい文字が追加された場合は
        # perceptual hash と edge の差が大きくなるため残る。
        same_content = (
            metrics["hash"] <= 0.07
            and metrics["edge"] <= 0.012
            and metrics["changed"] <= 0.08
        )
        if same_content and duplicate_threshold > 0:
            return True
    return False


def deduplicate_image_candidates(
    frames: Sequence[dict[str, Any]],
    duplicate_threshold: float = 0.08,
) -> tuple[list[dict[str, Any]], int]:
    """画像解析の前に、見た目や内容がほぼ同じ候補をローカルで除外する。"""
    _require_vision_dependencies()
    unique_frames: list[dict[str, Any]] = []
    signatures: list[FrameSignature] = []
    removed = 0
    exact_hashes = set()

    for frame_info in frames:
        image_path = Path(frame_info["path"])
        if duplicate_threshold == 0:
            import hashlib
            try:
                digest = hashlib.sha256(image_path.read_bytes()).digest()
            except OSError:
                unique_frames.append(frame_info)
                continue
            if digest in exact_hashes:
                removed += 1
            else:
                exact_hashes.add(digest)
                unique_frames.append(frame_info)
            continue
        try:
            encoded = np.frombuffer(image_path.read_bytes(), dtype=np.uint8)
            image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        except OSError:
            image = None

        # 読み取れない画像はここで失わず、後段で明確なエラーを出せるよう残す。
        if image is None:
            unique_frames.append(frame_info)
            continue

        signature = make_signature(image)
        if is_near_duplicate(signature, signatures, duplicate_threshold):
            removed += 1
            continue
        unique_frames.append(frame_info)
        signatures.append(signature)

    return unique_frames, removed


def is_meaningful_scene_change(metrics: dict[str, float], scene_threshold: float) -> bool:
    """局所的な人物の動きより、画面構成・文字・色の変化を優先する。"""
    if metrics["score"] < scene_threshold:
        return False
    return (
        metrics["histogram"] >= 0.12
        or metrics["changed"] >= 0.12
        or metrics["edge"] >= 0.014
    )


def _write_jpeg(path: Path, frame: Any, quality: int = 92) -> None:
    """日本語を含むWindowsパスでも確実にJPEGを書き出す。"""
    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError(f"画像のエンコードに失敗しました: {path}")
    path.write_bytes(encoded.tobytes())


def _save_scene(
    scenes: list[Scene],
    frame: Any,
    signature: FrameSignature,
    timestamp_sec: float,
    score: float,
    scenes_dir: Path,
) -> Scene:
    index = len(scenes) + 1
    timestamp = format_timestamp(timestamp_sec)
    filename_time = timestamp.replace(":", "-").replace(".", "-")
    filename = f"場面_{index:03d}_{filename_time}.jpg"
    _write_jpeg(scenes_dir / filename, frame)
    scene = Scene(
        index=index,
        timestamp_sec=round(timestamp_sec, 3),
        timestamp=timestamp,
        image=f"{scenes_dir.name}/{filename}",
        change_score=round(score, 4),
        _signature=signature,
    )
    scenes.append(scene)
    return scene


def extract_scenes(
    video_path: Path,
    scenes_dir: Path,
    *,
    scan_fps: float = 4.0,
    scene_threshold: float = SENSITIVITY_THRESHOLDS["normal"],
    duplicate_threshold: float = 0.10,
    min_scene_duration: float = 0.75,
    settle_duration: float = 1.25,
    ignore_bottom_ratio: float = 0.14,
    language: str = "ja",
) -> tuple[list[Scene], dict[str, float]]:
    """一定間隔ではなく、安定して成立したシーン変化だけを画像化する。"""
    _require_vision_dependencies()
    english = language == "en"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(os.fspath(video_path))
    if not capture.isOpened():
        if english:
            raise RuntimeError(f"Could not open the video: {video_path}")
        raise RuntimeError(f"動画を開けません: {video_path}")

    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if not math.isfinite(fps) or fps <= 0:
        fps = 30.0
    duration = frame_count / fps if frame_count > 0 else 0.0
    sample_step = max(1, int(round(fps / max(0.25, scan_fps))))
    stable_threshold = max(0.035, duplicate_threshold * 0.85)

    scenes: list[Scene] = []
    accepted_signatures: list[FrameSignature] = []
    reference_signature: FrameSignature | None = None
    reference_time = 0.0
    pending_frame = None
    pending_signature: FrameSignature | None = None
    pending_started = 0.0
    frame_number = -1
    sampled = 0
    rejected_duplicates = 0

    if english:
        print("\n[1/3] Detecting meaningful scene changes...")
        print(
            f"  Duration: {format_timestamp(duration, milliseconds=False)} / "
            f"Scan rate: {scan_fps:g} fps / Change threshold: {scene_threshold:.2f}"
        )
    else:
        print("\n[1/3] シーン変化を解析中...")
        print(
            f"  長さ: {format_timestamp(duration, milliseconds=False)} / "
            f"走査: {scan_fps:g} fps / 変化閾値: {scene_threshold:.2f}"
        )

    try:
        while capture.grab():
            from workflow_control import checkpoint
            checkpoint()
            frame_number += 1
            if frame_number % sample_step != 0:
                continue
            ok, frame = capture.retrieve()
            if not ok or frame is None:
                continue
            sampled += 1
            current_time = frame_number / fps
            signature = make_signature(frame, ignore_bottom_ratio)

            if reference_signature is None:
                _save_scene(scenes, frame, signature, current_time, 1.0, scenes_dir)
                accepted_signatures.append(signature)
                reference_signature = signature
                reference_time = current_time
                continue

            reference_metrics = signature_distance(reference_signature, signature)
            reference_delta = reference_metrics["score"]
            if pending_signature is None:
                if (
                    is_meaningful_scene_change(reference_metrics, scene_threshold)
                    and current_time - reference_time >= min_scene_duration
                ):
                    pending_frame = frame.copy()
                    pending_signature = signature
                    pending_started = current_time
                elif reference_delta >= scene_threshold and current_time - reference_time >= 2.5:
                    # 人物の姿勢など、局所的な変化が長時間蓄積するのをリセットする。
                    reference_signature = signature
                    reference_time = current_time
                continue

            # 一瞬だけ表示された通知・フラッシュは、元の場面に戻れば捨てる。
            if reference_delta < scene_threshold * 0.70:
                pending_frame = None
                pending_signature = None
                continue

            pending_delta = signature_distance(pending_signature, signature)["score"]
            settled = pending_delta <= stable_threshold
            settle_timed_out = current_time - pending_started >= settle_duration
            if not settled and not settle_timed_out:
                pending_frame = frame.copy()
                pending_signature = signature
                continue

            candidate_frame = frame if settled else pending_frame
            candidate_signature = signature if settled else pending_signature
            # 画像は遷移後の安定したフレーム、時刻は変化が始まった瞬間を使う。
            candidate_time = pending_started
            final_delta = signature_distance(reference_signature, candidate_signature)["score"]

            # 保存済みの全場面と比較し、戻ってきた同一画面も含めて重複を除く。
            if is_near_duplicate(candidate_signature, accepted_signatures, duplicate_threshold):
                rejected_duplicates += 1
            else:
                scene = _save_scene(
                    scenes,
                    candidate_frame,
                    candidate_signature,
                    candidate_time,
                    final_delta,
                    scenes_dir,
                )
                accepted_signatures.append(candidate_signature)
                if english:
                    print(f"  {scene.index:03d}: {scene.timestamp}  change {scene.change_score:.3f}")
                else:
                    print(f"  {scene.index:03d}: {scene.timestamp}  変化量 {scene.change_score:.3f}")

            # 重複で保存しなくても現時点を基準にし、同じ候補の連続検出を防ぐ。
            reference_signature = candidate_signature
            reference_time = current_time
            pending_frame = None
            pending_signature = None
    finally:
        capture.release()

    # 動画末尾で成立した変化は、次の安定確認フレームがなくても採用候補にする。
    if (
        pending_signature is not None
        and pending_frame is not None
        and reference_signature is not None
    ):
        final_metrics = signature_distance(reference_signature, pending_signature)
        if is_meaningful_scene_change(final_metrics, scene_threshold):
            if is_near_duplicate(pending_signature, accepted_signatures, duplicate_threshold):
                rejected_duplicates += 1
            else:
                scene = _save_scene(
                    scenes,
                    pending_frame,
                    pending_signature,
                    pending_started,
                    final_metrics["score"],
                    scenes_dir,
                )
                if english:
                    print(f"  {scene.index:03d}: {scene.timestamp}  change {scene.change_score:.3f}")
                else:
                    print(f"  {scene.index:03d}: {scene.timestamp}  変化量 {scene.change_score:.3f}")

    if not scenes:
        if english:
            raise RuntimeError("No frames could be read from the video. Check the video format.")
        raise RuntimeError("動画からフレームを読み取れませんでした。動画形式を確認してください。")

    if english:
        print(
            f"  Complete: selected {len(scenes)} scenes "
            f"(removed {rejected_duplicates} similar scenes)"
        )
    else:
        print(f"  完了: {len(scenes)}場面を採用（類似 {rejected_duplicates}場面を除外）")
    metadata = {
        "duration_sec": round(duration, 3),
        "fps": round(fps, 3),
        "frame_count": frame_count,
        "sampled_frames": sampled,
        "rejected_duplicates": rejected_duplicates,
    }
    return scenes, metadata


def _resolve_whisper_device(device: str) -> tuple[str, str]:
    if device != "auto":
        return (device, "float16" if device == "cuda" else "int8")
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except (ImportError, RuntimeError):
        pass
    return "cpu", "int8"


def transcribe_locally(
    video_path: Path,
    *,
    model: str = "small",
    language: str | None = None,
    device: str = "auto",
    offline: bool = False,
) -> tuple[list[TranscriptSegment], dict[str, Any]]:
    raise RuntimeError(
        "完全ローカル文字起こしは廃止されました。Start.bat の高精度AI解析を使ってください。"
    )

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "ローカル文字起こし用の faster-whisper がありません。次を実行してください:\n"
            "  pip install -r requirements-local.txt"
        ) from exc

    actual_device, compute_type = _resolve_whisper_device(device)
    print("\n[2/3] 音声をローカルで文字起こし中...")
    print(f"  Whisperモデル: {model} / デバイス: {actual_device}")
    if not offline:
        print("  ※ モデル未取得の場合だけ、初回にモデルデータをダウンロードします。")

    try:
        whisper = WhisperModel(
            model,
            device=actual_device,
            compute_type=compute_type,
            local_files_only=offline,
        )
        generated, info = whisper.transcribe(
            os.fspath(video_path),
            language=language,
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            condition_on_previous_text=True,
        )
        segments: list[TranscriptSegment] = []
        for item in generated:
            text = item.text.strip()
            if not text:
                continue
            segment = TranscriptSegment(round(item.start, 3), round(item.end, 3), text)
            segments.append(segment)
            print(f"  [{format_timestamp(segment.start, False)}] {text}")
    except Exception as exc:
        message = str(exc)
        if offline and ("model" in message.lower() or "local" in message.lower()):
            message += "\nオフライン用モデルが見つかりません。最初に --offline なしで一度実行してください。"
        raise RuntimeError(f"ローカル文字起こしに失敗しました: {message}") from exc

    detected_language = getattr(info, "language", language or "unknown")
    probability = float(getattr(info, "language_probability", 0.0) or 0.0)
    print(f"  完了: {len(segments)}区間 / 検出言語: {detected_language}")
    return segments, {
        "model": model,
        "device": actual_device,
        "compute_type": compute_type,
        "language": detected_language,
        "language_probability": round(probability, 4),
    }


def attach_transcript_to_scenes(
    scenes: list[Scene], segments: Sequence[TranscriptSegment]
) -> None:
    if not scenes:
        return
    for index, scene in enumerate(scenes):
        start = 0.0 if index == 0 else scene.timestamp_sec
        end = scenes[index + 1].timestamp_sec if index + 1 < len(scenes) else math.inf
        related = [
            segment.text
            for segment in segments
            if start <= segment.start < end
        ]
        scene.transcript = " ".join(related).strip()


def _write_transcript_files(output_dir: Path, segments: Sequence[TranscriptSegment]) -> None:
    text_lines = [segment.text for segment in segments]
    (output_dir / "transcript.txt").write_text(
        "\n".join(text_lines) + ("\n" if text_lines else ""), encoding="utf-8"
    )

    timestamped = [
        f"[{format_timestamp(segment.start, False)} - "
        f"{format_timestamp(segment.end, False)}] {segment.text}"
        for segment in segments
    ]
    (output_dir / "transcript_timestamped.txt").write_text(
        "\n".join(timestamped) + ("\n" if timestamped else ""), encoding="utf-8"
    )

    srt_blocks = [
        f"{index}\n{_srt_timestamp(segment.start)} --> "
        f"{_srt_timestamp(segment.end)}\n{segment.text}"
        for index, segment in enumerate(segments, 1)
    ]
    (output_dir / "transcript.srt").write_text(
        "\n\n".join(srt_blocks) + ("\n" if srt_blocks else ""), encoding="utf-8"
    )


def _write_ai_markdown(
    output_dir: Path,
    video_path: Path,
    scenes: Sequence[Scene],
    segments: Sequence[TranscriptSegment],
    metadata: dict[str, Any],
) -> Path:
    lines = [
        f"# {video_path.stem} — シーン画像と文字起こし",
        "",
        f"- 元動画: `{video_path.name}`",
        f"- 動画時間: {format_timestamp(metadata.get('duration_sec', 0), False)}",
        f"- 抽出画像: {len(scenes)}枚",
        f"- 文字起こし区間: {len(segments)}件",
        "- 処理方式: ローカルシーン検出 + ローカルWhisper文字起こし",
        "",
        "> 各画像の直後に、その場面の表示中に話された内容を配置しています。",
        "",
    ]
    for scene in scenes:
        lines.extend([
            f"## Scene {scene.index:03d} — {scene.timestamp}",
            "",
            f"![Scene {scene.index:03d}]({scene.image})",
            "",
            "### この場面の音声",
            "",
            scene.transcript or "（音声なし、または発話を検出できませんでした）",
            "",
        ])
    output_path = output_dir / "AI_INPUT.md"
    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return output_path


def analyze_video(
    video_path: str | os.PathLike[str],
    *,
    transcription_source: str | os.PathLike[str] | None = None,
    output_dir: str | os.PathLike[str] | None = None,
    sensitivity: str = "normal",
    scene_threshold: float | None = None,
    duplicate_threshold: float = 0.10,
    scan_fps: float = 4.0,
    min_scene_duration: float = 0.75,
    ignore_bottom_ratio: float = 0.14,
    transcribe: bool = True,
    whisper_model: str = "small",
    language: str | None = None,
    device: str = "auto",
    offline: bool = False,
) -> dict[str, Any]:
    raise RuntimeError(
        "完全ローカル解析は廃止されました。Start.bat の高精度AI解析を使ってください。"
    )

    source = Path(video_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"動画ファイルが見つかりません: {source}")
    if sensitivity not in SENSITIVITY_THRESHOLDS:
        raise ValueError(f"未対応の感度です: {sensitivity}")
    if not 0.0 <= duplicate_threshold <= 1.0:
        raise ValueError("重複判定閾値は0〜1で指定してください。")
    if scan_fps <= 0 or min_scene_duration < 0:
        raise ValueError("走査頻度は正数、最小場面時間は0以上で指定してください。")
    if not 0.0 <= ignore_bottom_ratio < 1.0:
        raise ValueError("画面下部の除外率は0以上1未満で指定してください。")
    if source.suffix.lower() not in VIDEO_EXTENSIONS:
        print(f"警告: 一般的でない拡張子です。そのまま解析を試みます: {source.suffix}")

    transcript_source = source
    if transcription_source is not None:
        transcript_source = Path(transcription_source).expanduser().resolve()
        if not transcript_source.is_file():
            raise FileNotFoundError(f"文字起こし音声が見つかりません: {transcript_source}")

    if output_dir is None:
        timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        destination = source.parent / f"{_safe_stem(source.stem)}_local_{timestamp}"
    else:
        destination = Path(output_dir).expanduser().resolve()
    scenes_dir = destination / "scenes"
    destination.mkdir(parents=True, exist_ok=True)

    threshold = (
        scene_threshold
        if scene_threshold is not None
        else SENSITIVITY_THRESHOLDS[sensitivity]
    )
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("シーン変化閾値は0〜1で指定してください。")
    print("=" * 64)
    print("  Local Video Scene & Transcript Analyzer")
    print("  動画・音声は外部APIへ送信しません")
    print("=" * 64)
    print(f"  入力: {source}")
    print(f"  出力: {destination}")

    scenes, video_metadata = extract_scenes(
        source,
        scenes_dir,
        scan_fps=scan_fps,
        scene_threshold=threshold,
        duplicate_threshold=duplicate_threshold,
        min_scene_duration=min_scene_duration,
        ignore_bottom_ratio=ignore_bottom_ratio,
    )

    transcript_segments: list[TranscriptSegment] = []
    transcription_metadata: dict[str, Any] = {"enabled": False}
    if transcribe:
        try:
            transcript_segments, transcription_metadata = transcribe_locally(
                transcript_source,
                model=whisper_model,
                language=language,
                device=device,
                offline=offline,
            )
            transcription_metadata.update({"enabled": True, "success": True})
            transcription_metadata["source_file"] = transcript_source.name
        except RuntimeError as exc:
            # 音声トラックなし・モデル未取得でも、抽出済み画像は整理して残す。
            print(f"  警告: {exc}", file=sys.stderr)
            print("  画像の抽出結果だけを保存して処理を続けます。", file=sys.stderr)
            transcription_metadata = {
                "enabled": True,
                "success": False,
                "error": str(exc),
            }
    else:
        print("\n[2/3] 文字起こしはスキップしました。")

    attach_transcript_to_scenes(scenes, transcript_segments)
    print("\n[3/3] AIへ渡しやすい形式に整理中...")
    _write_transcript_files(destination, transcript_segments)
    combined_metadata = {
        **video_metadata,
        "app_version": APP_VERSION,
        "scene_threshold": threshold,
        "duplicate_threshold": duplicate_threshold,
        "scan_fps": scan_fps,
        "sensitivity": sensitivity,
    }
    markdown_path = _write_ai_markdown(
        destination, source, scenes, transcript_segments, combined_metadata
    )
    manifest = {
        "version": APP_VERSION,
        "created_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": {"file": source.name, "path": os.fspath(source)},
        "privacy": {"media_uploaded": False, "processing": "local"},
        "video": combined_metadata,
        "transcription": transcription_metadata,
        "scenes": [scene.to_dict() for scene in scenes],
        "transcript": [asdict(segment) for segment in transcript_segments],
    }
    manifest_path = destination / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    result = {
        "success": True,
        "output_dir": os.fspath(destination),
        "scene_count": len(scenes),
        "transcript_segment_count": len(transcript_segments),
        "ai_input": os.fspath(markdown_path),
        "manifest": os.fspath(manifest_path),
    }
    print("\n" + "=" * 64)
    print(f"  完了: {len(scenes)}枚の重要場面を抽出しました")
    print(f"  AI用Markdown: {markdown_path}")
    print(f"  画像フォルダ: {scenes_dir}")
    print("=" * 64)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="動画から実際のシーン変化だけを抽出し、音声もローカルで文字起こしします。"
    )
    parser.add_argument("video", help="解析する動画ファイル")
    parser.add_argument("--audio", help="動画とは別に録音した文字起こし用音声ファイル")
    parser.add_argument("-o", "--output-dir", help="出力先（省略時は動画と同じ場所）")
    parser.add_argument(
        "--sensitivity", choices=sorted(SENSITIVITY_THRESHOLDS), default="normal",
        help="シーン検出感度: low=厳選、normal=標準、high=細かく抽出",
    )
    parser.add_argument("--scene-threshold", type=float, help="シーン変化閾値を直接指定")
    parser.add_argument("--duplicate-threshold", type=float, default=0.10,
                        help="この値以下の類似画像を重複扱い（デフォルト: 0.10）")
    parser.add_argument("--scan-fps", type=float, default=4.0,
                        help="変化検出の走査頻度（デフォルト: 4fps。画像出力間隔ではありません）")
    parser.add_argument("--min-scene-duration", type=float, default=0.75,
                        help="短い通知等を無視する最小場面時間（秒）")
    parser.add_argument("--include-subtitles", action="store_true",
                        help="画面下部も変化判定に含める（通常は字幕による誤検出を防ぐため除外）")
    parser.add_argument("--no-transcribe", action="store_true", help="音声文字起こしを省略")
    parser.add_argument("--whisper-model", default="small",
                        choices=["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"],
                        help="ローカルWhisperモデル（デフォルト: small）")
    parser.add_argument("--language", help="音声言語（ja、en等。省略時は自動判定）")
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto",
                        help="文字起こしデバイス（デフォルト: auto）")
    parser.add_argument("--offline", action="store_true",
                        help="モデルのダウンロードも禁止し、取得済みモデルだけを使用")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    del argv
    print(
        "このファイルは高精度AI解析の内部シーン抽出用です。\n"
        "Start.bat から高精度AI解析を実行してください。",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
