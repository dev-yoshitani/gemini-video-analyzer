r"""
==========================================================
  Gemini Voice Transcriber (PC Audio Recording & Transcription)
==========================================================

使い方:
  python audio_transcriber.py                    # PC音声を録音
  python audio_transcriber.py "音声ファイル.mp3"  # 既存ファイルを文字起こし

==========================================================
"""

import os
import sys
import wave
import struct
import threading
import datetime
import time
import argparse


# ============================================================
# 設定
# ============================================================

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Gemini APIキー（環境変数 GEMINI_API_KEY が優先されます）
API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Geminiモデル
GEMINI_MODEL = "gemini-3.5-flash"

# 録音設定（デバイスのデフォルトで録音し、保存時に変換する）
SAMPLE_RATE = 44100
CHANNELS = 2
CHUNK = 1024
FORMAT_BITS = 16
SAVE_RATE = 16000     # 保存時の目標サンプルレート（音声認識の標準品質）

import sys

# PyInstaller の --onefile モードで実行された場合、実行ファイルのディレクトリを取得する
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
    # PyInstallerのエントリーポイントが循環インポートされる際の ModuleNotFoundError 回避
    if __name__ == "__main__":
        sys.modules['audio_transcriber'] = sys.modules['__main__']
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ============================================================
# 定数と設定
# ============================================================
# 出力先ディレクトリ
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

# PDF設定
PDF_FONT_SIZE = 11
PDF_LINE_HEIGHT = 7

# 環境変数の読み込み (同じフォルダにある .env を優先)
def load_env_if_exists():
    env_path = os.path.join(BASE_DIR, ".env")
    lines = []
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            


# ============================================================
# APIキー保存ユーティリティ
# ============================================================
def save_api_key_to_env(api_key_val):
    """APIキーを.envファイルに安全に保存（既存キーを置換）"""
    env_path = os.path.join(BASE_DIR, ".env")
    lines = []
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
    with open(env_path, "w", encoding="utf-8") as f:
        key_found = False
        for line in lines:
            if line.strip().startswith("GEMINI_API_KEY="):
                f.write(f'GEMINI_API_KEY="{api_key_val}"\n')
                key_found = True
            else:
                f.write(line)
        if not key_found:
            f.write(f'GEMINI_API_KEY="{api_key_val}"\n')



# ============================================================
# 日本語フォントの自動検出
# ============================================================

def find_japanese_font():
    """Windowsにインストールされている日本語フォントを探す"""
    font_candidates = [
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "YuGothR.ttc"),
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "YuGothM.ttc"),
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "meiryo.ttc"),
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "msgothic.ttc"),
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "msmincho.ttc"),
    ]
    for font_path in font_candidates:
        if os.path.exists(font_path):
            return font_path
    return None


# ============================================================
# 録音クラス
# ============================================================

class AudioRecorder:
    """WASAPIループバックでPCのシステム音声を録音する"""

    def __init__(self):
        self.frames = []
        self.is_recording = False
        self.record_thread = None
        self.pa = None
        self.stream = None
        self.sample_width = FORMAT_BITS // 8
        self.actual_rate = SAMPLE_RATE
        self.actual_channels = CHANNELS

    def _find_loopback_device(self):
        import pyaudiowpatch as pyaudio
        self.pa = pyaudio.PyAudio()

        try:
            wasapi_info = self.pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        except OSError:
            print("エラー: WASAPIが利用できません。Windows 10/11を確認してください。")
            sys.exit(1)

        default_output_index = wasapi_info["defaultOutputDevice"]
        default_speakers = self.pa.get_device_info_by_index(default_output_index)

        if not default_speakers.get("isLoopbackDevice", False):
            for i in range(self.pa.get_device_count()):
                device = self.pa.get_device_info_by_index(i)
                if (device.get("isLoopbackDevice", False) and
                        device.get("name", "").startswith(default_speakers["name"][:30])):
                    return device
            try:
                return self.pa.get_wasapi_loopback_analogue_by_dict(default_speakers)
            except Exception:
                pass
            print("エラー: ループバックデバイスが見つかりません。")
            print(f"  デフォルトスピーカー: {default_speakers['name']}")
            sys.exit(1)

        return default_speakers

    def _record_worker(self, loopback_device):
        import pyaudiowpatch as pyaudio
        try:
            # WASAPIループバックはデバイスのネイティブ設定でしか開けない
            device_rate = int(loopback_device.get("defaultSampleRate", SAMPLE_RATE))
            device_channels = int(loopback_device.get("maxInputChannels", CHANNELS))
            self.actual_rate = device_rate
            self.actual_channels = device_channels

            self.stream = self.pa.open(
                format=pyaudio.paInt16,
                channels=device_channels,
                rate=device_rate,
                input=True,
                input_device_index=loopback_device["index"],
                frames_per_buffer=CHUNK,
            )

            while self.is_recording:
                try:
                    data = self.stream.read(CHUNK, exception_on_overflow=False)
                    self.frames.append(data)
                except OSError:
                    break
        except Exception as e:
            print(f"録音エラー: {e}")
            self.is_recording = False
        finally:
            if self.stream:
                try:
                    self.stream.stop_stream()
                    self.stream.close()
                except Exception:
                    pass

    def start_recording(self):
        self.frames = []
        self.is_recording = True
        loopback_device = self._find_loopback_device()
        print(f"録音デバイス: {loopback_device['name']}")
        self.record_thread = threading.Thread(
            target=self._record_worker, args=(loopback_device,), daemon=True
        )
        self.record_thread.start()

    def stop_recording(self):
        self.is_recording = False
        if self.record_thread:
            self.record_thread.join(timeout=3)

    def save_wav(self, filepath):
        """録音データを8000Hzモノラルに変換して保存（ファイルサイズを最小化）"""
        if not self.frames:
            print("録音データがありません。")
            return False

        raw_data = b"".join(self.frames)
        duration_sec = len(raw_data) / (self.actual_rate * self.actual_channels * self.sample_width)

        # ステレオ→モノラル変換
        total_samples = len(raw_data) // self.sample_width
        samples = list(struct.unpack(f"<{total_samples}h", raw_data))

        if self.actual_channels == 2:
            mono = [(samples[i] + samples[i + 1]) // 2
                    for i in range(0, len(samples), 2)]
        else:
            mono = samples

        # ダウンサンプリング（デバイスのレート → SAVE_RATE）
        target_rate = SAVE_RATE
        if self.actual_rate != target_rate:
            ratio = self.actual_rate / target_rate
            downsampled = [mono[int(i * ratio)]
                           for i in range(int(len(mono) / ratio))]
        else:
            downsampled = mono

        # 書き出し
        with wave.open(filepath, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(self.sample_width)
            wf.setframerate(target_rate)
            wf.writeframes(struct.pack(f"<{len(downsampled)}h", *downsampled))

        file_size_mb = os.path.getsize(filepath) / (1024 * 1024)
        print(f"録音保存: {filepath}")
        print(f"  サイズ: {file_size_mb:.1f} MB / 長さ: {duration_sec:.1f} 秒")
        print(f"  (録音: {self.actual_rate}Hz {self.actual_channels}ch → 保存: {target_rate}Hz モノラル)")
        return True

    def cleanup(self):
        if self.pa:
            try:
                self.pa.terminate()
            except Exception:
                pass


# ============================================================
# 画面録画クラス (ScreenRecorder)
# ============================================================
class ScreenRecorder:
    """ffmpeg gdigrabを使ってデスクトップ画面を録画するクラス"""
    
    def __init__(self, output_path, framerate=10, preset="ultrafast"):
        self.output_path = output_path
        self.framerate = framerate
        self.preset = preset
        self.proc = None
        self.is_recording = False
        
    def start_recording(self):
        import subprocess
        # 低負荷設定での全画面キャプチャ
        cmd = [
            "ffmpeg", "-y", "-f", "gdigrab", "-framerate", str(self.framerate),
            "-i", "desktop", "-c:v", "libx264", "-preset", self.preset,
            "-pix_fmt", "yuv420p", self.output_path
        ]
        print(f"画面録画を開始します (framerate={self.framerate}, preset={self.preset})")
        # CREATE_NO_WINDOW で ffmpeg をバックグラウンド実行（邪魔なコンソールを出さない）
        creationflags = 0
        if os.name == 'nt':
            creationflags = subprocess.CREATE_NO_WINDOW
            
        self.proc = subprocess.Popen(
            cmd, 
            stdin=subprocess.PIPE, 
            stdout=subprocess.DEVNULL, 
            stderr=subprocess.DEVNULL,
            creationflags=creationflags
        )
        self.is_recording = True
        
    def stop_recording(self):
        self.is_recording = False
        if self.proc and self.proc.poll() is None:
            try:
                # 正常終了させるために 'q' を送る
                self.proc.communicate(b"q", timeout=5)
            except Exception:
                self.proc.terminate()
                self.proc.wait()
            print("画面録画を停止しました。")



# ============================================================
# 音声ファイル圧縮（100MB制限対応）
# ============================================================

MAX_UPLOAD_MB = 95  # 余裕を持って95MBを上限とする

def convert_wav_to_smaller(input_path, output_path, target_rate=16000):
    """
    WAVファイルをモノラル・指定サンプルレートに変換する。
    """
    with wave.open(input_path, "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    # 16bit前提でサンプルを取り出す
    total_samples = n_frames * n_channels
    samples = list(struct.unpack(f"<{total_samples}h", raw))

    # ステレオ→モノラル変換（左右チャンネルの平均）
    if n_channels == 2:
        mono = [(samples[i] + samples[i + 1]) // 2
                for i in range(0, len(samples), 2)]
    else:
        mono = samples

    # ダウンサンプリング（間引き法）
    if framerate != target_rate:
        ratio = framerate / target_rate
        downsampled = [mono[int(i * ratio)]
                       for i in range(int(len(mono) / ratio))]
    else:
        downsampled = mono

    # 書き出し
    with wave.open(output_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(target_rate)
        wf.writeframes(struct.pack(f"<{len(downsampled)}h", *downsampled))


def compress_audio_for_upload(audio_filepath, language="ja"):
    """
    音声ファイルが大きすぎる場合、Geminiのアップロード時間短縮のためにWAVのみ圧縮する。
    MP3/M4A/MP4などのフォーマットはすでに圧縮されており、WAVに変換するとかえって巨大化するため
    そのままアップロードする（Geminiは2GBまで対応）。
    戻り値: (アップロード用ファイルパス, 一時ファイルかどうか)
    """
    file_size_mb = os.path.getsize(audio_filepath) / (1024 * 1024)
    _, ext = os.path.splitext(audio_filepath)
    english = language == "en"

    # --- WAVファイル以外（MP3, M4A, 等）はそのまま返す ---
    if ext.lower() != ".wav":
        if file_size_mb > 95:
            print(
                f"  File size: {file_size_mb:.1f} MB (already compressed; uploading as-is)"
                if english
                else f"  ファイルサイズ: {file_size_mb:.1f} MB (圧縮済みフォーマットのためそのままアップロードします)"
            )
        else:
            print(f"  File size: {file_size_mb:.1f} MB" if english else f"  ファイルサイズ: {file_size_mb:.1f} MB")
        return audio_filepath, False

    # --- WAVファイルの場合 ---
    if file_size_mb < 50:
        print(
            f"  File size: {file_size_mb:.1f} MB (no WAV conversion needed)"
            if english
            else f"  ファイルサイズ: {file_size_mb:.1f} MB (WAV変換不要)"
        )
        return audio_filepath, False

    # 巨大なWAVファイルの場合はサンプルレートを下げて圧縮
    print(
        f"  Compressing the {file_size_mb:.1f} MB WAV file..."
        if english
        else f"  WAVファイルサイズが {file_size_mb:.1f} MB のため圧縮します..."
    )
    tmp_path = audio_filepath.replace(".wav", "_upload.wav")

    # 極端に大きい場合は8000Hz、それ以外は16000Hz
    target_rate = 8000 if file_size_mb > 300 else 16000

    print(f"  Converting to {target_rate} Hz mono..." if english else f"  {target_rate}Hz モノラルに変換中...")
    try:
        convert_wav_to_smaller(audio_filepath, tmp_path, target_rate=target_rate)
        result_mb = os.path.getsize(tmp_path) / (1024 * 1024)
        print(
            f"  Conversion complete: {file_size_mb:.1f} MB → {result_mb:.1f} MB"
            if english
            else f"  変換完了！ {file_size_mb:.1f} MB → {result_mb:.1f} MB"
        )
        return tmp_path, True
    except Exception as e:
        print(
            f"  Warning: WAV compression failed: {e}"
            if english
            else f"  ⚠ WAV圧縮中にエラーが発生しました: {e}"
        )
        print("  Trying to upload the original file." if english else "  元のファイルのままアップロードを試みます。")
        return audio_filepath, False


# ============================================================
# フィラー（つなぎ言葉）除去
# ============================================================

import re
import json

# 除去対象のフィラーパターン（単独で出現する場合のみ除去）
_FILLER_WORDS = [
    "えーっと", "えーと", "ええと", "えっと",
    "あのー", "あの", "あのね",
    "うーん", "うーんと", "うん",
    "まあね", "まあ", "ま",
    "なんか", "なんだろう",
    "えー", "あー", "うー",
    "そのー", "その",
    "ほら", "ほらね",
    "ねー", "さー", "ね",
    "お", "え", "おー", "ええ",
]

_ENGLISH_FILLER_WORDS = [
    "you know", "um", "uh", "erm", "er", "hmm", "ah", "uh-huh",
]

def remove_fillers(text, language="ja"):
    """文字起こしテキストからフィラー（つなぎ言葉）を除去する"""
    if not text:
        return text

    # フィラーを長い順にソート（「えーっと」が「えー」より先にマッチするように）
    filler_words = _ENGLISH_FILLER_WORDS if language == "en" else _FILLER_WORDS
    fillers_sorted = sorted(filler_words, key=len, reverse=True)
    filler_pattern = "|".join(re.escape(f) for f in fillers_sorted)

    lines = text.split("\n")
    cleaned_lines = []

    for line in lines:
        # 句読点を挟んだ同じ単語（1文字〜6文字）の3回以上の繰り返しを検出し、1回にまとめる。
        repeat_pattern = r"(\w{1,6})([、。，\.\s\?？\!！]+)\1(?:\2\1)+"
        prev_line = ""
        while prev_line != line:
            prev_line = line
            line = re.sub(repeat_pattern, r"\1\2", line)
            
        # 句読点のない単純な文字の繰り返し（例：「テストテストテスト」）の対策（3回以上繰り返し）
        pattern_no_punc = r"(\w{2,4})\1{2,}"
        line = re.sub(pattern_no_punc, r"\1", line)

        # 連続した短いフィラー（「お。お。お。」など）の削除
        line = re.sub(
            rf"(?:(?:{filler_pattern})[、,，。．.]\s*){{2,}}",
            "", line
        )
        
        # 文頭のフィラー + 句読点パターンを除去（例: 「えー、今日は」→「今日は」）
        line = re.sub(
            rf"^(?:{filler_pattern})(?:[、,，。．.]\s*)+",
            "", line
        )
        # 文中の「、フィラー、」パターンを「、」に置換
        line = re.sub(
            rf"(?<=[、,，。．.])\s*(?:{filler_pattern})\s*(?=[、,，。．.])",
            "", line
        )
        # 句読点の後のフィラー + 句読点（例: 「です。えー、次に」→「です。次に」）
        line = re.sub(
            rf"([。．.])\s*(?:{filler_pattern})\s*(?:[、,，。．.]\s*)*",
            r"\1", line
        )
        # 独立した残ったフィラーを消す
        line = re.sub(
            rf"^(?:{filler_pattern})$",
            "", line
        )
        
        # 連続した句読点の整理
        line = re.sub(r"[、,，]{2,}", "、", line)
        line = re.sub(r"[。．.]{2,}", "。", line)
        
        # ゴミとして残った不要な句読点を整理
        line = re.sub(r"^[、,，。．.]+\s*", "", line)

        # 空の行にならない場合だけ追加
        if line.strip():
            cleaned_lines.append(line)

    result = "\n".join(cleaned_lines)
    # 空行が連続しすぎないように
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def _build_transcription_prompt(language=None):
    """指定した出力言語に合わせたGemini文字起こし用プロンプトを返す。"""
    if language == "en":
        return """Create a complete, readable English transcript of the attached audio.

Instructions:
- If the speech is English, transcribe it faithfully in English.
- If the speech is in another language, translate it faithfully into natural English while preserving the meaning and order of the speech.
- Use clear punctuation and paragraph breaks.
- Do not add speaker labels such as "Speaker A" or any commentary.
- Mark unintelligible portions as [inaudible].
- Output only the full transcript.

Remove non-meaningful filler words such as "um", "uh", "erm", and repeated false starts when doing so does not change the speaker's meaning."""

    lang_instruction = ""
    if language == "ja":
        lang_instruction = "音声は日本語です。日本語で文字起こししてください。"
    elif language:
        lang_instruction = f"Please transcribe in language code: {language}."

    return f"""以下の音声を文字起こししてください。

【指示】
{lang_instruction if lang_instruction else "日本語の音声は日本語で、英語は英語で文字起こししてください。"}
- 句読点や改行を適切に入れて、読みやすい形式にしてください。
- 話者を区別するためのラベル（例: 話者A、話者Bなど）は一切付けないでください。発言内容のみをそのまま記載してください。
- 聞き取れない部分は [聞き取り不可] と記載してください。
- 出力は文字起こしの全文テキストのみとしてください。余計な説明やコメントは不要です。

【重要】フィラー（つなぎ言葉）の除去:
以下のような意味のないつなぎ言葉・フィラーは全て削除し、絶対に出力に含めないでください:
「えー」「あのー」「あの」「まあ」「その」「なんか」「ええと」「うーん」「うん」「えっと」「まあね」
「とか」「ほら」「ねー」「ね」「さー」「ま」「あー」「うー」「え」「お」
文頭・文末・文中のどこにあっても削除してください。
"""


# ============================================================
# Gemini 文字起こし
# ============================================================

def transcribe_with_gemini(audio_filepath, api_key, language=None, progress_callback=None,
                           timestamps=False, duration_seconds=None):
    """Gemini APIで音声ファイルを文字起こしする"""
    from google import genai
    from gemini_retry import call_with_gemini_retry, is_retryable_gemini_error

    english = language == "en"
    print(
        f"\nConnecting to the Gemini API... (model: {GEMINI_MODEL})"
        if english
        else f"\nGemini APIに接続中... (モデル: {GEMINI_MODEL})"
    )
    client = genai.Client(api_key=api_key)

    # ファイルアップロード
    import mimetypes
    _, ext = os.path.splitext(audio_filepath)

    # 100MB制限対応: 必要に応じて圧縮
    file_size_mb = os.path.getsize(audio_filepath) / (1024 * 1024)
    if progress_callback:
        progress_callback(
            2,
            f"Checking the audio file... ({file_size_mb:.1f} MB)"
            if english
            else f"ファイルを確認中... ({file_size_mb:.1f} MB)",
        )
    print(
        f"\nSource file: {file_size_mb:.1f} MB"
        if english
        else f"\n元ファイル: {file_size_mb:.1f} MB"
    )
    upload_path, converted_tmp = compress_audio_for_upload(audio_filepath, language=language)
    converted_tmp = upload_path if converted_tmp else None

    file_size_mb = os.path.getsize(upload_path) / (1024 * 1024)
    if progress_callback:
        progress_callback(
            3,
            f"Uploading to Gemini... ({file_size_mb:.1f} MB)"
            if english
            else f"Geminiにアップロード中... ({file_size_mb:.1f} MB)",
        )
    print(
        f"Uploading audio... ({file_size_mb:.1f} MB)"
        if english
        else f"音声ファイルをアップロード中... ({file_size_mb:.1f} MB)"
    )

    # MIMEタイプを判定
    upload_ext = os.path.splitext(upload_path)[1]
    mime_type, _ = mimetypes.guess_type(upload_path)
    if not mime_type:
        mime_type = "audio/mp4" if upload_ext.lower() == ".m4a" else "audio/wav"

    safe_filename = f"audio{upload_ext}"
    def upload_audio():
        with open(upload_path, "rb") as upload_file:
            return client.files.upload(
                file=upload_file,
                config={"display_name": safe_filename, "mime_type": mime_type},
            )

    try:
        audio_file = call_with_gemini_retry(
            upload_audio,
            description="audio upload" if english else "音声アップロード",
            language="en" if english else "ja",
        )
    except Exception:
        if converted_tmp and os.path.exists(converted_tmp):
            try:
                os.remove(converted_tmp)
            except OSError:
                pass
        raise
    print(
        f"  Upload complete: {audio_file.name}"
        if english
        else f"  アップロード完了: {audio_file.name}"
    )

    def cleanup_uploaded_audio() -> None:
        """Gemini上の一時音声とローカル変換ファイルを削除する。"""
        try:
            client.files.delete(name=audio_file.name)
        except Exception:
            pass
        if converted_tmp and os.path.exists(converted_tmp):
            try:
                os.remove(converted_tmp)
            except Exception:
                pass

    # 処理完了を待機
    if progress_callback:
        progress_callback(3, "Waiting for file processing..." if english else "ファイルの処理を待機中...")
    print("Waiting for file processing..." if english else "ファイル処理を待機中...")
    wait_count = 0
    max_wait = 900  # 900回 * 2秒 = 1800秒 (30分)
    while audio_file.state.name == "PROCESSING":
        time.sleep(2)
        wait_count += 1
        if wait_count > max_wait:
            print(
                "Timed out waiting for file processing after 30 minutes. Stopping."
                if english
                else "ファイルの処理待ちがタイムアウトしました（30分経過）。処理を中止します。"
            )
            cleanup_uploaded_audio()
            return None, None
        try:
            audio_file = call_with_gemini_retry(
                lambda: client.files.get(name=audio_file.name),
                description="audio processing status check" if english else "音声処理状況の確認",
                language="en" if english else "ja",
            )
        except Exception:
            cleanup_uploaded_audio()
            raise

    if audio_file.state.name == "FAILED":
        print("File processing failed." if english else "ファイルの処理に失敗しました。")
        cleanup_uploaded_audio()
        return None, None

    prompt = _build_transcription_prompt(language)
    if timestamps:
        prompt = (("Translate speech faithfully into English. " if english else "日本語で忠実に文字起こししてください。他言語の発言は意味を保って日本語に翻訳してください。 ")
                  + "Return ONLY a JSON array of spoken segments, "
                   'each {"start": 0.0, "end": 3.2, "text": "spoken words"}. '
                   f"Times are seconds relative to this audio clip (duration {duration_seconds:.3f}s). "
                   "Use short utterances, chronological order, start < end, and end <= duration. "
                   "For an entirely silent clip return []. Do not invent speech. Preserve meaningful "
                   "words, negation and numbers; remove only meaningless hesitation sounds.")

    # 文字起こし実行
    if progress_callback:
        progress_callback(3, "Transcribing with Gemini..." if english else "Geminiで文字起こしを実行中...")
    print("Transcribing with Gemini..." if english else "Geminiで文字起こし中...")
    start_time = time.time()

    # 混雑時にモデルを切り替えて再試行するロジック
    models_to_try = [GEMINI_MODEL]
    if GEMINI_MODEL != "gemini-2.5-flash":
        models_to_try.append("gemini-2.5-flash")
    if "gemini-3.1-flash-lite" not in models_to_try:
        models_to_try.append("gemini-3.1-flash-lite")

    # thinking モデル用の設定（thinking_budget=0 で思考モードをOFFにし response.text が空になるのを防ぐ）
    from google.genai import types as genai_types
    gen_config = genai_types.GenerateContentConfig(
        thinking_config=genai_types.ThinkingConfig(thinking_budget=0)
    )

    def transcribe_once():
        last_retryable_error = None
        last_error = None
        for model in models_to_try:
            try:
                print(
                    f"Trying transcription... (model: {model})"
                    if english
                    else f"文字起こし試行中... (モデル: {model})"
                )
                try:
                    return client.models.generate_content(
                        model=model,
                        contents=[prompt, audio_file],
                        config=gen_config,
                    )
                except Exception as config_error:
                    if is_retryable_gemini_error(config_error):
                        raise
                    # thinking_config に対応していないモデルだけconfigなしで再試行
                    return client.models.generate_content(
                        model=model,
                        contents=[prompt, audio_file],
                    )
            except Exception as exc:
                last_error = exc
                if is_retryable_gemini_error(exc):
                    last_retryable_error = exc
                    print(
                        f"Model {model} is rate-limited or busy."
                        if english
                        else f"モデル {model} が制限または混雑中です。"
                    )
                    continue
                print(
                    f"Model {model} returned an error: {exc}"
                    if english
                    else f"モデル {model} でエラーが発生しました: {exc}"
                )
        if last_retryable_error is not None:
            raise last_retryable_error
        if last_error is not None:
            raise last_error
        raise RuntimeError("No Gemini model is available." if english else "利用できるGeminiモデルがありません。")

    try:
        response = call_with_gemini_retry(
            transcribe_once,
            description="transcription" if english else "文字起こし",
            language="en" if english else "ja",
        )
    except Exception as exc:
        print(f"Transcription failed: {exc}" if english else f"文字起こしに失敗しました: {exc}")
        cleanup_uploaded_audio()
        return None, None

    elapsed = time.time() - start_time
    print(
        f"Transcription complete! (processing time: {elapsed:.1f} seconds)"
        if english
        else f"文字起こし完了！ (処理時間: {elapsed:.1f} 秒)"
    )

    # response.text が None/空の場合、partsから直接テキストを取得する（thinking モデル対策）
    full_text = response.text
    if not full_text and response.candidates:
        parts_text = []
        for part in response.candidates[0].content.parts:
            if hasattr(part, 'text') and part.text and not getattr(part, 'thought', False):
                parts_text.append(part.text)
        if parts_text:
            full_text = "".join(parts_text)

    if not full_text or not full_text.strip():
        finish = response.candidates[0].finish_reason if response.candidates else "UNKNOWN"
        print(
            f"The transcript was empty. (finish_reason: {finish})"
            if english
            else f"文字起こし結果が空でした。(finish_reason: {finish})"
        )
        cleanup_uploaded_audio()
        return None, None

    full_text = full_text.strip()

    if timestamps:
        from timeline_analysis import validate_segments
        try:
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", full_text, flags=re.IGNORECASE)
            segments = validate_segments(json.loads(raw), duration_seconds)
            return "\n".join(item["text"] for item in segments), segments
        finally:
            cleanup_uploaded_audio()

    # フィラー（つなぎ言葉）の後処理除去
    full_text = remove_fillers(full_text, language="en" if english else "ja")

    cleanup_uploaded_audio()

    return full_text, ""


# ============================================================
# タイトル生成
# ============================================================

def _build_title_prompt(language="ja"):
    if language == "en":
        return """You are a professional meeting-notes assistant.
Read the transcript and return exactly one concise English title that clearly identifies the central topic of the meeting, presentation, or video. The title must be safe as a Windows file name.

Rules:
- Do not use any of these characters: \\ / : * ? " < > |
- Output only the title: no prefix, quotation marks, punctuation-only line, or explanation.
- Keep it within about 60 characters.
- Include at least one concrete keyword such as a proper noun, project, product, number, or specific topic.
- Avoid generic titles made only of words such as "Meeting", "Transcript", or "Video".
- If the content cannot be understood, output only: Transcript

Examples:
- Q3 Revenue Review and Next-Year Strategy
- New System Requirements and Rollout Plan
- AI Workflow Automation for Customer Support
- Technical Interview Feedback and Evaluation"""

    return """あなたはプロの議事録作成アシスタントです。
以下の文字起こしテキストを読み込み、この会議・発表・動画の「核心となるテーマや話題」が一目でわかる、Windowsのファイル名として使えるタイトルを1つだけ出力してください。

【重要なルール】
- ファイル名に使えない記号（\\ / : * ? " < > |）は絶対に含めないこと。
- 余計な前置き（「タイトル案：」「以下のタイトルを提案します」など）や改行は入れず、タイトル名だけを出力すること。
- 最大25文字程度の日本語で作成すること。
- 「議事録」「文字起こし」「会議」「動画」などの当たり障りのない無機質な言葉だけのタイトルは絶対に避けること。
- 具体的なキーワード（固有名詞・数値・プロジェクト名・製品名・トピックなど）を必ず1つ以上含めること。
- 内容が理解できない場合のみ「文字起こし結果」とだけ出力すること。

【良いタイトルの例】
- 「第3四半期売上報告と来期戦略」
- 「新システム導入の要件定義まとめ」
- 「AIを活用した業務効率化の取り組み」
- 「採用面接フィードバック 技術評価」
"""


def generate_title_from_text(text, api_key, language="ja"):
    """文字起こしテキストから簡潔なタイトルを生成する"""
    from google import genai
    from google.genai import types as genai_types
    import re

    english = language == "en"
    print("\nGenerating a title from the transcript..." if english else "\n文字起こし内容からタイトルを生成中...")

    # 試行するモデルの優先順位リスト
    models_to_try = [GEMINI_MODEL]
    if GEMINI_MODEL != "gemini-2.5-flash":
        models_to_try.append("gemini-2.5-flash")
    if "gemini-3.1-flash-lite" not in models_to_try:
        models_to_try.append("gemini-3.1-flash-lite")

    client = genai.Client(api_key=api_key)
    prompt = _build_title_prompt(language)

    for model in models_to_try:
        try:
            # thinkingモデル以外でthinking_configを渡すとエラーになるため分岐
            if "thinking" in model:
                gen_config = genai_types.GenerateContentConfig(
                    thinking_config=genai_types.ThinkingConfig(thinking_budget=0)
                )
            else:
                gen_config = genai_types.GenerateContentConfig()

            try:
                response = client.models.generate_content(
                    model=model,
                    contents=[prompt, text[:5000]], # 最初の5000文字程度で判定
                    config=gen_config,
                )
            except Exception:
                # configパラメータに対応していない古いモデル向け
                response = client.models.generate_content(
                    model=model,
                    contents=[prompt, text[:5000]],
                )

            # テキストの取り出し（thinkingモデル等でresponse.textが空になる対策）
            title_candidate = response.text
            if not title_candidate and response.candidates:
                parts_text = []
                for part in response.candidates[0].content.parts:
                    if hasattr(part, 'text') and part.text and not getattr(part, 'thought', False):
                        parts_text.append(part.text)
                if parts_text:
                    title_candidate = "".join(parts_text)

            if title_candidate:
                title_candidate = title_candidate.strip()
                # 引用符の除去
                title_candidate = title_candidate.replace('"', '').replace("'", "").replace("「", "").replace("」", "")
                # 改行の除去
                title_candidate = title_candidate.split("\n")[0].strip()
                # Windowsの禁止文字を削除
                title_candidate = re.sub(r'[\\/:*?"<>|]', '', title_candidate)
                
                if title_candidate:
                    print(
                        f"  Generated title: {title_candidate} (model: {model})"
                        if english
                        else f"  生成されたタイトル: {title_candidate} (モデル: {model})"
                    )
                    return title_candidate
        except Exception as e:
            print(
                f"  Could not generate a title with model {model}: {e}"
                if english
                else f"  モデル {model} でのタイトル生成に失敗しました: {e}"
            )

    return "Transcript" if english else "文字起こし結果"




# ============================================================
# Markdown / PDF生成
# ============================================================

def create_markdown(full_text, output_filepath, title="音声文字起こし",
                    audio_filename=""):
    """文字起こしテキストをMarkdownとして出力する。"""
    print("\nMarkdownを生成中...")

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"# {title}",
        "",
        f"- 作成日時: {now}",
    ]
    if audio_filename:
        lines.append(f"- ソースファイル: {audio_filename}")
    lines.extend([
        f"- モデル: {GEMINI_MODEL}",
        "",
        "---",
        "",
        "## 文字起こし全文",
        "",
        full_text.strip(),
        "",
    ])

    with open(output_filepath, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))

    file_size_kb = os.path.getsize(output_filepath) / 1024
    print(f"Markdown保存完了: {output_filepath}")
    print(f"  サイズ: {file_size_kb:.1f} KB")
    return output_filepath


def create_pdf(full_text, timestamped_text, output_filepath, audio_filename="", key_slides=None,
               document_title=None, language="ja"):
    """文字起こしテキストをPDFとして出力する"""
    from fpdf import FPDF

    english = language == "en"
    print("\nGenerating PDF..." if english else "\nPDFを生成中...")

    font_path = find_japanese_font()
    font_family = "Japanese"
    
    if not font_path:
        has_japanese = any(ord(c) >= 0x3000 for c in full_text)
        if has_japanese:
            print(
                "A required CJK font was not found. Saving a text file instead."
                if english
                else "日本語フォントが見つかりません。テキストファイルとして保存します。"
            )
            txt_path = output_filepath.replace(".pdf", ".txt")
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(full_text)
            print(f"Text file saved: {txt_path}" if english else f"テキスト保存: {txt_path}")
            return txt_path
        else:
            print(
                "No Japanese font was found; using the standard Helvetica font for English text."
                if english
                else "日本語フォントが見つかりませんが、アルファベットテキストのため標準フォント(Helvetica)を使用します。"
            )
            font_family = "Helvetica"

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    
    if font_family == "Japanese":
        pdf.add_font("Japanese", "", font_path)
        pdf.add_font("Japanese", "B", font_path)

    def pdf_text(value):
        text = str(value)
        if font_family == "Helvetica":
            return text.encode("latin-1", errors="replace").decode("latin-1")
        return text

    pdf.add_page()

    # タイトルとヘッダー情報
    if font_family == "Japanese":
        pdf.set_font("Japanese", "B", size=18)
        pdf.cell(0, 15, pdf_text(document_title or ("Video Analysis Report" if english else "音声文字起こし")),
                 new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.ln(3)

        pdf.set_font("Japanese", "", size=9)
        pdf.set_text_color(100, 100, 100)
        now_value = datetime.datetime.now()
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") if english else (
            f"{now_value:%Y}年{now_value:%m}月{now_value:%d}日 "
            f"{now_value:%H:%M:%S}"
        )
        pdf.cell(0, 6, f"Created: {now}" if english else f"作成日時: {now}", new_x="LMARGIN", new_y="NEXT")
        if audio_filename:
            pdf.cell(
                0,
                6,
                pdf_text(f"Source File: {audio_filename}" if english else f"ソースファイル: {audio_filename}"),
                new_x="LMARGIN",
                new_y="NEXT",
            )
        pdf.cell(0, 6, f"Model: {GEMINI_MODEL}" if english else f"モデル: {GEMINI_MODEL}", new_x="LMARGIN", new_y="NEXT")
    else:
        pdf.set_font("Helvetica", "B", size=18)
        pdf.cell(
            0,
            15,
            pdf_text(document_title or ("Video Analysis Report" if english else "Audio Transcription")),
                 new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.ln(3)

        pdf.set_font("Helvetica", "", size=9)
        pdf.set_text_color(100, 100, 100)
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        pdf.cell(0, 6, f"Created: {now}", new_x="LMARGIN", new_y="NEXT")
        if audio_filename:
            pdf.cell(0, 6, pdf_text(f"Source File: {audio_filename}"), new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 6, f"Model: {GEMINI_MODEL}", new_x="LMARGIN", new_y="NEXT")
        
    pdf.set_text_color(0, 0, 0)

    pdf.ln(5)
    pdf.set_draw_color(200, 200, 200)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(8)

    # キースライド出力
    if key_slides:
        if not english and font_family == "Japanese":
            pdf.set_font("Japanese", "B", size=14)
            pdf.cell(0, 10, "【 画像解析結果 】", new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.set_font(font_family, "B", size=14)
            pdf.cell(0, 10, "[ Image Analysis Results ]", new_x="LMARGIN", new_y="NEXT")
            
        pdf.ln(2)
        pdf.set_font(font_family, "", size=PDF_FONT_SIZE)
        
        for i, slide in enumerate(key_slides):
            analysis = slide.get("analysis", {})
            title = (
                f"Analysis {i+1} - {slide['timestamp_str']}"
                if english
                else f"解析 {i+1} - {slide['timestamp_str']}"
            )
            pdf.set_font(font_family, "B", size=11)
            pdf.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
            pdf.set_font(font_family, "", size=PDF_FONT_SIZE)
            
            # 解析結果
            pdf.set_text_color(80, 80, 80)
            score = analysis.get('importance_score', 0)
            pdf.cell(
                0,
                6,
                f"Importance: {score}/100" if english else f"重要度: {score}/100",
                new_x="LMARGIN",
                new_y="NEXT",
            )
            pdf.set_text_color(0, 0, 0)
            
            summary = analysis.get('summary', '')
            if summary:
                pdf.ln(2)
                for line in summary.split("\n"):
                    if font_family == "Helvetica":
                        line = line.encode("latin-1", errors="replace").decode("latin-1")
                    pdf.multi_cell(0, PDF_LINE_HEIGHT, line, wrapmode="CHAR" if font_family == "Japanese" else "WORD")

            detected_text = analysis.get('detected_text', '')
            if detected_text:
                pdf.ln(2)
                pdf.set_font(font_family, "B", size=10)
                pdf.cell(
                    0,
                    6,
                    "Key Text in Image" if english else "画像内の主要テキスト",
                    new_x="LMARGIN",
                    new_y="NEXT",
                )
                pdf.set_font(font_family, "", size=PDF_FONT_SIZE)
                pdf.multi_cell(
                    0,
                    PDF_LINE_HEIGHT,
                    pdf_text(detected_text),
                    wrapmode="CHAR" if font_family == "Japanese" else "WORD",
                )
            
            pdf.ln(5)
            pdf.set_draw_color(220, 220, 220)
            pdf.line(10, pdf.get_y(), 200, pdf.get_y())
            pdf.ln(5)



    # 全文字起こしのヘッダー
    if not english and font_family == "Japanese":
        pdf.set_font("Japanese", "B", size=14)
        pdf.cell(0, 10, "【 文字起こし全文 (Full Transcript) 】", new_x="LMARGIN", new_y="NEXT")
    else:
        pdf.set_font(font_family, "B", size=14)
        pdf.cell(0, 10, "[ Full Transcript ]", new_x="LMARGIN", new_y="NEXT")
        
    pdf.ln(2)

    # 本文の出力
    pdf.set_font(font_family, "", size=PDF_FONT_SIZE)
    for para in full_text.split("\n"):
        if para.strip() == "":
            pdf.ln(4)
        else:
            if font_family == "Helvetica":
                para_clean = para.strip().encode("latin-1", errors="replace").decode("latin-1")
                pdf.multi_cell(0, PDF_LINE_HEIGHT, para_clean)
            else:
                pdf.multi_cell(0, PDF_LINE_HEIGHT, para.strip(), wrapmode="CHAR")
            pdf.ln(1)

    pdf.output(output_filepath)
    file_size_kb = os.path.getsize(output_filepath) / 1024
    print(f"PDF saved: {output_filepath}" if english else f"PDF保存完了: {output_filepath}")
    print(f"  Size: {file_size_kb:.1f} KB" if english else f"  サイズ: {file_size_kb:.1f} KB")
    return output_filepath



# ============================================================
# メイン処理
# ============================================================

def main():
    global GEMINI_MODEL

    # コマンドライン引数
    parser = argparse.ArgumentParser(
        description="Gemini Voice Transcriber - Audio Transcription to Markdown and PDF"
    )
    parser.add_argument("audio_file", nargs="?", default=None,
                        help="音声/動画ファイルのパス（省略でPC音声録音モード）")
    parser.add_argument("-l", "--language", default=None,
                        help="言語コード (例: ja, en)")
    parser.add_argument("-m", "--model", default=None,
                        choices=["gemini-2.5-flash", "gemini-3.1-flash-lite",
                                 "gemini-3.5-flash", "gemini-2.5-pro"],
                        help="Geminiモデル")

    # 動画キースライド抽出オプション
    parser.add_argument("--video", default=None,
                        help="動画ファイルのパス（キースライド抽出＋文字起こし）")
    parser.add_argument("--extract-key-slides", action="store_true",
                        help="動画からキースライドを抽出する")
    parser.add_argument("--frame-interval", type=int, default=60,
                        help="フレーム抽出間隔（秒）（デフォルト: 60）")
    parser.add_argument("--max-key-slides", type=int, default=15,
                        help="最大キースライド数（デフォルト: 15）")
    parser.add_argument("--analyze-max-frames", type=int, default=50,
                        help="解析する最大フレーム数（デフォルト: 50）")
    parser.add_argument("--output-format", default="md",
                        choices=["md", "json"],
                        help="出力フォーマット（デフォルト: md）")
    parser.add_argument("--dry-run", action="store_true",
                        help="Gemini APIを呼ばず、フレーム抽出だけ確認する")
    parser.add_argument("--output-dir", default=None,
                        help="出力先ディレクトリ（デフォルト: output/）")
    
    # リアルタイム画面録画オプション
    parser.add_argument("--record-screen", action="store_true",
                        help="PC音声と一緒にデスクトップ画面をリアルタイム録画し、終了後に全自動でスライド抽出と文字起こしを行う")

    args = parser.parse_args()

    if args.model:
        GEMINI_MODEL = args.model

    print("=" * 50)
    print("  Gemini Voice Transcriber")
    print("=" * 50)
    print(f"  モデル: {GEMINI_MODEL}")
    print()

    # APIキー確認
    api_key = os.environ.get("GEMINI_API_KEY", API_KEY).strip()
    if not api_key:
        print("【初回セットアップ】")
        print("APIキーが設定されていません。")
        print("APIキー取得: https://aistudio.google.com/apikey")
        print()
        if not sys.stdin.isatty():
            print("[エラー] 非対話環境ではAPIキーを入力できません。環境変数 GEMINI_API_KEY を設定するか、.envファイルを作成してください。")
            sys.exit(1)
            
        user_key = input("取得したAPIキーをここに貼り付けてEnterを押してください: ").strip()
        if not user_key:
            print("APIキーが入力されませんでした。終了します。")
            sys.exit(1)
        api_key = user_key
        # .env に安全に保存
        save_api_key_to_env(api_key)
        print("APIキーを保存しました！次回からは入力不要です。")
        print("-" * 50)

    # 出力ディレクトリ作成
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    # モード分岐
    if args.video or args.extract_key_slides:
        # === 動画キースライド抽出モード ===
        video_path = args.video or args.audio_file
        if not video_path:
            print("エラー: --video で動画ファイルを指定してください。")
            sys.exit(1)
        video_path = os.path.abspath(video_path)
        if not os.path.exists(video_path):
            print(f"ファイルが見つかりません: {video_path}")
            sys.exit(1)

        from key_slide_extractor import KeySlideExtractor
        extractor = KeySlideExtractor(
            api_key=api_key,
            model=GEMINI_MODEL,
            frame_interval=args.frame_interval,
            max_key_slides=args.max_key_slides,
            analyze_max_frames=args.analyze_max_frames,
            dry_run=args.dry_run,
            output_dir=args.output_dir or OUTPUT_DIR,
            skip_frame_analysis=not args.extract_key_slides,
        )
        result = extractor.run(video_path)
        if not result["success"]:
            sys.exit(1)
            
        return

    elif args.audio_file:
        # 既存ファイルを文字起こし
        audio_filepath = os.path.abspath(args.audio_file)
        if not os.path.exists(audio_filepath):
            print(f"ファイルが見つかりません: {audio_filepath}")
            sys.exit(1)
        file_size_mb = os.path.getsize(audio_filepath) / (1024 * 1024)
        print(f"入力ファイル: {audio_filepath}")
        print(f"  サイズ: {file_size_mb:.1f} MB")
        audio_filename = os.path.basename(audio_filepath)
    else:
        # PC音声をリアルタイム録音（＋録画）
        screen_recorder = None
        video_filepath = None
        
        if args.record_screen:
            from key_slide_extractor import check_ffmpeg, print_ffmpeg_install_guide
            if not check_ffmpeg():
                print_ffmpeg_install_guide()
                print("\nエラー: 画面録画モードには ffmpeg が必要です。インストール後に再実行してください。")
                sys.exit(1)
                
            print("\n" + "!" * 50)
            print("  ⚠️ プライバシーに関する警告 ⚠️")
            print("!" * 50)
            print("  画面録画モードでは、デスクトップ全体が録画されます。")
            print("  以下の点に注意してください：")
            print("  - パスワードや個人情報を表示しないこと")
            print("  - 通知や個人的なチャット画面を閉じておくこと")
            print("  - 会議などで必要な録画の許可を得ていること")
            print("  - 上に重なった別のウィンドウも一緒に録画されてしまいます\n")
            try:
                ans = input("  上記に同意して画面録画を開始しますか？ [y: 開始する / n: キャンセル] (デフォルト: n): ")
            except EOFError:
                ans = 'n'
            
            if ans.lower() != 'y':
                print("録画をキャンセルしました。")
                sys.exit(0)
                
            os.makedirs(os.path.join(OUTPUT_DIR, "screen_recordings"), exist_ok=True)
            video_filename = f"screen_{timestamp}.mp4"
            video_filepath = os.path.join(OUTPUT_DIR, "screen_recordings", video_filename)
            screen_recorder = ScreenRecorder(output_path=video_filepath, framerate=10, preset="ultrafast")

        recorder = AudioRecorder()
        print("\n【使い方】")
        if args.record_screen:
            print("  1. Enterキー → 画面録画 ＆ 録音 開始")
            print("  2. もう一度Enterキー → 停止して解析開始")
        else:
            print("  1. Enterキー → 録音開始")
            print("  2. もう一度Enterキー → 録音停止")
        print()

        try:
            input(">>> Enterキーを押して開始...")
        except KeyboardInterrupt:
            print("\n中止しました。")
            sys.exit(0)

        print()
        if args.record_screen:
            print("●画面録画 ＆ 音声録音中... (Enterキーで停止)")
            screen_recorder.start_recording()
        else:
            print("●録音中... (Enterキーで停止)")
            
        recorder.start_recording()

        try:
            input()
        except (KeyboardInterrupt, EOFError):
            pass

        print("停止処理中...")
        recorder.stop_recording()
        if args.record_screen:
            screen_recorder.stop_recording()

        audio_filename = f"recording_{timestamp}.wav"
        audio_filepath = os.path.join(OUTPUT_DIR, audio_filename)

        if not recorder.save_wav(audio_filepath):
            print("録音データの保存に失敗しました。")
            recorder.cleanup()
            sys.exit(1)
        recorder.cleanup()
        
        # 画面録画していた場合はキースライド抽出へ投げて終了
        if args.record_screen and os.path.exists(video_filepath):
            from key_slide_extractor import KeySlideExtractor
            extractor = KeySlideExtractor(
                api_key=api_key,
                model=GEMINI_MODEL,
                frame_interval=args.frame_interval,
                max_key_slides=args.max_key_slides,
                analyze_max_frames=args.analyze_max_frames,
                dry_run=args.dry_run,
                output_dir=args.output_dir or OUTPUT_DIR,
                skip_frame_analysis=False,
            )
            result = extractor.run(video_path=video_filepath, provided_audio_path=audio_filepath)
            if not result["success"]:
                sys.exit(1)
            return

    # Gemini 文字起こし (録音のみ、または既存ファイルの場合)
    full_text, timestamped_text = transcribe_with_gemini(
        audio_filepath, api_key, language=args.language
    )

    if not full_text or not full_text.strip():
        print("文字起こし結果が空です。音声が含まれていない可能性があります。")
        sys.exit(1)

    # タイトルの自動生成
    title_name = generate_title_from_text(full_text, api_key)
    # ファイル名用のクリーンアップ（念のため）
    import re
    title_name = re.sub(r'[\\/:*?"<>|]', '', title_name).strip()
    if not title_name:
        title_name = "文字起こし結果"

    # 結果を表示
    print("\n" + "=" * 50)
    print("文字起こし結果:")
    print("=" * 50)
    print(full_text)
    print("-" * 50)

    # Markdown / PDF生成
    md_filename = f"{title_name}_{timestamp}.md"
    md_filepath = os.path.join(OUTPUT_DIR, md_filename)
    create_markdown(
        full_text,
        md_filepath,
        title=title_name,
        audio_filename=audio_filename,
    )

    pdf_filename = f"{title_name}_{timestamp}.pdf"
    pdf_filepath = os.path.join(OUTPUT_DIR, pdf_filename)
    create_pdf(full_text, timestamped_text, pdf_filepath, audio_filename=audio_filename)

    # 完了
    print("\n" + "=" * 50)
    print("  すべての処理が完了しました！")
    print("=" * 50)
    print(f"\n  出力フォルダ: {OUTPUT_DIR}")
    print(f"  Markdown: {md_filename}")
    print(f"  PDF:  {pdf_filename}")
    print()


if __name__ == "__main__":
    main()
