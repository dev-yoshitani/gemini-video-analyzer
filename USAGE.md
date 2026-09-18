# 操作ガイド / Usage

## 日本語

### 基本的な操作手順
1. **初回準備**: 解凍したフォルダで `py -3 -m pip install -r requirements-app.txt` を実行します（Tkinterを含む標準的なWindows版Python 3.10以降が必要です）。
2. **アプリの起動**: `Start.bat` をダブルクリックして操作画面を開きます。
   - **「● 録画する」**: 録画範囲（全画面・モニター・指定範囲）を選び、画面とPC内部音声を記録してAI解析・PDF作成を行います。
   - **「🎙 録音する」**: 画面録画を行わずPC内部音声のみを記録します。「録音だけ保存（完全ローカル・API不要）」または「録音して文字起こし（音声＋PDF作成）」を選択できます。
   - **「動画を選ぶ」**: 保存済みの動画ファイルを選択してAI解析を行います。
3. **一時停止と再開**: 録画・録音中は「一時停止／再開」ボタンで一時停止できます（一時停止中の内容は記録されません）。
4. **終了と解析**: 「録画を終了して解析」を押すと記録を保存します。文字起こしを行う場合はクラウド送信の同意確認を経て解析を開始します（「いいえ」を選んだ場合、録音・録画データはPC内のみに保存されます）。
5. **結果の確認**: 「結果を見る」ボタンから、生成されたPDFレポートや保存先フォルダを直接開きます。

> [!NOTE]
> **APIキーの取り扱い**
> 既存の環境変数または `.env` に設定されたAPIキーを優先して利用します。「録音だけ保存」ではAPIキーは不要です。文字起こし時に未設定で画面から入力したキーは、アプリ起動中のみメモリ内に保持され、設定ファイルや起動引数には保存されません。

### 解析と上限設定

- **文字起こしの仕組み**: 音声は最大60秒ごとに区切って文字起こしされ、完了した区間から順次キャッシュされます。概算の発言時刻と直前の場面要約は、後続の画像解析の文脈情報として活用されます。
- **画像候補の選別**: シーンの視覚的変化と時間間隔のバランスを考慮して候補画像を抽出します。さらに、結論・設定・数値など重要な発言キーワード（日英のヒューリスティック判定）に対応するフレームへ優先的に枚数を配分します。
- **微小な変化の扱い**: 同一内容の重複は除外されますが、数値やテキストが変わった可能性のある候補は保持されます（極めて小さな画面変化の完全な検出を保証するものではありません）。
- **上限値と挙動**: 既定の上限は「画像30枚」「音声120分」です。元音声が上限を超える場合は送信前に停止します。必要に応じて「設定」から上限を引き上げ、未完了一覧から再開できます。
- **API利用の注意**: ここでの上限は画像候補数と元音声の長さに対する設定であり、トークン数やAPI請求額の上限を保証するものではありません。モデルの再試行や翻訳でも追加のAPIリクエストが発生します。
- **プライバシー保護**: 録画中の先行クラウド解析は行いません。録画終了後、ユーザーによる明示的な同意を得てから送信を開始します。

### 中断と復旧

- **安全な中断**: 「処理を中断（保存）」を押すと安全な停止を要求します。実行中のAPI通信やファイルの保存処理が完了するまで数十秒待機する場合があります。
- **途中からの再開**: 「未完了の処理・録画の復旧」一覧から、完了済みの文字起こし区間や画像解析データを再利用して続きから実行できます（古いキャッシュに時刻情報がない場合は、従来の全文文脈が自動適用されます）。
- **録画クラッシュ時の復旧**: 録画中は約60秒ごとに画面と音声の分割ファイルをディスクへ確定しています。アプリやPCが不意に強制終了した場合は、未完了一覧の「録画を復旧」を選択することで、確定済みの区間を結合・復元できます（最後の未確定区間は復旧できない場合があります。PCの電源断に対する完全な耐障害性を保証するものではありません）。
- **ディスク容量**: 通常の録画終了時に分割ファイルを結合します。処理時間と、分割分・結合分の両方を保存できる十分な空き容量が必要です。
- **作業ファイルの整理**: 正常終了時は録画・PC音声・PDFのみを残し、中間解析データを自動削除します。クラッシュ復旧時は安全のため元の分割ファイルを残します。
- **元ファイルの変更**: 保存済みの動画や音声を外部で編集した場合は、古い途中データを再利用せず新規の解析を開始してください。

※旧形式のコンソール画面を利用する場合: `python launcher.py`（日本語） / `python launcher.py --language en`（英語）。

## English

Open `Start_EN.bat` after installing `requirements-app.txt`. A standard Windows Python installation with Tk is required.

- **Record** selects the capture area and records screen + PC audio.
- **Record Audio** records PC playback audio only (no screen capture). Choose either "Save Audio Only" (completely local, no API key needed) or "Transcribe Audio" (audio + PDF report).
- **Pause / Resume** pauses recording; paused content is not recorded.
- **Finish** saves the media, then requests cloud-upload consent for transcription. Declining keeps the recording locally.
- **Choose video** analyzes an existing file. **View results** opens the PDF or audio folder.

- **Settings** controls the language, recording folder, image limit (default 30) and source-audio limit (default 120 minutes). Over-limit audio stops before upload; increase the limit and resume if desired.
- Existing environment/`.env` API keys are used. Keys entered in the GUI are held only for that app session, not stored in settings or command-line arguments.

Audio is transcribed in chunks of up to 60 seconds. Completed chunks are cached for retries. Approximate utterance timestamps and the previous scene summary provide local context for each image. Candidate selection balances scene changes across the timeline and reserves some image capacity for novel conclusions, settings and numbers in speech. These speech cues use Japanese/English keyword heuristics, not full semantic topic detection. Small visual changes may still be missed.

The limits govern unique candidate images and source-audio duration, not token usage, request attempts or the final API bill. Model retries and translation can make additional API calls. Cloud analysis starts only after recording and explicit consent; concurrent cloud analysis during recording is not enabled.

**Stop safely** saves completed work; an active network call or file finalization may need to finish. Resume from the unfinished-job list. Older cached transcripts without timestamps retain the legacy full-transcript context.

Recording chunks are finalized about every 60 seconds. After a crash, **Recover** uses finalized chunks only; the last unfinished interval may be lost. This is not a guarantee against power failure. Final recording assembly requires time and disk space for both chunks and merged media. Normal success keeps recording, PC audio and PDF only. Crash recovery retains original chunks for safety. If source media changed, start a new analysis.

Console fallback: `python launcher.py --language en`.
