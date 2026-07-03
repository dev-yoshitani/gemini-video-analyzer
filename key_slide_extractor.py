r"""
==========================================================
  Key Slide Extractor
==========================================================

動画ファイルからキースライド（重要なフレーム）を抽出し、
文字起こし結果と統合したリッチ議事録を生成するモジュール。

使い方:
  python audio_transcriber.py --video meeting.mp4 --extract-key-slides

==========================================================
"""

import os
import sys
import json
import time
import re
import shutil
import subprocess
import datetime
from pathlib import Path


# ============================================================
# ffmpeg チェック
# ============================================================

def check_ffmpeg():
    """ffmpegがインストールされているか確認する。

    Returns:
        bool: ffmpegが利用可能な場合True
    """
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def print_ffmpeg_install_guide():
    """ffmpegのインストール方法を表示する。"""
    print()
    print("=" * 60)
    print("  エラー: ffmpegが見つかりません")
    print("=" * 60)
    print()
    print("  動画からのキースライド抽出には ffmpeg が必要です。")
    print()
    print("  【インストール方法】")
    print()
    print("  方法1: winget (Windows 10/11 推奨)")
    print("    winget install ffmpeg")
    print()
    print("  方法2: 公式サイトからダウンロード")
    print("    https://ffmpeg.org/download.html")
    print("    ダウンロード後、PATHに追加してください。")
    print()
    print("  方法3: Chocolatey")
    print("    choco install ffmpeg")
    print()
    print("  インストール後、PowerShellを再起動してください。")
    print("=" * 60)


# ============================================================
# KeySlideExtractor クラス
# ============================================================

class KeySlideExtractor:
    """動画からキースライドを抽出するメインクラス。

    Attributes:
        api_key (str): Gemini APIキー
        model (str): 使用するGeminiモデル名
        frame_interval (int): フレーム抽出間隔（秒）
        max_key_slides (int): 最大キースライド数
        analyze_max_frames (int): 解析する最大フレーム数
        importance_threshold (int): 重要度の閾値（0-100）
        dry_run (bool): True時はAPI呼び出しをスキップ
        output_dir (str): 出力先ディレクトリ
    """

    def __init__(self, api_key, model="gemini-3.5-flash",
                 frame_interval=60, max_key_slides=15,
                 analyze_max_frames=50, importance_threshold=50,
                 dry_run=False, output_dir=None, skip_frame_analysis=False):
        self.api_key = api_key
        self.model = model
        self.frame_interval = frame_interval
        self.max_key_slides = max_key_slides
        self.analyze_max_frames = analyze_max_frames
        self.importance_threshold = importance_threshold
        self.dry_run = dry_run
        self.skip_frame_analysis = skip_frame_analysis
        import sys
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            
        self.output_dir = output_dir or os.path.join(base_dir, "output")

        # モデルフォールバックチェーン
        self._models_to_try = [self.model]
        if self.model != "gemini-2.5-flash":
            self._models_to_try.append("gemini-2.5-flash")
        if "gemini-2.0-flash" not in self._models_to_try:
            self._models_to_try.append("gemini-2.0-flash")

    # ============================================================
    # 動画→音声抽出
    # ============================================================

    def extract_audio_from_video(self, video_path, output_audio_path):
        """ffmpegで動画から音声をWAVとして抽出する。

        Args:
            video_path (str): 入力動画ファイルのパス
            output_audio_path (str): 出力WAVファイルのパス

        Returns:
            bool: 成功した場合True
        """
        print(f"\n動画から音声を抽出中...")
        print(f"  入力: {os.path.basename(video_path)}")
        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-i", video_path,
                    "-vn",              # 映像を無視
                    "-acodec", "pcm_s16le",  # 16bit PCM
                    "-ar", "16000",     # 16kHz
                    "-ac", "1",         # モノラル
                    "-y",               # 上書き
                    output_audio_path,
                ],
                capture_output=True, text=True, timeout=600,
            )
            if result.returncode != 0:
                print(f"  音声抽出エラー: {result.stderr[:500]}")
                return False

            size_mb = os.path.getsize(output_audio_path) / (1024 * 1024)
            print(f"  音声抽出完了: {os.path.basename(output_audio_path)} ({size_mb:.1f} MB)")
            return True

        except subprocess.TimeoutExpired:
            print("  エラー: 音声抽出がタイムアウトしました（10分超過）")
            return False
        except Exception as e:
            print(f"  エラー: 音声抽出に失敗しました: {e}")
            return False

    # ============================================================
    # フレーム抽出
    # ============================================================

    def extract_frames(self, video_path, frames_dir):
        """ffmpegで動画から一定間隔でフレーム画像を抽出する。

        Args:
            video_path (str): 入力動画ファイルのパス
            frames_dir (str): フレーム画像の保存先ディレクトリ

        Returns:
            list[dict]: 抽出されたフレーム情報のリスト
                [{"path": "...", "timestamp_sec": 10.0, "filename": "frame_001.jpg"}, ...]
        """
        os.makedirs(frames_dir, exist_ok=True)

        print(f"\n動画からフレームを抽出中... (間隔: {self.frame_interval}秒)")
        try:
            # 録画開始直後のウィンドウ切り替え画面を避けるため、最初の10秒をスキップ
            start_offset = 10
            
            # フレーム抽出実行
            result = subprocess.run(
                [
                    "ffmpeg", "-ss", str(start_offset), "-i", video_path,
                    "-vf", f"fps=1/{self.frame_interval}",
                    "-q:v", "2",        # JPEG品質 (2=高品質)
                    "-y",
                    os.path.join(frames_dir, "frame_%04d.jpg"),
                ],
                capture_output=True, text=True, timeout=600,
            )
            if result.returncode != 0:
                print(f"  フレーム抽出エラー: {result.stderr[:500]}")
                return []

        except subprocess.TimeoutExpired:
            print("  エラー: フレーム抽出がタイムアウトしました（10分超過）")
            return []
        except Exception as e:
            print(f"  エラー: フレーム抽出に失敗しました: {e}")
            return []

        # 抽出されたフレームファイルを収集
        frames = []
        frame_files = sorted(
            [f for f in os.listdir(frames_dir) if f.startswith("frame_") and f.endswith(".jpg")]
        )
        for i, filename in enumerate(frame_files):
            timestamp_sec = start_offset + i * self.frame_interval
            frames.append({
                "path": os.path.join(frames_dir, filename),
                "filename": filename,
                "timestamp_sec": timestamp_sec,
                "timestamp_str": self._format_timestamp(timestamp_sec),
            })

        # analyze_max_frames 制限
        if len(frames) > self.analyze_max_frames:
            print(f"  抽出フレーム数 ({len(frames)}) が上限 ({self.analyze_max_frames}) を超えたため、間引きます")
            step = len(frames) / self.analyze_max_frames
            frames = [frames[int(i * step)] for i in range(self.analyze_max_frames)]

        print(f"  フレーム抽出完了: {len(frames)} フレーム")
        return frames

    # ============================================================
    # Gemini API フレーム解析
    # ============================================================

    def analyze_frame_with_gemini(self, frame_path, client, transcript_text=None):
        """1枚のフレーム画像をGemini APIで解析する。

        Args:
            frame_path (str): フレーム画像のパス
            client: google.genai.Client インスタンス
            transcript_text (str, optional): 音声文字起こし全文（文脈として使用）

        Returns:
            dict: 解析結果。失敗時はデフォルト値を返す。
        """
        default_result = {
            "is_key_slide": False,
            "importance_score": 0,
            "frame_type": "other",
            "summary": "",
            "detected_text": "",
            "reason": "analysis failed or skipped",
        }

        # 文脈テキスト（文字起こし全文）をプロンプトに埋め込む
        context_section = ""
        if transcript_text and transcript_text.strip():
            # 長すぎる場合は先頭・末尾を取り出して要約的に使う
            max_context_chars = 8000
            if len(transcript_text) > max_context_chars:
                head = transcript_text[:4000]
                tail = transcript_text[-4000:]
                context_text = f"{head}\n...（中略）...\n{tail}"
            else:
                context_text = transcript_text
            context_section = f"""\n\n【この動画の文字起こし全文（発表者の発言内容）】\n{context_text}\n\n上記の文脈・発言内容を十分に考慮した上で、以下の画像を解析してください。"""

        prompt = f"""あなたはプロフェッショナルな議事録作成アシスタントです。{context_section}

この画像は会議・プレゼンテーションの動画から抽出した1フレームです。
以下のJSON形式のみで回答してください（マークダウンや余計な説明は一切不要）。

{{
  "is_key_slide": true か false,
  "importance_score": 0〜100の整数,
  "frame_type": "slide"（スライド）か "chart"（グラフ）か "document"（文書）か "whiteboard"（ホワイトボード）か "screen_share"（画面共有）か "speaker_view"（発表者映像）か "other"（その他）,
  "summary": "このスライド・画像が表している内容を、上記の発表者の発言文脈と結びつけて【流暢な日本語で3〜5文程度、極めて詳細かつ具体的に】解説してください。グラフや図解が何を意味するか、発表者の意図や重要な結論も含めてください。",
  "detected_text": "画像内に見えるタイトル・箇条書き・グラフの軸・重要な数値など、すべての主要テキストを【日本語の意味が通じるよう、単なる単語の羅列ではなく構造的に】抽出してください。例：【タイトル】〇〇 【要点】・〇〇 ・〇〇",
  "reason": "このフレームをキースライドと判定した、または判定しなかった理由を【日本語で具体的に】説明してください。"
}}

【キースライドの判定基準】
- キースライドとは：プレゼンスライド、グラフ、図解、重要な文書、意味のある画面共有など、視覚的に重要な情報を含むフレーム。
- キースライドでないもの：発表者だけが映っている映像、画面の切り替わり、真っ黒な画面、ロード中の画面など。
- importance_score：80〜100＝文字・グラフが明確で情報量が多い重要スライド、50〜79＝部分的に有用、0〜49＝重要度が低い。
- すべての文字列フィールド（summary, detected_text, reason）は必ず日本語で出力してください。"""

        # 画像データを読み込む
        try:
            with open(frame_path, "rb") as f:
                image_data = f.read()
        except Exception as e:
            print(f"    画像読み込みエラー: {e}")
            return default_result

        from google.genai import types as genai_types

        # thinking_budget=0 でthinkingモデル対策
        gen_config = genai_types.GenerateContentConfig(
            thinking_config=genai_types.ThinkingConfig(thinking_budget=0)
        )

        # リトライループ（最大3回）
        max_retries = 3
        for attempt in range(max_retries):
            for model in self._models_to_try:
                try:
                    # 画像をPartとして送信
                    image_part = genai_types.Part.from_bytes(
                        data=image_data,
                        mime_type="image/jpeg",
                    )

                    try:
                        response = client.models.generate_content(
                            model=model,
                            contents=[prompt, image_part],
                            config=gen_config,
                        )
                    except Exception:
                        # thinking_config非対応モデルはconfigなしで再試行
                        response = client.models.generate_content(
                            model=model,
                            contents=[prompt, image_part],
                        )

                    # レスポンステキストの取得（thinkingモデル対策）
                    text = response.text
                    if not text and response.candidates:
                        parts_text = []
                        for part in response.candidates[0].content.parts:
                            if hasattr(part, 'text') and part.text and not getattr(part, 'thought', False):
                                parts_text.append(part.text)
                        if parts_text:
                            text = "".join(parts_text)

                    if not text:
                        continue

                    # JSONの抽出とパース
                    parsed = self._extract_json(text)
                    if parsed:
                        # 必須フィールドの検証と補完
                        result = {**default_result, **parsed}
                        result["is_key_slide"] = bool(result.get("is_key_slide", False))
                        result["importance_score"] = int(result.get("importance_score", 0))
                        return result

                except Exception as e:
                    err_str = str(e).lower()
                    if "503" in err_str or "unavailable" in err_str or "429" in err_str or "too many requests" in err_str or "resourceexhausted" in err_str:
                        if model != self._models_to_try[-1] or "429" in err_str:
                            # 429等のレート制限時は少し長めに待機
                            time.sleep(5)
                            continue
                    # その他のエラーはリトライへ
                    break

            # リトライ前に少し待つ
            if attempt < max_retries - 1:
                time.sleep(5)

        # 全リトライ失敗
        return default_result

    def analyze_all_frames(self, frames, transcript_text=None):
        """全フレームをGemini APIで解析する。

        Args:
            frames (list[dict]): フレーム情報リスト
            transcript_text (str, optional): 音声文字起こし全文（文脈として各フレーム解析に使用）

        Returns:
            list[dict]: 解析結果が追加されたフレーム情報リスト
        """
        if self.dry_run:
            print("\n[dry-run] Gemini API呼び出しをスキップします")
            for frame in frames:
                frame["analysis"] = {
                    "is_key_slide": True,
                    "importance_score": 50,
                    "frame_type": "other",
                    "summary": "[dry-run] 解析スキップ",
                    "detected_text": "",
                    "reason": "dry-run mode",
                }
            return frames

        from google import genai
        client = genai.Client(api_key=self.api_key)

        if transcript_text:
            print(f"\nGemini APIでフレームを解析中... ({len(frames)} フレーム) ※文字起こし全文を文脈として使用")
        else:
            print(f"\nGemini APIでフレームを解析中... ({len(frames)} フレーム)")
        analyzed = []
        skipped = 0

        for i, frame in enumerate(frames):
            progress = f"[{i+1}/{len(frames)}]"
            print(f"  {progress} {frame['filename']} (t={frame['timestamp_str']})...", end=" ")

            result = self.analyze_frame_with_gemini(frame["path"], client, transcript_text=transcript_text)
            frame["analysis"] = result

            if result["importance_score"] == 0 and result["reason"] == "analysis failed or skipped":
                print(" [スキップ]")
                skipped += 1
            elif result["is_key_slide"]:
                print(f" [OK] score={result['importance_score']} type={result['frame_type']}")
            else:
                print(f" [NG] score={result['importance_score']}")

            analyzed.append(frame)

            # APIレート制限対策（無料枠15RPM考慮の待機）
            if i < len(frames) - 1:
                time.sleep(2)

        if skipped > 0:
            print(f"\n  ※ {skipped} フレームの解析がスキップされました")

        return analyzed

    # ============================================================
    # 重複除外
    # ============================================================

    def deduplicate_frames(self, frames):
        """類似・重複フレームを除外する。

        summary と detected_text の類似度で判定し、
        重複ペアのうち importance_score が低い方を除外する。

        Args:
            frames (list[dict]): 解析済みフレーム情報リスト

        Returns:
            list[dict]: 重複除外後のフレームリスト
        """
        if len(frames) <= 1:
            return frames

        # キースライドのみを対象に重複チェック
        key_frames = [f for f in frames if f.get("analysis", {}).get("is_key_slide", False)]
        non_key_frames = [f for f in frames if not f.get("analysis", {}).get("is_key_slide", False)]

        if len(key_frames) <= 1:
            return frames

        # 重複チェック
        to_remove = set()
        for i in range(len(key_frames)):
            if i in to_remove:
                continue
            for j in range(i + 1, len(key_frames)):
                if j in to_remove:
                    continue

                similarity = self._text_similarity(
                    key_frames[i]["analysis"].get("summary", "") + " " + key_frames[i]["analysis"].get("detected_text", ""),
                    key_frames[j]["analysis"].get("summary", "") + " " + key_frames[j]["analysis"].get("detected_text", ""),
                )

                if similarity >= 0.70:
                    # 重複ペアのうちスコアが低い方を除外
                    if key_frames[i]["analysis"]["importance_score"] >= key_frames[j]["analysis"]["importance_score"]:
                        to_remove.add(j)
                    else:
                        to_remove.add(i)

        removed_count = len(to_remove)
        deduplicated_keys = [f for idx, f in enumerate(key_frames) if idx not in to_remove]

        if removed_count > 0:
            print(f"\n  重複フレーム除外: {removed_count} フレームを除外しました")

        return deduplicated_keys + non_key_frames

    # ============================================================
    # キースライド選定
    # ============================================================

    def select_key_slides(self, frames):
        """重要度スコアで上位N件のキースライドを選定する。

        Args:
            frames (list[dict]): 解析済みフレーム情報リスト

        Returns:
            list[dict]: 選定されたキースライドのリスト（タイムスタンプ順）
        """
        # is_key_slide=True かつ importance_score >= threshold のフレームを抽出
        candidates = [
            f for f in frames
            if f.get("analysis", {}).get("is_key_slide", False)
            and f.get("analysis", {}).get("importance_score", 0) >= self.importance_threshold
        ]

        # importance_score で降順ソート
        candidates.sort(key=lambda f: f["analysis"]["importance_score"], reverse=True)

        # 上位N件に制限
        selected = candidates[:self.max_key_slides]

        # タイムスタンプ順に並べ直す
        selected.sort(key=lambda f: f["timestamp_sec"])

        print(f"\n  キースライド選定: {len(selected)} / {len(candidates)} 件 (閾値: {self.importance_threshold})")
        return selected

    # ============================================================
    # キースライド保存
    # ============================================================

    def save_key_slides(self, key_slides, output_subdir):
        """選定されたキースライド画像をoutputフォルダにコピー保存する。

        Args:
            key_slides (list[dict]): 選定されたキースライド情報
            output_subdir (str): 出力サブディレクトリのパス

        Returns:
            list[dict]: 保存先パスが追加されたキースライド情報
        """
        slides_dir = os.path.join(output_subdir, "key_slides")
        os.makedirs(slides_dir, exist_ok=True)

        print(f"\nキースライド画像を保存中...")
        for i, slide in enumerate(key_slides):
            src = slide["path"]
            # ファイル名を連番＋タイムスタンプにする
            dst_filename = f"slide_{i+1:02d}_{slide['timestamp_str'].replace(':', '-')}.jpg"
            dst = os.path.join(slides_dir, dst_filename)

            try:
                shutil.copy2(src, dst)
                slide["saved_path"] = dst
                slide["saved_filename"] = dst_filename
                print(f"  [{i+1}] {dst_filename} (t={slide['timestamp_str']}, score={slide['analysis']['importance_score']})")
            except Exception as e:
                print(f"  [{i+1}] 保存エラー: {e}")
                slide["saved_path"] = None
                slide["saved_filename"] = None

        return key_slides

    # ============================================================
    # key_slides.json 生成
    # ============================================================

    def generate_key_slides_json(self, key_slides, output_path):
        """key_slides.json を生成する。

        Args:
            key_slides (list[dict]): キースライド情報
            output_path (str): 出力JSONファイルのパス
        """
        json_data = {
            "generated_at": datetime.datetime.now().isoformat(),
            "model": self.model,
            "settings": {
                "frame_interval": self.frame_interval,
                "max_key_slides": self.max_key_slides,
                "importance_threshold": self.importance_threshold,
            },
            "total_key_slides": len(key_slides),
            "key_slides": [],
        }

        for i, slide in enumerate(key_slides):
            entry = {
                "index": i + 1,
                "timestamp_sec": slide["timestamp_sec"],
                "timestamp_str": slide["timestamp_str"],
                "image_file": slide.get("saved_filename", slide["filename"]),
                "analysis": slide.get("analysis", {}),
            }
            json_data["key_slides"].append(entry)

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)

        print(f"\nkey_slides.json 保存完了: {output_path}")

    # ============================================================
    # rich_minutes.md 生成
    # ============================================================

    def generate_rich_minutes(self, key_slides, transcript_text, output_path,
                              video_filename=""):
        """rich_minutes.md を生成する。

        MVP仕様: キースライドごとにタイムスタンプと要約を表示し、
        文字起こし全文は末尾にまとめて配置する。

        Args:
            key_slides (list[dict]): キースライド情報
            transcript_text (str): 文字起こし全文テキスト
            output_path (str): 出力Markdownファイルのパス
            video_filename (str): 元動画のファイル名
        """
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = []

        # ヘッダー
        lines.append("# 📋 リッチ議事録 / Rich Minutes")
        lines.append("")
        lines.append(f"- **生成日時 / Generated**: {now}")
        if video_filename:
            lines.append(f"- **動画ファイル / Video**: {video_filename}")
        lines.append(f"- **モデル / Model**: {self.model}")
        lines.append(f"- **キースライド数 / Key Slides**: {len(key_slides)}")
        lines.append("")
        lines.append("---")
        lines.append("")

        # キースライドセクション
        lines.append("## 🎯 キースライド / Key Slides")
        lines.append("")

        if key_slides:
            for i, slide in enumerate(key_slides):
                analysis = slide.get("analysis", {})
                lines.append(f"### Slide {i+1} — {slide['timestamp_str']}")
                lines.append("")

                # 画像の埋め込み（ユーザー要望により廃止）
                # saved_filename = slide.get("saved_filename")
                # if saved_filename:
                #     lines.append(f"![Slide {i+1}](key_slides/{saved_filename})")
                #     lines.append("")

                # 解析結果
                lines.append(f"- **種類 / Type**: {analysis.get('frame_type', 'unknown')}")
                lines.append(f"- **重要度 / Score**: {analysis.get('importance_score', 0)}/100")
                lines.append(f"- **要約 / Summary**: {analysis.get('summary', '')}")

                detected_text = analysis.get("detected_text", "")
                if detected_text:
                    lines.append(f"- **検出テキスト / Detected Text**: {detected_text}")

                lines.append("")
                lines.append("---")
                lines.append("")
        else:
            lines.append("*キースライドは検出されませんでした / No key slides detected.*")
            lines.append("")
            lines.append("---")
            lines.append("")

        # 文字起こし全文セクション
        lines.append("## 📝 文字起こし全文 / Full Transcript")
        lines.append("")

        if transcript_text and transcript_text.strip():
            lines.append(transcript_text.strip())
        else:
            lines.append("*文字起こし結果はありません / No transcript available.*")

        lines.append("")

        # ファイル書き込み
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

        print(f"rich_minutes.md 保存完了: {output_path}")

    # ============================================================
    # フルパイプライン実行
    # ============================================================

    def run(self, video_path, provided_audio_path=None):
        """動画キースライド抽出のフルパイプラインを実行する。

        Args:
            video_path (str): 動画ファイルのパス

        Returns:
            dict: 結果サマリー
                {
                    "success": bool,
                    "audio_path": str,
                    "transcript_text": str,
                    "key_slides_count": int,
                    "key_slides_json_path": str,
                    "rich_minutes_path": str,
                    "output_subdir": str,
                }
        """
        video_path = os.path.abspath(video_path)
        video_filename = os.path.basename(video_path)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

        print()
        print("=" * 60)
        print("  [動画キースライド抽出モード]")
        print("=" * 60)
        print(f"  動画: {video_filename}")
        print(f"  モデル: {self.model}")
        if self.skip_frame_analysis:
            print("  フレーム解析: [オフ] (音声の文字起こしのみ)")
        else:
            print(f"  フレーム間隔: {self.frame_interval}秒")
            print(f"  最大キースライド数: {self.max_key_slides}")
            print(f"  解析最大フレーム数: {self.analyze_max_frames}")
        if self.dry_run:
            print(f"  [注意] dry-runモード: APIは呼び出しません")
        print()

        result = {
            "success": False,
            "audio_path": None,
            "transcript_text": None,
            "key_slides_count": 0,
            "key_slides_json_path": None,
            "rich_minutes_path": None,
            "output_subdir": None,
        }

        # ---- Step 0: ffmpeg チェック ----
        if not check_ffmpeg():
            print_ffmpeg_install_guide()
            return result

        # ---- 出力ディレクトリ準備 ----
        output_subdir = os.path.join(self.output_dir, f"video_{timestamp}")
        os.makedirs(output_subdir, exist_ok=True)
        result["output_subdir"] = output_subdir

        frames_dir = os.path.join(output_subdir, "frames_tmp")

        # ---- Step 1: 音声抽出 ----
        if provided_audio_path and os.path.exists(provided_audio_path):
            audio_path = provided_audio_path
            print("\n  ※ 事前録音された音声ファイルを使用します (動画からの抽出スキップ)")
        else:
            audio_path = os.path.join(output_subdir, f"audio_{timestamp}.wav")
            if not self.extract_audio_from_video(video_path, audio_path):
                print("\nエラー: 動画からの音声抽出に失敗しました。")
                return result
        result["audio_path"] = audio_path

        # ---- Step 2: フレーム抽出 ----
        frames = []
        if not self.skip_frame_analysis:
            frames = self.extract_frames(video_path, frames_dir)
            if not frames:
                print("\n警告: フレームを抽出できませんでした。音声の文字起こしのみ行います。")

        # ---- Step 3: 音声の文字起こし ----
        transcript_text = None
        if not self.dry_run:
            print("\n" + "-" * 40)
            print("  音声の文字起こしを実行中...")
            print("-" * 40)
            # audio_transcriber の関数をインポートして使用
            from audio_transcriber import transcribe_with_gemini
            transcript_text, _ = transcribe_with_gemini(audio_path, self.api_key)
            if transcript_text:
                result["transcript_text"] = transcript_text
                print(f"\n  文字起こし完了: {len(transcript_text)} 文字")
            else:
                print("\n  警告: 文字起こし結果が空です。")
        else:
            transcript_text = "[dry-run] 文字起こしはスキップされました"
            result["transcript_text"] = transcript_text

        # ---- Step 4: フレーム解析 ----
        # 文字起こし全文を文脈として渡すことで、AIが発表者の意図を踏まえた深い解析を行う
        if frames:
            frames = self.analyze_all_frames(frames, transcript_text=transcript_text)

            # ---- Step 5: 重複除外 ----
            frames = self.deduplicate_frames(frames)

            # ---- Step 6: キースライド選定 ----
            key_slides = self.select_key_slides(frames)

            # ---- Step 7: キースライド保存 ----
            if key_slides:
                key_slides = self.save_key_slides(key_slides, output_subdir)
                result["key_slides_count"] = len(key_slides)
            else:
                print("\n  キースライドは検出されませんでした。")

            # ---- Step 8: key_slides.json 生成 (ユーザー要望により廃止) ----
            # json_path = os.path.join(output_subdir, "key_slides.json")
            # self.generate_key_slides_json(key_slides, json_path)
            # result["key_slides_json_path"] = json_path
            result["key_slides_json_path"] = None

            # ---- タイトル自動生成 ----
            title_name = "リッチ議事録"
            if transcript_text:
                from audio_transcriber import generate_title_from_text
                import re
                print("\n  AIがタイトルを自動生成中...")
                generated_title = generate_title_from_text(transcript_text, self.api_key)
                if generated_title:
                    title_name = re.sub(r'[\\/:*?"<>|]', '', generated_title).strip() or "リッチ議事録"
            
            timestamp_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            base_filename = f"{title_name}_{timestamp_str}"

            # ---- Step 9: rich_minutes.md 生成 ----
            md_path = os.path.join(output_subdir, f"{base_filename}.md")
            self.generate_rich_minutes(
                key_slides, transcript_text or "", md_path,
                video_filename=video_filename,
            )
            result["rich_minutes_path"] = md_path

            # ---- Step 10: PDF 生成 ----
            from audio_transcriber import create_pdf
            pdf_path = os.path.join(output_subdir, f"{base_filename}.pdf")
            try:
                create_pdf(
                    full_text=transcript_text or "",
                    timestamped_text="",
                    output_filepath=pdf_path,
                    audio_filename=video_filename,
                    key_slides=key_slides
                )
                result["pdf_path"] = pdf_path
            except Exception as e:
                print(f"\n  PDF生成に失敗しました: {e}")
        else:
            # フレームなしの場合でも文字起こし結果は保存
            # タイトル自動生成
            title_name = "文字起こし結果"
            if transcript_text:
                from audio_transcriber import generate_title_from_text
                import re
                print("\n  AIがタイトルを自動生成中...")
                generated_title = generate_title_from_text(transcript_text, self.api_key)
                if generated_title:
                    title_name = re.sub(r'[\\/:*?"<>|]', '', generated_title).strip() or "文字起こし結果"
            
            timestamp_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            base_filename = f"{title_name}_{timestamp_str}"

            md_path = os.path.join(output_subdir, f"{base_filename}.md")
            self.generate_rich_minutes(
                [], transcript_text or "", md_path,
                video_filename=video_filename,
            )
            result["rich_minutes_path"] = md_path
            
            # PDFも出力しておく
            from audio_transcriber import create_pdf
            pdf_path = os.path.join(output_subdir, f"{base_filename}.pdf")
            try:
                create_pdf(
                    full_text=transcript_text or "",
                    timestamped_text="",
                    output_filepath=pdf_path,
                    audio_filename=video_filename
                )
                result["pdf_path"] = pdf_path
            except Exception:
                pass

        # ---- 一時フレームディレクトリの削除 ----
        if os.path.exists(frames_dir):
            try:
                shutil.rmtree(frames_dir)
                print(f"\n一時フレームファイルを削除しました")
            except Exception:
                pass

        result["success"] = True

        # ---- 完了メッセージ ----
        print()
        print("=" * 60)
        print("  [完了] すべての処理が完了しました！")
        print("=" * 60)
        print(f"\n  出力フォルダ: {output_subdir}")
        if result["key_slides_count"] > 0:
            print(f"  キースライド数: {result['key_slides_count']}")
        if result["key_slides_json_path"]:
            print(f"  JSON: {os.path.basename(result['key_slides_json_path'])}")
        if result["rich_minutes_path"]:
            print(f"  議事録: {os.path.basename(result['rich_minutes_path'])}")
        if result["audio_path"]:
            print(f"  音声: {os.path.basename(result['audio_path'])}")
        print()

        return result

    # ============================================================
    # ユーティリティ
    # ============================================================

    @staticmethod
    def _format_timestamp(seconds):
        """秒数をHH:MM:SS形式に変換する。"""
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    @staticmethod
    def _extract_json(text):
        """テキストからJSON部分を抽出してパースする。

        ```json ... ``` ブロック、または直接のJSONオブジェクトに対応。

        Returns:
            dict or None: パース成功時はdict、失敗時はNone
        """
        if not text:
            return None

        # パターン1: ```json ... ``` ブロック
        json_block = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
        if json_block:
            try:
                return json.loads(json_block.group(1).strip())
            except json.JSONDecodeError:
                pass

        # パターン2: 直接のJSONオブジェクト { ... }
        json_match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass

        # パターン3: テキスト全体をそのまま試す
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _text_similarity(text1, text2):
        """2つのテキストの類似度を計算する。日本語にも対応するため difflib を使用。

        Returns:
            float: 0.0 〜 1.0 の類似度
        """
        if not text1 or not text2:
            return 0.0

        import difflib
        return difflib.SequenceMatcher(None, text1, text2).ratio()
