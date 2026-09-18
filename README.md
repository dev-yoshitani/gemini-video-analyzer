# 🎞️ Gemini Video Analyzer（動画解析・文字起こしツール）

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Gemini](https://img.shields.io/badge/AI-Gemini%203.5-blueviolet.svg)](https://aistudio.google.com/)

A Windows desktop application that records or analyzes video, selects meaningful scene changes locally, and uses the Gemini API for transcription and analysis. The recommended workflow keeps the recording and a structured PDF report.

Windowsで動作する録画・動画解析・文字起こしツールです。PC内で重要な場面の候補を自動選別し、Gemini APIを活用して要約・文字起こし・画像解説をまとめたPDFレポートを生成します。

## 新しい操作画面 / Desktop interface

v2.3.0より、`Start.bat`（英語版は `Start_EN.bat`）から直感的なボタン操作の画面を利用できるようになりました。「録画する」「録音する」「動画を選ぶ」「結果を見る」の4つの基本操作で進められます。「録音する」では画面をキャプチャせずPC内部音声のみを記録でき、「録音だけ保存（完全ローカル・APIキー不要）」と「録音して文字起こし」のいずれかを選択できます。保存およびGemini送信用の音声データは、自動的に超軽量フォーマット（Opus 32 kbps / M4A 48 kbps）へ高圧縮されます。設定画面では表示言語、保存先フォルダ、画像候補数や音声時間の上限を調整できます。詳しい使い方や中断時の復旧手順は [操作ガイド](USAGE.md) をご覧ください。

Version 2.3.0 opens a desktop interface from `Start.bat` / `Start_EN.bat`. Use **Record**, **Record Audio**, **Choose video**, and **View results**. **Record Audio** captures PC audio without screen recording, allowing you to choose between "Save Audio Only" (completely local, no API key required) or "Transcribe Audio". Audio for storage and Gemini upload is automatically compressed for extreme lightweight efficiency (Opus 32 kbps / M4A 48 kbps). Settings control language, recording folder, image count and audio duration limits. See the [usage and recovery guide](USAGE.md).

---

## 実装のポイント / Engineering highlights

- **責務の分離**: [録画処理](local_screen_recorder.py)・[ローカル動画処理](local_video_analyzer.py)と、[Gemini連携・PDF生成](gemini_hybrid_analyzer.py)を明確に分離。
- **耐障害性**: APIの一時的なエラーに対する[自動再試行](gemini_retry.py)と、処理済み区間を保持した安全な途中再開に対応。
- **検証と国際化**: 各種[テスト](tests/)を整備し、日英両対応の[配布パッケージ](https://github.com/dev-yoshitani/gemini-video-analyzer/releases/latest)を提供。

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

[GitHub Releases](https://github.com/dev-yoshitani/gemini-video-analyzer/releases/latest) から `Gemini-Video-Analyzer-Windows-Japanese.zip` をダウンロードして解凍します。

#### 前提環境とインストール
- **対応OS**: Windows 10 / 11
- **Python環境**: Python 3.10 以降（Tkinterを含む標準インストーラー版）

解凍したフォルダ内でPowerShellを開き、初回のみ以下のコマンドで必要なライブラリをインストールします。

```powershell
py -3 -m pip install -r requirements-app.txt
```

### 🛠️ 初期設定: Gemini APIキーの取得
1. [Google AI Studio](https://aistudio.google.com/apikey) で無料のAPIキーを取得します。
2. 初回起動時にAPIキーの入力画面が表示されます。一度入力すれば利用可能になります（環境変数の手動設定は不要です）。
   ※「録音だけ保存（完全ローカル）」を使用する場合、APIキーは不要です。

---

### 🚀 使い方

#### 1. アプリの起動
`Start.bat` をダブルクリックして起動します。ボタン操作のデスクトップ画面が開きます。

- **録画する**: 録画範囲（「すべての画面」「モニターを1台選ぶ」「マウスで範囲指定」）を選び、3秒間の音声テストを経て録画を開始します。録画終了後、内容の確認を経てAI解析とPDFレポート生成へ進みます。
- **録音する**: 画面録画を行わずPC内部音声のみを記録します。「録音だけ保存（完全ローカル・API不要）」と「録音して文字起こし」のいずれかを選択できます。
- **動画を選ぶ**: 保存済みの動画ファイルを選択してAI解析を行います。動画ファイルを `Start.bat` に直接ドラッグ＆ドロップして解析することも可能です。
- **結果を見る**: 作成されたPDFレポートや保存先フォルダを素早く開きます。

#### 2. 中断と途中再開
処理中に一時的なAPI制限（429エラー等）や混雑を検知した場合は、待機時間を調整しながら自動で再試行します。途中でアプリを終了した場合でも、完了済みの文字起こしや画像解析データが保存されているため、次回起動時に「未完了一覧」から続きを安全に再開できます。

#### 3. 保存先と出力ファイル
録画セッションが完了すると、成果物は `output/local_recordings/録画_<日時>/` に保存されます。

```text
output/local_recordings/録画_<日時>/
├── 画面録画.mp4
├── PC音声.wav（または圧縮音声）
└── AI解析結果/
    └── <内容に応じたタイトル>_解析レポート.pdf
```
PDFレポートの完成後、抽出に使用した一時的な中間画像や作業用データは自動で削除されます。

> [!WARNING]
> **プライバシーとデータ送信に関する注意**
> - **元動画はクラウドへ送信されません**: PC内で抽出した音声と、選別された重要場面の静止画候補のみを、ユーザーの確認を経てGemini APIへ送信します。
> - **録画範囲の確認**: 選択した録画範囲内の画面のみが記録されます。通知ポップアップや個人チャットなど、機密情報の映り込みにご注意ください。
> - **録画の同意**: 会議や授業を録画・録音する際は、参加者の同意や利用規約を事前にご確認ください。

※従来のコンソールメニューで利用したい場合は、`python launcher.py` を実行してください。

---

### ✨ 主な機能

- **🆕 超軽量PC音声録音モード**: 画面を録画せずPC内部音声のみを記録。「録音だけ保存（完全ローカル・API不要・APIキー未設定でも使用可能）」と「録音して文字起こし（音声＋PDF作成）」に対応。
- **高効率な音声圧縮（最大約98%削減）**: 元のWAV音声から、Gemini送信用にOpus（32 kbps mono、`audio/ogg`）、ローカル保存用にM4A（48 kbps mono、`audio/m4a`）を直接生成。元ファイル比で最大98%（約1/48）削減し、再生時間の整合性検証に合格した後にのみ元ファイルを安全に置き換えます。
- **クリアなPCシステム音声録音**: WASAPIループバック録音を採用し、オンライン会議や動画の音声をノイズなく直接キャプチャ。
- **録画前の音声テスト**: 録画開始前にPCの再生音声を3秒間チェックし、無音やオーディオデバイスの不調に気づかないまま録画してしまうミスを防止。
- **Geminiによる高精度な文字起こし・解析**: 高速・高精度な `gemini-3.5-flash` を標準採用。混雑時や利用制限時には `gemini-2.5-flash` や `gemini-3.1-flash-lite` へ自動で切り替えて処理を継続。
- **API制限からの自動復旧**: 429や503などの一時的エラーを検知すると指数バックオフで自動再試行。中断時も完了済みの文字起こしや画像解析を再利用し、重複課金や無駄な処理を防ぎます。
- **動画キースライドの自動抽出**: 会議や授業の録画動画から、重要なスライド・図表・資料をGeminiの視覚解析で自動抽出。発言内容と統合した見やすい議事録PDFを生成。
- **つなぎ言葉（フィラー）とループの自動除去**: 「えーっと」「あのー」などの不要なつなぎ言葉を自動で取り除き、同じ言葉が連続するループ現象（ハルシネーション）も自動で整理。
- **整理されたPDF出力**: 処理完了後は画面録画、PC音声、要約タイトルが付いたPDFレポートのみを残し、中間画像や解析用作業ファイルを自動削除。

このプロジェクトが役に立ちましたら、ぜひGitHubで Star（⭐）をお願いします！
---


## 🗺️ Roadmap / 今後の予定
- [x] Support export as Markdown / Markdown形式での書き出しサポート
- [ ] Real-time progressive transcription / リアルタイム順次文字起こし表示
- [ ] Support macOS (CoreAudio) / macOSへの対応

---

## Repository rename and compatibility / リポジトリ名の変更と互換性

The repository was renamed from `gemini-voice-transcriber` to `gemini-video-analyzer` because the primary workflow now covers recording, local scene selection, Gemini analysis, transcription, and PDF reporting. Legacy internal names such as `audio_transcriber.py` and `Gemini_CLI_Transcriber` remain unchanged so existing launchers and scripts keep working.

録画・ローカルシーン選別・Gemini解析・文字起こし・PDFレポート生成までを一貫して扱うワークフローへ拡張されたため、リポジトリ名を `gemini-voice-transcriber` から `gemini-video-analyzer` へ変更しました。既存の起動バッチや各種スクリプトとの互換性を保つため、`audio_transcriber.py` や `Gemini_CLI_Transcriber` などの内部名称は変更せず維持されています。

---

## 📝 License / ライセンス
This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
本プロジェクトはMITライセンスのもとで公開されています。詳細は [LICENSE](LICENSE) ファイルをご覧ください。
