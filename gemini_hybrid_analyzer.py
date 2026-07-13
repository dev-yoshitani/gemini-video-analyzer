"""ローカル候補抽出とGemini解析を組み合わせ、解析結果PDFを生成する。"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import wave
from pathlib import Path
from typing import Any, Sequence


STATE_FILENAME = ".analysis_state.json"
STATE_VERSION = 1


def _application_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _pending_jobs_dir() -> Path:
    return _application_root() / "output" / "pending_analyses"


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _job_marker_path(destination: Path) -> Path:
    digest = hashlib.sha256(os.fspath(destination).encode("utf-8")).hexdigest()[:20]
    return _pending_jobs_dir() / f"{digest}.json"


def _register_pending_job(state_path: Path, state: dict[str, Any]) -> None:
    marker = _job_marker_path(state_path.parent)
    _write_json_atomic(marker, {
        "state_path": os.fspath(state_path),
        "source": state["source"]["path"],
        "created_at": state["created_at"],
        "current_stage": state.get("current_stage", "準備中"),
    })


def _remove_pending_job(destination: Path) -> None:
    marker = _job_marker_path(destination)
    try:
        marker.unlink()
    except FileNotFoundError:
        pass


def list_pending_analyses() -> list[dict[str, Any]]:
    """ランチャーに表示できる未完了の高精度解析一覧を返す。"""
    jobs = []
    directory = _pending_jobs_dir()
    if not directory.is_dir():
        return jobs
    for marker in directory.glob("*.json"):
        try:
            job = _read_json(marker)
            state_path = Path(job["state_path"])
            if not state_path.is_file():
                continue
            state = _read_json(state_path)
            if state.get("status") == "complete":
                continue
            jobs.append({
                **job,
                "current_stage": state.get("current_stage", job.get("current_stage", "不明")),
                "completed_stages": state.get("completed_stages", []),
            })
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(jobs, key=lambda item: item.get("created_at", ""), reverse=True)


def _source_identity(source: Path) -> dict[str, Any]:
    stat = source.stat()
    return {
        "path": os.fspath(source),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _validate_source_identity(source: Path, identity: dict[str, Any]) -> None:
    current = _source_identity(source)
    if current["size"] != identity.get("size") or current["mtime_ns"] != identity.get("mtime_ns"):
        raise RuntimeError("元動画が解析開始後に変更されたため、安全に再開できません。")


def _set_stage(
    state_path: Path,
    state: dict[str, Any],
    stage: str,
    *,
    completed: bool = False,
    artifact: str | os.PathLike[str] | None = None,
) -> None:
    state["current_stage"] = stage
    state["status"] = "in_progress"
    if completed and stage not in state["completed_stages"]:
        state["completed_stages"].append(stage)
    if artifact is not None:
        state.setdefault("artifacts", {})[stage] = os.fspath(artifact)
    state["updated_at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    _write_json_atomic(state_path, state)
    _register_pending_job(state_path, state)


def _artifact_for(
    state: dict[str, Any],
    stage: str,
    *,
    must_exist: bool = True,
) -> Path | None:
    value = state.get("artifacts", {}).get(stage)
    if not value:
        return None
    path = Path(value)
    if must_exist and not path.is_file():
        return None
    return path


def _ask_cloud_consent() -> bool:
    print("\n" + "=" * 64)
    print("  高精度AI解析モード（Gemini）")
    print("=" * 64)
    print("  元動画全体は送信しません。")
    print("  次のデータだけをGemini APIへ送信します:")
    print("  ・動画からPC内で取り出した音声")
    print("  ・PC内で絞り込んだシーン候補画像")
    print("  Gemini上の一時音声ファイルは文字起こし後に削除します。")
    try:
        answer = input("\n上記に同意して高精度解析を開始しますか？ [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer == "y"


def _get_api_key() -> str:
    import audio_transcriber

    key = os.environ.get("GEMINI_API_KEY", audio_transcriber.API_KEY).strip()
    if key:
        return key
    if not sys.stdin.isatty():
        raise RuntimeError("GEMINI_API_KEYが設定されていません。")
    print("\nGemini APIキーが設定されていません。")
    print("取得先: https://aistudio.google.com/apikey")
    key = input("APIキーを貼り付けてEnterを押してください: ").strip()
    if not key:
        raise RuntimeError("APIキーが入力されませんでした。")
    audio_transcriber.save_api_key_to_env(key)
    print("APIキーをこのPCの.envファイルへ保存しました。")
    return key


def extract_audio_locally(video_path: Path, output_path: Path) -> Path:
    """PyAVで動画の音声だけを16kHzモノラルWAVへ変換する。"""
    try:
        import av
    except ImportError as exc:
        raise RuntimeError(
            "動画音声の抽出にPyAVが必要です。requirements-local.txtを導入してください。"
        ) from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    container = av.open(os.fspath(video_path))
    try:
        stream = next((item for item in container.streams if item.type == "audio"), None)
        if stream is None:
            raise RuntimeError("動画に音声トラックがありません。")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        bytes_written = 0
        with wave.open(os.fspath(output_path), "wb") as destination:
            destination.setnchannels(1)
            destination.setsampwidth(2)
            destination.setframerate(16000)
            for frame in container.decode(stream):
                for converted in resampler.resample(frame):
                    data = converted.to_ndarray().tobytes()
                    destination.writeframesraw(data)
                    bytes_written += len(data)
            for converted in resampler.resample(None):
                data = converted.to_ndarray().tobytes()
                destination.writeframesraw(data)
                bytes_written += len(data)
    finally:
        container.close()

    if bytes_written == 0:
        raise RuntimeError("動画の音声を読み取れませんでした。")
    return output_path


def _limit_candidates(scenes: Sequence[Any], maximum: int) -> list[Any]:
    if len(scenes) <= maximum:
        return list(scenes)
    first = scenes[0]
    ranked = sorted(scenes[1:], key=lambda scene: scene.change_score, reverse=True)
    selected = [first, *ranked[:maximum - 1]]
    return sorted(selected, key=lambda scene: scene.timestamp_sec)


def _frames_from_scenes(scenes: Sequence[Any], output_dir: Path) -> list[dict[str, Any]]:
    frames = []
    for scene in scenes:
        path = output_dir / scene.image
        frames.append({
            "path": os.fspath(path),
            "filename": path.name,
            "timestamp_sec": scene.timestamp_sec,
            "timestamp_str": scene.timestamp,
        })
    return frames


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", value).strip(" .")
    return cleaned[:80] or "AI動画解析レポート"


def _write_analysis_json(path: Path, source: Path, slides: Sequence[dict[str, Any]]) -> None:
    serializable = []
    for index, slide in enumerate(slides, 1):
        serializable.append({
            "index": index,
            "timestamp_sec": slide.get("timestamp_sec"),
            "timestamp": slide.get("timestamp_str"),
            "image": slide.get("saved_filename") or slide.get("filename"),
            "analysis": slide.get("analysis", {}),
        })
    payload = {
        "created_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": source.name,
        "raw_video_uploaded": False,
        "audio_uploaded": True,
        "candidate_images_uploaded": True,
        "slides": serializable,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def analyze_with_gemini(
    video_path: str | os.PathLike[str],
    *,
    audio_path: str | os.PathLike[str] | None = None,
    output_dir: str | os.PathLike[str] | None = None,
    require_consent: bool = True,
    max_candidates: int = 30,
    max_key_slides: int = 20,
) -> dict[str, Any]:
    from audio_transcriber import (
        GEMINI_MODEL,
        create_pdf,
        generate_title_from_text,
        transcribe_with_gemini,
    )
    from key_slide_extractor import KeySlideExtractor
    from local_video_analyzer import extract_scenes

    source = Path(video_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"動画が見つかりません: {source}")
    if require_consent and not _ask_cloud_consent():
        raise RuntimeError("高精度AI解析をキャンセルしました。")

    if output_dir is None:
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        destination = source.parent / f"{source.stem}_解析結果_{stamp}"
    else:
        destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    state_path = destination / STATE_FILENAME
    resumed = state_path.is_file()
    if resumed:
        try:
            state = _read_json(state_path)
        except (OSError, ValueError, TypeError) as exc:
            raise RuntimeError("再開データを読み取れませんでした。") from exc
        if state.get("version") != STATE_VERSION:
            raise RuntimeError("再開データの形式が異なるため再開できません。")
        if state.get("status") == "complete":
            raise RuntimeError("この解析はすでに完了しています。")
        _validate_source_identity(source, state["source"])
        print("\n未完了の解析を検出しました。完了済みの処理を再利用して続行します。")
    else:
        now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        state = {
            "version": STATE_VERSION,
            "status": "in_progress",
            "created_at": now,
            "updated_at": now,
            "source": _source_identity(source),
            "audio_path": (
                os.fspath(Path(audio_path).expanduser().resolve()) if audio_path else None
            ),
            "options": {
                "max_candidates": max_candidates,
                "max_key_slides": max_key_slides,
            },
            "current_stage": "準備中",
            "completed_stages": [],
            "artifacts": {},
        }
        _write_json_atomic(state_path, state)
        _register_pending_job(state_path, state)

    try:
        api_key = _get_api_key()

        candidates_path = _artifact_for(state, "シーン候補抽出")
        if candidates_path is not None and "シーン候補抽出" in state["completed_stages"]:
            print("\n[1/5] 保存済みのシーン候補を再利用します。")
            candidate_data = _read_json(candidates_path)
            video_metadata = candidate_data["video"]
            frames = []
            for scene in candidate_data["scenes"]:
                image_path = destination / scene["image"]
                if not image_path.is_file():
                    raise RuntimeError(f"再開用のシーン画像が見つかりません: {image_path.name}")
                frames.append({
                    "path": os.fspath(image_path),
                    "filename": image_path.name,
                    "timestamp_sec": scene["timestamp_sec"],
                    "timestamp_str": scene["timestamp"],
                })
        else:
            _set_stage(state_path, state, "シーン候補抽出")
            print("\n[1/5] PC内でシーン候補を抽出中...")
            scenes, video_metadata = extract_scenes(
                source,
                destination / "抽出シーン",
                scan_fps=4.0,
                scene_threshold=0.16,
                duplicate_threshold=0.08,
                min_scene_duration=0.6,
            )
            scenes = _limit_candidates(scenes, max_candidates)
            frames = _frames_from_scenes(scenes, destination)
            candidates_path = destination / "シーン候補一覧.json"
            _write_json_atomic(candidates_path, {
                "video": video_metadata,
                "scenes": [scene.to_dict() for scene in scenes],
            })
            _set_stage(
                state_path,
                state,
                "シーン候補抽出",
                completed=True,
                artifact=candidates_path,
            )
        print(f"  Geminiへ送る候補画像: {len(frames)}枚")

        prepared_audio = _artifact_for(state, "音声準備")
        if prepared_audio is not None and "音声準備" in state["completed_stages"]:
            print("\n[2/5] 保存済みの音声を再利用します。")
        else:
            _set_stage(state_path, state, "音声準備")
            print("\n[2/5] PC内で音声を準備中...")
            original_audio = audio_path or state.get("audio_path")
            if original_audio is not None:
                prepared_audio = Path(original_audio).expanduser().resolve()
                if not prepared_audio.is_file():
                    raise FileNotFoundError(f"録音音声が見つかりません: {prepared_audio}")
            else:
                prepared_audio = extract_audio_locally(
                    source, destination / "解析用音声.wav"
                )
            _set_stage(
                state_path,
                state,
                "音声準備",
                completed=True,
                artifact=prepared_audio,
            )

        transcript_path = _artifact_for(state, "文字起こし")
        if transcript_path is not None and "文字起こし" in state["completed_stages"]:
            print("\n[3/5] 保存済みの文字起こしを再利用します。")
            transcript = transcript_path.read_text(encoding="utf-8").strip()
        else:
            _set_stage(state_path, state, "文字起こし")
            print("\n[3/5] Geminiで高精度文字起こし中...")
            transcript, _ = transcribe_with_gemini(
                os.fspath(prepared_audio), api_key, language="ja"
            )
            transcript = (transcript or "").strip()
            if not transcript:
                raise RuntimeError("Geminiの文字起こし結果が空でした。")
            transcript_path = destination / "文字起こし.txt"
            transcript_path.write_text(transcript + "\n", encoding="utf-8")
            _set_stage(
                state_path,
                state,
                "文字起こし",
                completed=True,
                artifact=transcript_path,
            )

        print("\n[4/5] Geminiで候補画像の重要度と内容を解析中...")
        extractor = KeySlideExtractor(
            api_key=api_key,
            model=GEMINI_MODEL,
            max_key_slides=max_key_slides,
            importance_threshold=45,
            output_dir=os.fspath(destination),
        )
        frame_analysis_path = _artifact_for(
            state, "画像解析", must_exist=False
        ) or (destination / "画像解析の途中結果.json")
        if frame_analysis_path.is_file():
            saved_frames = _read_json(frame_analysis_path)
            saved_by_name = {item["filename"]: item for item in saved_frames}
            for frame in frames:
                saved = saved_by_name.get(frame["filename"])
                if saved and saved.get("analysis"):
                    frame["analysis"] = saved["analysis"]

        def save_frame_progress(_analyzed: Sequence[dict[str, Any]]) -> None:
            _write_json_atomic(frame_analysis_path, frames)
            _set_stage(
                state_path,
                state,
                "画像解析",
                artifact=frame_analysis_path,
            )

        if "画像解析" in state["completed_stages"] and all(
            frame.get("analysis") for frame in frames
        ):
            print("  保存済みの画像解析結果を再利用します。")
        else:
            _set_stage(state_path, state, "画像解析", artifact=frame_analysis_path)
            frames = extractor.analyze_all_frames(
                frames,
                transcript_text=transcript,
                progress_callback=save_frame_progress,
                skip_analyzed=True,
            )
            _write_json_atomic(frame_analysis_path, frames)
            _set_stage(
                state_path,
                state,
                "画像解析",
                completed=True,
                artifact=frame_analysis_path,
            )

        frames = extractor.deduplicate_frames(frames)
        key_slides = extractor.select_key_slides(frames)
        key_slides = extractor.save_key_slides(key_slides, os.fspath(destination))

        _set_stage(state_path, state, "PDF生成")
        print("\n[5/5] 画像解析結果と文字起こしをPDFへ統合中...")
        if state.get("title"):
            title = state["title"]
        else:
            title = _safe_filename(
                generate_title_from_text(transcript, api_key) or "AI動画解析レポート"
            )
            state["title"] = title
            _set_stage(state_path, state, "PDF生成")
        pdf_path = destination / f"{title}_解析レポート.pdf"
        markdown_path = destination / f"{title}_解析レポート.md"
        extractor.generate_rich_minutes(
            key_slides,
            transcript,
            os.fspath(markdown_path),
            video_filename=source.name,
        )
        create_pdf(
            full_text=transcript,
            timestamped_text="",
            output_filepath=os.fspath(pdf_path),
            audio_filename=source.name,
            key_slides=key_slides,
            document_title=title,
        )
        json_path = destination / "画像解析結果.json"
        _write_analysis_json(json_path, source, key_slides)
        _set_stage(
            state_path,
            state,
            "PDF生成",
            completed=True,
            artifact=pdf_path,
        )

        result = {
            "success": True,
            "resumed": resumed,
            "output_dir": os.fspath(destination),
            "pdf": os.fspath(pdf_path),
            "markdown": os.fspath(markdown_path),
            "analysis_json": os.fspath(json_path),
            "transcript_chars": len(transcript),
            "candidate_count": len(frames),
            "key_slide_count": len(key_slides),
            "video": video_metadata,
        }
        state["status"] = "complete"
        state["current_stage"] = "完了"
        state["result"] = result
        state["updated_at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        state.pop("last_error", None)
        _write_json_atomic(state_path, state)
        _remove_pending_job(destination)
        print("\n" + "=" * 64)
        print("  高精度AI解析が完了しました")
        print(f"  PDF: {pdf_path}")
        print(f"  採用画像: {len(key_slides)}枚")
        print("=" * 64)
        return result
    except (Exception, KeyboardInterrupt) as exc:
        state["status"] = "interrupted"
        state["last_error"] = str(exc) or exc.__class__.__name__
        state["updated_at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        _write_json_atomic(state_path, state)
        _register_pending_job(state_path, state)
        print("\n解析の進行状況を保存しました。次回は途中から再開できます。")
        print(f"再開データ: {state_path}")
        raise


def resume_analysis(
    state_path: str | os.PathLike[str],
    *,
    require_consent: bool = True,
) -> dict[str, Any]:
    path = Path(state_path).expanduser().resolve()
    if path.is_dir():
        path = path / STATE_FILENAME
    if not path.is_file():
        raise FileNotFoundError(f"再開データが見つかりません: {path}")
    try:
        state = _read_json(path)
        source = state["source"]["path"]
        options = state.get("options", {})
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise RuntimeError("再開データを読み取れませんでした。") from exc
    return analyze_with_gemini(
        source,
        audio_path=state.get("audio_path"),
        output_dir=path.parent,
        require_consent=require_consent,
        max_candidates=int(options.get("max_candidates", 30)),
        max_key_slides=int(options.get("max_key_slides", 20)),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="音声と候補画像だけをGeminiへ送り、解析結果PDFを生成します。"
    )
    parser.add_argument("video", nargs="?", help="解析する動画")
    parser.add_argument("--audio", help="別録音したPC音声")
    parser.add_argument("--output-dir", help="出力先")
    parser.add_argument("--resume", help="未完了解析のフォルダまたは再開データ")
    parser.add_argument("--yes", action="store_true", help="クラウド送信確認を省略")
    parser.add_argument("--max-candidates", type=int, default=30)
    parser.add_argument("--max-key-slides", type=int, default=20)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.max_candidates < 1 or args.max_key_slides < 1:
        print("エラー: 画像枚数は1以上で指定してください。", file=sys.stderr)
        return 2
    if not args.resume and not args.video:
        print("エラー: 動画または --resume を指定してください。", file=sys.stderr)
        return 2
    try:
        if args.resume:
            resume_analysis(args.resume, require_consent=not args.yes)
        else:
            analyze_with_gemini(
                args.video,
                audio_path=args.audio,
                output_dir=args.output_dir,
                require_consent=not args.yes,
                max_candidates=args.max_candidates,
                max_key_slides=args.max_key_slides,
            )
    except (FileNotFoundError, RuntimeError, OSError, ValueError, KeyboardInterrupt) as exc:
        print(f"\nエラー: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
