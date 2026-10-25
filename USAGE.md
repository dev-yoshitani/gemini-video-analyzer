# 操作ガイド / Usage

## 日本語

### 基本的な操作手順
1. **初回準備**: 解凍したフォルダで `py -3 -m pip install -r requirements-app.txt` を実行します（Tkinterを含む標準的なWindows版Python 3.10以降が必要です）。
2. **アプリの起動**: `Start.bat` をダブルクリックして操作画面を開きます。
   - **「● 録画する」**: 録画範囲（全画面・モニター・指定範囲）を選び、画面とPC内部音声を記録してAI解析・PDF作成を行います。
   - **「録音して文字起こし」**: PCで再生される音声だけを録音します。画面やマイクの記録、画像解析は行いません。確認後、事前テストなしで録音が始まります。録音中にPCで音声を再生してください。
   - **「動画を選ぶ」**: 保存済みの動画ファイルを選択してAI解析を行います。
   - **「音声を選ぶ」**: 保存済みのWAV・MP3・M4Aなどを文字起こしします。音声を `Start.bat` にドラッグ＆ドロップしても起動できます。元音声は変更・削除しません。
3. **一時停止と再開**: 録画・録音中は「一時停止／再開」ボタンで一時停止できます（一時停止中の内容は記録されません）。
4. **終了と解析**: 録音の場合は「録音を終了して文字起こし」、録画の場合は「録画を終了して解析」を押します。記録を保存してからクラウド送信の確認を表示します。「はい」で文字起こしを開始し、「いいえ」では記録をPC内に残します。録音の場合、送信するのは音声だけです。
5. **結果の確認**: 「結果を見る」ボタンから、生成されたPDFレポートや保存先フォルダを直接開きます。

> [!NOTE]
> **APIキーの取り扱い**
> 既存の環境変数または `.env` に設定されたAPIキーを優先して利用します。未設定で画面から入力したキーは、アプリ起動中のみメモリ内に保持され、設定ファイルや起動引数には保存されません。

録音と文字起こしが完了すると、保存先の `録音_<日時>/` に音声（M4AまたはWAV）と、`文字起こし結果/` 内に `<タイトル>_文字起こしレポート.pdf` が残ります。中間データは正常終了後に削除します。送信のキャンセルや文字起こし・PDF生成の失敗時は、未完了一覧から再開できます。

保存済み音声を取り込んだ場合は、元音声の横に `<元ファイル名>_文字起こし結果/` を作成してPDFを保存します。音声は取り込み元にそのまま残します。

### 文字起こし中に次を録音する

1. 現在の録音を終了し、保存が完了するまで待ちます。
2. Geminiへの送信を確認します。
3. 「録音して文字起こし」を押し、次の録音を開始します。
4. 前の処理は「バックグラウンド解析」一覧で進捗を確認します。「選択した結果を開く」で結果を開き、「選択した解析を中断」で個別に中断できます。

録音は同時に1件だけ行います。前の処理の完了やエラーで、新しく録音中の停止ボタンが無効になることはありません。新しい録音の送信にも、別途確認が必要です。

### 解析と上限設定

- **文字起こしの仕組み**: 音声全体をまとめて送信し、1分ごとのAPIリクエストには分割しません。音声のみの処理は通常の文章を受け取り、動画解析では概算の発言時刻を画像解析の文脈情報として活用します。
- **録音時の品質と容量**: 新しい録音は、デバイスの取り込み設定を保ったまま最大16kHz・モノラル・16bitで保存します。48kHz・ステレオのPCM音声と比べて容量は約6分の1です。既存の録音は変更しません。
- **長文の出力上限**: 文字起こしには長文向けの出力枠を設定していますが、API自体の上限は残ります。音声のみの処理で上限に達した場合、取得できた文章は「未完了の文字起こし」で始まるテキストファイルへ保存します。全文ではないため、完成したPDFとしては扱いません。再開時は続きだけでなく音声全体を再処理する場合があります。
- **画像候補の選別**: シーンの視覚的変化と時間間隔のバランスを考慮して候補画像を抽出します。さらに、結論・設定・数値など重要な発言キーワード（日英のヒューリスティック判定）に対応するフレームへ優先的に枚数を配分します。
- **微小な変化の扱い**: 同一内容の重複は除外されますが、数値やテキストが変わった可能性のある候補は保持されます（極めて小さな画面変化の完全な検出を保証するものではありません）。
- **上限値と挙動**: 既定の上限は「画像30枚」「音声120分」です。元音声が上限を超える場合は送信前に停止します。必要に応じて「設定」から上限を引き上げ、未完了一覧から再開できます。
- **API利用の注意**: ここでの上限は画像候補数と元音声の長さに対する設定であり、トークン数やAPI請求額の上限を保証するものではありません。モデルの再試行や翻訳でも追加のAPIリクエストが発生します。
- **プライバシー保護**: 現在録音・録画中のデータは先行送信しません。保存後、ユーザーの明示的な同意を得て送信します。保存・確認済みの前の記録は、次の録音中もバックグラウンドで解析できます。

### 中断と復旧

- **安全な中断**: 「処理を中断（保存）」を押すと安全な停止を要求します。実行中のAPI通信やファイルの保存処理が完了するまで数十秒待機する場合があります。
- **途中からの再開**: 「未完了の処理・録画の復旧」一覧から、完了済みの文字起こしや画像解析データを再利用して実行できます。PDF作成だけが失敗した場合は、保存済みの文字起こしを再利用します。文字起こし自体の失敗時は音声全体を再処理する場合があります。
- **録画クラッシュ時の復旧**: 録画中は約60秒ごとに画面と音声の分割ファイルをディスクへ確定しています。アプリやPCが不意に強制終了した場合は、未完了一覧の「録画を復旧」を選択することで、確定済みの区間を結合・復元できます（最後の未確定区間は復旧できない場合があります。PCの電源断に対する完全な耐障害性を保証するものではありません）。
- **ディスク容量**: 通常の録画終了時に分割ファイルを結合します。処理時間と、分割分・結合分の両方を保存できる十分な空き容量が必要です。
- **作業ファイルの整理**: 正常終了時は録画・PC音声・PDFのみを残し、中間解析データを自動削除します。クラッシュ復旧時は安全のため元の分割ファイルを残します。
- **元ファイルの変更**: 保存済みの動画や音声を外部で編集した場合は、古い途中データを再利用せず新規の解析を開始してください。

※旧形式のコンソール画面を利用する場合: `python launcher.py`（日本語） / `python launcher.py --language en`（英語）。

## English

Open `Start_EN.bat` after installing `requirements-app.txt`. A standard Windows Python installation with Tk is required.

- **Record** selects the capture area and records screen + PC audio.
- **Record audio & transcribe** records PC playback audio only, not the screen or microphone. Recording starts after confirmation, without a pre-recording audio test. Play PC audio while recording. No image analysis runs.
- **Pause / Resume** pauses recording; paused content is not recorded.
- **Finish audio & transcribe** saves audio, then requests Gemini upload consent. Only audio is uploaded. Declining keeps audio locally and allows resuming later. Success keeps audio and a transcript PDF; intermediate files are removed. Transcription or PDF failures can resume without re-recording.
- **Choose video** analyzes an existing file. **View results** opens the PDF or audio folder.
- **Choose audio** transcribes an existing WAV, MP3, M4A or another supported audio file. Dropping it onto `Start_EN.bat` also works. The original is never modified or deleted; a source-specific results folder is created next to it.
- **Background analysis** lets you start the next recording after the previous recording has finished saving. Each upload requires consent. Only one recording runs at a time; progress, results and individual cancellation are available in the job list.
- **Recording quality** is up to 16 kHz, mono, 16-bit, while capture uses the playback device's native format. Stored PCM data is about one-sixth the size of 48 kHz stereo. Existing recordings are unchanged.

- **Settings** controls the language, recording folder, image limit (default 30) and source-audio limit (default 120 minutes). Over-limit audio stops before upload; increase the limit and resume if desired.
- Existing environment/`.env` API keys are used. Keys entered in the GUI are held only for that app session, not stored in settings or command-line arguments.

Audio is sent as one whole recording, not split into one-minute API calls. Audio-only jobs request plain text instead of timestamp JSON; video analysis uses approximate speech times and the previous scene summary as local context for images. A larger output budget is set for transcription, but provider limits still apply. Output-limited plain text is saved in an `Incomplete_Transcript_*.txt` file and is not published as a completed PDF. Retrying may require the whole audio again, not just the remaining speech.

Candidate selection balances scene changes across the timeline and reserves some image capacity for novel conclusions, settings and numbers in speech. These speech cues use Japanese/English keyword heuristics, not full semantic topic detection. Small visual changes may still be missed.

The limits govern unique candidate images and source-audio duration, not token usage, request attempts or the final API bill. Model retries and translation can make additional API calls. The current recording is not uploaded until it has been saved and consent granted. A previous saved and approved job can continue transcribing while the next recording is running.

**Stop safely** saves completed work; an active network call or file finalization may need to finish. Resume from the unfinished-job list. Completed transcripts and image analyses are reused; a PDF-only failure does not require another transcription request.

Recording chunks are finalized about every 60 seconds. After a crash, **Recover** uses finalized chunks only; the last unfinished interval may be lost. This is not a guarantee against power failure. Final recording assembly requires time and disk space for both chunks and merged media. Normal success keeps recording, PC audio and PDF only. Crash recovery retains original chunks for safety. If source media changed, start a new analysis.

Console fallback: `python launcher.py --language en`.
