# 🎞️ Gemini Video Analyzer（動画解析・文字起こしツール）

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Gemini](https://img.shields.io/badge/AI-Gemini%203.5-blueviolet.svg)](https://aistudio.google.com/)

A Windows desktop application that records or analyzes video, selects meaningful scene changes locally, and uses the Gemini API for transcription and analysis. The recommended workflow keeps the recording and a structured PDF report.

Windowsで動作する、録画・動画解析・文字起こしツールです。PC内で重要な場面候補を選別し、Gemini APIを活用して整形済みPDFを自動生成します。

## 新しい操作画面 / Desktop interface

v2.3.0では、`Start.bat` / `Start_EN.bat` からボタン式の操作画面が開きます。「録画する」「録音する」「動画を選ぶ」「結果を見る」が主な操作です。「録音する」では画面を録画せずPC音声のみを扱い、「録音だけ保存（完全ローカル・APIキー不要）」または「録音して文字起こし」を選べます。保存・Gemini送信用の音声は自動で超軽量圧縮（Opus 32 kbps / M4A 48 kbps）されます。設定で言語・録画保存先・画像枚数と音声時間の上限を変更できます。詳細と復旧時の注意点は [操作ガイド](USAGE.md) を参照してください。

Version 2.3.0 opens a desktop interface from `Start.bat` / `Start_EN.bat`. Use **Record**, **Record Audio**, **Choose video**, and **View results**. **Record Audio** captures PC audio without screen recording, allowing you to choose between "Save Audio Only" (completely local, no API key required) or "Transcribe Audio". Audio for storage and Gemini upload is automatically compressed for extreme lightweight efficiency (Opus 32 kbps / M4A 48 kbps). Settings control language, recording folder, image count and audio duration limits. See the [usage and recovery guide](USAGE.md).

---

## 実装のポイント / Engineering highlights

- [録画](local_screen_recorder.py)・[ローカル動画処理](local_video_analyzer.py)と、[Gemini連携・PDF生成](gemini_hybrid_analyzer.py)を分離。
- APIの一時エラーに対する[再試行](gemini_retry.py)と、処理済み結果を保存した途中再開に対応。
- [テスト](tests/)と[日英の配布物](https://github.com/dev-yoshitani/gemini-video-analyzer/releases/latest)を用意。WindowsとGemini APIキーが必要です。

## Workflow

```text
record or select a video
  → extract audio and scene candidates locally
  → review the cloud-processing notice
  → send audio and selected images to Gemini
  → keep the recording, PC audio, and structured PDF report
```

The complete source video is not uploaded. The application sends the extracted audio and locally selected candidate images only after confirmation. API usage and data handling are subject to the Gemini API terms and the user's Google AI Studio plan.

---

## Language / 言語
- [English (README_EN)](#english)
- [日本語 (README_JA)](#日本語)

---

## English

## 📥 Download and install

[![Download English ZIP](https://img.shields.io/badge/Download-English%20ZIP-green?style=for-the-badge&logo=github)](https://github.com/dev-yoshitani/gemini-video-analyzer/releases/latest/download/Gemini-Video-Analyzer-Windows-English.zip)

From [GitHub Releases](https://github.com/dev-yoshitani/gemini-video-analyzer/releases/latest), download `Gemini-Video-Analyzer-Windows-English.zip`, extract it, and install the included dependencies once with Python 3.10 or later.

### ✨ Features
- **🆕 Ultra-lightweight Audio-only Modes**: Record internal PC audio without screen capture. Choose "Save Audio Only" (completely local, zero API usage, no API key needed) or "Transcribe Audio" (audio + PDF generation).
- **Ultra-efficient Audio Compression**: Compresses audio to Opus (32 kbps mono, `audio/ogg`) for Gemini Files API and M4A (48 kbps mono, `audio/m4a`) for local storage, reducing audio payload sizes by up to 98% (approx. 1/48th of original 48 kHz stereo WAV) with automatic duration integrity verification.
- **Real-time System Audio Recording**: Captures computer internal audio using WASAPI loopback.
- **Pre-recording Audio Test**: Checks the actual system-audio level for three seconds before screen recording begins.
- **Gemini transcription and analysis**: Uses `gemini-3.5-flash`, with automatic fallback to `gemini-2.5-flash` and `gemini-3.1-flash-lite` when the primary model is rate-limited or busy.
- **Automatic Rate-limit Recovery**: Waits and retries automatically for temporary 429/503 errors. Completed stages and images are reused after interruption.
- **Video Key Slide Extraction**: Automatically extracts important slides, charts, and documents from recorded videos using Gemini's vision capabilities.
- **Filler Word Removal**: Automatically strips out filler words (e.g., "uhm", "uh", "like") and resolves hallucinated repetitions.
- **Clean PDF Output**: Keeps the original recording, captured PC audio, and the formatted PDF. Temporary images and analysis files are removed after success.

### 🛠️ Initial Setup: Get Gemini API Key
1. Go to [Google AI Studio](https://aistudio.google.com/apikey).
2. Generate a free API key.

*(No complicated environment variable setup is required! You will be prompted to enter this key the first time you run the app.)*

---

### 🚀 Usage

#### 1. Install once

**Prerequisites:** Windows 10/11 and Python 3.10 or later.

Open PowerShell in the extracted folder and run:
```powershell
py -3 -m pip install -r requirements-app.txt
```

#### 2. Start the recommended workflow

Double-click **Start_EN.bat** for the English workflow. It offers these choices:

1. **Record**: select an area, pass the audio test, then pause or finish using buttons.
2. **Choose video**: select a saved video for analysis.
3. **Resume selected**: continue an unfinished job or recover finalized recording chunks.

You can also drag a video file onto **Start_EN.bat** to analyze it. Before recording, choose the full desktop, one monitor, or a mouse-selected area. Start playback on the PC before the three-second audio test; recording starts only after sound is detected.

The English workflow creates an English transcript and English frame analysis. English speech is transcribed directly; other spoken languages are translated into natural English. Detected on-screen text is summarized or translated into English for the PDF.

> [!WARNING]
> **Screen Recording Mode Privacy Notice**
> - Only the selected recording area is recorded. Notifications or personal chats within that area can still be captured.
> - The raw video is kept on your PC. Audio and locally selected scene-candidate images are sent to the Gemini API only after confirmation.
> - Please ensure you hide sensitive information before starting.
> - Ensure you have permission to record the meeting.

The Japanese interface is available from `Start.bat`. The legacy audio-only menu remains available through `python launcher.py`.

On success, a recording session keeps this user-facing output:

```text
output/local_recordings/Recording_<timestamp>/
├── Screen Recording.mp4
├── PC Audio.wav
└── Analysis Results/
    └── <generated-title>_Analysis_Report.pdf
```

The exact localized file names depend on the selected language. Intermediate candidate images and analysis state are removed only after the PDF is created successfully; interrupted work can be resumed.

If this project is useful, please consider giving it a star ⭐
---



## 日本語

## 📥 ダウンロードと初回準備

[![日本語版ZIPをダウンロード](https://img.shields.io/badge/ダウンロード-日本語版ZIP-green?style=for-the-badge&logo=github)](https://github.com/dev-yoshitani/gemini-video-analyzer/releases/latest/download/Gemini-Video-Analyzer-Windows-Japanese.zip)

[GitHub Releases](https://github.com/dev-yoshitani/gemini-video-analyzer/releases/latest)から`Gemini-Video-Analyzer-Windows-Japanese.zip`をダウンロードして解凍します。最初の一回だけPython 3.10以降で必要なライブラリを入れます。

### ✨ 主な機能
- **🆕 超軽量PC音声録音モード**: 画面を録画せずPC内部音声のみを記録。「録音だけ保存（完全ローカル・API不要・APIキー未設定でも使用可能）」と「録音して文字起こし（音声＋PDF作成）」に対応。
- **高効率音声圧縮（最大約1/48削減）**: Gemini送信用にOpus（32 kbps mono、`audio/ogg`）、ローカル保存用にM4A（48 kbps mono、`audio/m4a`）を元WAVから直接生成。元ファイル比最大98%削減（約1/48）しつつ、整合性検証（duration一致）合格後にのみ元WAVを安全置換。
- **PCシステム音声録音**: WASAPIループバックを使用し、会議や動画の音声をクリアに直接録音。
- **録画前の音声テスト**: 録画開始前にPC音声を3秒間確認し、無音やデバイス異常のまま長時間録画することを防止。
- **Geminiによる文字起こし・解析**: `gemini-3.5-flash` を使用（制限・混雑時は `gemini-2.5-flash`、さらに `gemini-3.1-flash-lite` へ自動切り替え）。
- **API制限からの自動復旧**: 一時的な429/503エラーでは段階的に待機して自動再試行。中断後も完了済みの工程と画像解析を再利用。
- **動画キースライド抽出**: 会議や授業の録画動画から、重要なスライド・チャート・資料をGeminiのAI解析で自動抽出。文字起こしと統合したリッチ議事録を生成。
- **つなぎ言葉（フィラー）の自動除去**: 「えーっと」「あのー」などを自動で取り除き、同じ言葉が連続するループ現象（ハルシネーション）も自動で除去。
- **整理されたPDF出力**: 正常終了後は画面録画、PC音声、AIがタイトルを付けたPDFだけを残し、中間画像や解析用ファイルを自動削除。

### 🎯 高精度AI解析 + PDF（推奨）

`Start.bat` の「録画する」「動画を選ぶ」から、PC内でシーン候補と音声を取り出し、Geminiで文字起こしと画像内容の解析を行います。元動画全体は送信せず、音声と候補画像だけを確認後に送信します。

出力PDFには画像そのものを埋め込まず、各場面の時刻・重要度・画像解析の説明・画像内で検出した主要テキストと、文字起こし全文をまとめます。抽出画像などの中間ファイルはPDF完成後に自動削除します。

初回だけ、アプリに必要なライブラリをインストールします：

```powershell
py -3 -m pip install -r requirements-app.txt
```

普段は `Start.bat` を開きます。「録画する」では録画範囲を選択後、3秒間の音声テストを行います。「録画を終了して解析」を押し、クラウド送信に同意するとPDF生成まで進みます。動画を `Start.bat` にドラッグ＆ドロップする方法も利用できます。従来のコンソール画面は `python launcher.py` から利用できます。

高精度AI解析中に一時的なAPI制限や混雑を検出すると、待機時間を段階的に延ばしながら自動再試行します。それでも完了できない場合やアプリを終了した場合は、シーン候補・文字起こし・画像ごとの解析結果が自動保存されます。次回 `Start.bat` の「未完了の高精度AI解析を途中から再開」を選ぶと、完了済みの処理を再利用し、残りから続行します。

録画データは `output/local_recordings/録画_日時/` に保存されます。中には `画面録画.mp4`、`PC音声.wav`、`AI解析結果` フォルダが作られ、解析結果は「内容のタイトル_解析レポート.pdf」のような分かりやすい名前で保存されます。選んだ録画範囲内に通知や個人的な情報が映らないことを、開始前に確認してください。

### 🛠️ 初期設定: Gemini API キーの取得
1. [Google AI Studio](https://aistudio.google.com/apikey) にアクセス。
2. 無料のAPIキーを作成します。

*(※難しい環境変数の設定は不要です！アプリを最初に起動したときに画面から入力できます。)*

---

### 🚀 使い方

#### 1. おすすめの使い方

**Start.bat** をダブルクリックします。以下を選べます。

1. 録画して高精度AI解析・解析結果PDF
2. 保存済み動画を高精度AI解析・解析結果PDF
3. 未完了の高精度AI解析を途中から再開
4. 従来のGemini文字起こしツール

動画ファイルを **Start.bat** へドラッグ＆ドロップしても解析できます。録画前には「すべての画面」「モニターを1台選ぶ」「マウスで範囲指定」から範囲を選びます。

> [!WARNING]
> **画面録画モードの注意（プライバシーについて）**
> - 選んだ録画範囲内の画面だけが録画されますが、通知や個人情報が映らないように注意してください。
> - **重要**: 元動画はPC内に残ります。Geminiへ送るのは、確認後のPC音声とPC内で厳選した場面候補画像だけです。
> - パスワードや機密情報が映り込まないよう十分ご注意ください。
> - 会議などで必要な録画の許可を得た上でご利用ください。

従来の音声文字起こしだけを使う場合は、`Start.bat`のメニュー4を選びます。

このプロジェクトが役に立ったら、Starを押してもらえると嬉しいです ⭐
---


## 🗺️ Roadmap / 今後の予定
- [x] Support export as Markdown / Markdown形式での書き出しサポート
- [ ] Real-time progressive transcription / リアルタイム順次文字起こし表示
- [ ] Support macOS (CoreAudio) / macOSへの対応

---

## Repository rename and compatibility

The repository was renamed from `gemini-voice-transcriber` to `gemini-video-analyzer` because the primary workflow now covers recording, local scene selection, Gemini analysis, transcription, and PDF reporting. Legacy internal names such as `audio_transcriber.py` and `Gemini_CLI_Transcriber` remain unchanged so existing launchers and scripts keep working.

---

## 📝 License / ライセンス
This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
本プロジェクトはMITライセンスのもとで公開されています。詳細は [LICENSE](LICENSE) ファイルをご覧ください。
