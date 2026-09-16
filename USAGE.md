# 操作ガイド / Usage

## 日本語

1. 初回は従来どおり `pip install -r requirements-app.txt` を実行します。Tkを含む通常のWindows版Pythonが必要です。
2. `Start.bat` を開きます。録画する場合はPCで音声を再生してから「録画する」を押し、画面範囲を選びます。
3. 「一時停止／再開」は画面と音声を一緒に停止・再開します。一時停止中の内容は記録されません。
4. 「録画を終了して解析」を押します。音声・候補画像のクラウド送信を確認してから解析します。「いいえ」の場合、録画はPCに残ります。
5. 「結果を見る」でPDFを開きます。画像を含まない従来のPDF構成は変更していません。

APIキーは既存の環境変数／`.env`を利用します。未設定時に画面から入力したキーは、その起動中だけメモリに保持します。設定ファイルや起動引数には保存しません。

### 解析と上限

- 音声は最大60秒ごとに文字起こしし、完了した区間を保存します。時刻付きの発言と前の場面の要約を、対応する画像解析の文脈に使います。AIが返す発言時刻は概算です。
- 画像の候補は、場面変化と時間的な偏りを考慮して選びます。上限の一部を、結論・設定・数値などの新しい発言に対応する画像へ割り当てます。発言の手掛かりは日英のキーワードによる補助判定であり、完全な話題理解ではありません。
- 同じ内容の繰り返しを除外しますが、数値や説明が変わった可能性のある候補は残します。すべての微小な画面変更を検出する保証はありません。
- 設定の既定値は画像30枚、音声120分です。音声が上限を超える場合は送信せず停止します。設定を増やして未完了一覧から再開できます。
- 上限は画像候補数と元音声の時間です。APIの試行回数・トークン数・請求額の上限保証ではありません。モデルの再試行や翻訳処理でもAPIを使用します。
- 録画中の先行クラウド解析は行いません。録画終了後、明示的な確認を経て送信します。

### 中断・復旧

- 「処理を中断（保存）」で安全な中断を要求します。実行中のAPI通信やファイル保存が終わるまで待つ場合があります。
- 未完了一覧から、完了済みの文字起こし区間・画像解析を再利用できます。古い版の途中データに時刻情報がない場合は、従来の全文文脈を使います。
- 録画中は内部で約60秒ごとに画面と音声のファイルを確定します。強制終了した場合は、未完了一覧の「録画を復旧」を選びます。最後の未確定区間（最大約60秒）は失われる場合があります。PCの電源断に対する完全な保証ではありません。
- 通常の録画終了時に分割ファイルを結合します。処理時間と、分割分・結合分の両方を保存する空き容量が必要です。
- 正常終了後は録画・PC音声・PDFを残し、中間解析データを削除します。強制終了からの復旧では、保護のため元の分割ファイルを残します。
- 保存済みの動画・音声を変更した場合は、古い途中データを再利用せず新しい解析を開始してください。

旧操作画面: `python launcher.py` / 英語: `python launcher.py --language en`。

## English

Open `Start_EN.bat` after installing `requirements-app.txt`. A standard Windows Python installation with Tk is required.

- **Record** selects the capture area and runs a three-second PC-audio check. Play audio first. **Pause / Resume** pauses both video and audio; paused content is not recorded.
- **Finish recording & analyze** saves the media, then requests cloud-upload consent. Declining keeps the recording locally.
- **Choose video** analyzes an existing file. **View results** opens the PDF. The existing text-only PDF layout is unchanged.
- **Settings** controls the language, recording folder, image limit (default 30) and source-audio limit (default 120 minutes). Over-limit audio stops before upload; increase the limit and resume if desired.
- Existing environment/`.env` API keys are used. Keys entered in the GUI are held only for that app session, not stored in settings or command-line arguments.

Audio is transcribed in chunks of up to 60 seconds. Completed chunks are cached for retries. Approximate utterance timestamps and the previous scene summary provide local context for each image. Candidate selection balances scene changes across the timeline and reserves some image capacity for novel conclusions, settings and numbers in speech. These speech cues use Japanese/English keyword heuristics, not full semantic topic detection. Small visual changes may still be missed.

The limits govern unique candidate images and source-audio duration, not token usage, request attempts or the final API bill. Model retries and translation can make additional API calls. Cloud analysis starts only after recording and explicit consent; concurrent cloud analysis during recording is not enabled.

**Stop safely** saves completed work; an active network call or file finalization may need to finish. Resume from the unfinished-job list. Older cached transcripts without timestamps retain the legacy full-transcript context.

Recording chunks are finalized about every 60 seconds. After a crash, **Recover** uses finalized chunks only; the last unfinished interval may be lost. This is not a guarantee against power failure. Final recording assembly requires time and disk space for both chunks and merged media. Normal success keeps recording, PC audio and PDF only. Crash recovery retains original chunks for safety. If source media changed, start a new analysis.

Console fallback: `python launcher.py --language en`.
