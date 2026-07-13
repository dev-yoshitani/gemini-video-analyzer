# 🎙️ Gemini Video Analyzer（動画解析・文字起こしツール）

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Gemini](https://img.shields.io/badge/AI-Gemini%203.5-blueviolet.svg)](https://aistudio.google.com/)

A Windows desktop application that records or analyzes video, selects meaningful scene changes locally, and uses the Gemini API for transcription and analysis. It outputs editable Markdown and structured PDF reports.

Windowsで動作する、録画・動画解析・文字起こしツールです。PC内で重要な場面候補を選別し、Gemini APIを活用して編集しやすいMarkdownと整形済みPDFを自動生成します。

---

## Language / 言語
- [English (README_EN)](#english)
- [日本語 (README_JA)](#日本語)

---

## English

## 📥 Download and install

[![Download ZIP](https://img.shields.io/badge/Download-Latest%20ZIP-green?style=for-the-badge&logo=github)](https://github.com/yoshitani-dev/Gemini-Voice-Transcriber/releases/latest/download/Gemini-Video-Analyzer-Windows.zip)

Download the ZIP, extract it, and install the included dependencies once with Python 3.10 or later.

### ✨ Features
- **Real-time System Audio Recording**: Captures computer internal audio using WASAPI loopback.
- **High-accuracy AI Transcription**: Powered by Google's Gemini API (`gemini-3.5-flash`, with automatic fallback to `gemini-2.5-flash` and `gemini-3.1-flash-lite` when rate-limited or busy).
- **🆕 Video Key Slide Extraction**: Automatically extracts important slides, charts, and documents from recorded videos using Gemini's vision capabilities.
- **Filler Word Removal**: Automatically strips out filler words (e.g., "uhm", "uh", "like") and resolves hallucinated repetitions.
- **Markdown & PDF Export**: Saves every transcription as an editable Markdown file and a formatted PDF with an AI-generated title.

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

Double-click **Start.bat**. It offers these choices:

1. Record and create a high-accuracy AI analysis report.
2. Analyze an existing video.
3. Resume an interrupted high-accuracy analysis.
4. Open the legacy audio transcription tool.

You can also drag a video file onto **Start.bat** to analyze it. Before recording, choose the full desktop, one monitor, or a mouse-selected area.

> [!WARNING]
> **Screen Recording Mode Privacy Notice**
> - Only the selected recording area is recorded. Notifications or personal chats within that area can still be captured.
> - The raw video is kept on your PC. Audio and locally selected scene-candidate images are sent to the Gemini API only after confirmation.
> - Please ensure you hide sensitive information before starting.
> - Ensure you have permission to record the meeting.

The legacy audio-only workflow is available as option 4 inside `Start.bat`.

If this project is useful, please consider giving it a star ⭐
---



## 日本語

## 📥 ダウンロードと初回準備

[![ZIPファイルをダウンロード](https://img.shields.io/badge/ダウンロード-最新版ZIP-green?style=for-the-badge&logo=github)](https://github.com/yoshitani-dev/Gemini-Voice-Transcriber/releases/latest/download/Gemini-Video-Analyzer-Windows.zip)

ZIPを解凍後、最初の一回だけPython 3.10以降で必要なライブラリを入れます。

### ✨ 主な機能
- **PCシステム音声録音**: WASAPIループバックを使用し、会議や動画の音声をクリアに直接録音。
- **高精度AI文字起こし**: Googleの `gemini-3.5-flash` を使用（制限・混雑時は `gemini-2.5-flash`、さらに `gemini-3.1-flash-lite` へ自動切り替え）。
- **🆕 動画キースライド抽出**: 会議や授業の録画動画から、重要なスライド・チャート・資料をGeminiのAI解析で自動抽出。文字起こしと統合したリッチ議事録を生成。
- **つなぎ言葉（フィラー）の自動除去**: 「えーっと」「あのー」などを自動で取り除き、同じ言葉が連続するループ現象（ハルシネーション）も自動で除去。
- **Markdown・PDF自動出力**: 文字起こし結果を、AIが最適なタイトルを付けた編集しやすいMarkdownとフォーマット済みPDFの両方で出力。

### 🎯 高精度AI解析 + PDF（推奨）

`Start.bat` の「高精度AI解析」では、録画または保存済み動画からPC内でシーン候補と音声を取り出し、`Gemini 3.5 Flash` で文字起こしと画像内容の解析を行います。制限・混雑時は `Gemini 2.5 Flash`、さらに `Gemini 3.1 Flash-Lite` へ自動で切り替わります。元動画全体は送信せず、音声と候補画像だけを確認後に送信します。

出力PDFには画像そのものを埋め込まず、各場面の時刻・重要度・画像解析の説明・画像内で検出した主要テキストと、文字起こし全文をまとめます。抽出画像は確認用として別フォルダに残ります。

初回だけ、アプリに必要なライブラリをインストールします：

```powershell
py -3 -m pip install -r requirements-app.txt
```

普段はメイン起動ファイルの `Start.bat` だけを使います。録画開始前に「すべての画面」「モニターを1台選ぶ」「マウスで範囲指定」から録画範囲を選び、Enterキーで開始・停止します。その後の解析とPDF生成まで自動で進みます。既存動画を `Start.bat` にドラッグ＆ドロップした場合は、高精度AI解析の送信確認画面が開きます。

高精度AI解析の途中でAPI制限、通信エラー、アプリ終了などが発生した場合は、シーン候補・文字起こし・画像ごとの解析結果が自動保存されます。次回 `Start.bat` の「未完了の高精度AI解析を途中から再開」を選ぶと、完了済みの処理を再利用し、残りから続行します。

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

## 📝 License / ライセンス
This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
本プロジェクトはMITライセンスのもとで公開されています。詳細は [LICENSE](LICENSE) ファイルをご覧ください。
