# Gemini Video Analyzer - User Manual

This is the guide for the current release. The application records or opens a video, chooses scene candidates locally, and uses Gemini to create a transcript and text-based scene analysis report.

> [!IMPORTANT]
> This is not a fully local workflow. The original video stays on your PC, but the selected scene-candidate images and audio are sent to the Gemini API after you confirm the upload.

---

## 1. One-time setup

You need Windows 10/11, Python 3.10 or later, and a Gemini API key. API availability, quotas, and charges depend on your Google account and plan.

1. Download the ZIP from GitHub Releases and extract it to a folder of your choice.
2. Open PowerShell in that folder and run:

```powershell
py -3 -m pip install -r requirements-app.txt
```

3. Create a Gemini API key in [Google AI Studio](https://aistudio.google.com/apikey).
4. The first time you launch `Start_EN.bat`, enter the key when prompted. Never share your key with anyone else.

> [!NOTE]
> If the `py` command is not found, install Python 3.10 or later, enable “Add Python to PATH” in the installer, and try again.

---

## 2. Everyday use

Double-click `Start_EN.bat` and select an item from the English menu. `Start.bat` remains available for the Japanese workflow.

| Number | Action |
|---|---|
| 1 | Record and create an English AI analysis PDF |
| 2 | Analyze a saved video and create an English AI analysis PDF |
| 3 | Resume an interrupted English analysis |

You can also drag a video file onto `Start_EN.bat` to start high-accuracy analysis of that video.

### Record and analyze

1. Select `1` from the menu.
2. Choose the whole desktop, one monitor, or a mouse-selected area.
3. Start playing a video or music on the PC, then press Enter. Recording starts only after a three-second system-audio test detects sound.
4. Press Enter again when you want to stop recording.
5. The app prepares scene candidates and audio locally, then displays a confirmation before anything is sent to Gemini.

The English workflow produces an English transcript. English speech is transcribed directly; other spoken languages are translated into natural English. Frame explanations and detected on-screen text are also written in English.

Before recording, make sure notifications, private chats, passwords, and other sensitive information are not visible in the chosen area. Obtain any necessary consent before recording a meeting.

### Analyze a saved video

1. Select `2`, or drag the video onto `Start_EN.bat`.
2. Choose the video and review the upload confirmation.
3. Wait for completion. If processing stops, use item `3` on the next launch to resume it.

---

## 3. What is sent to Gemini

- The complete original video is never uploaded to Gemini.
- The app sends locally selected scene-candidate images and audio for transcription after confirmation.
- Gemini 3.5 Flash is the default. If it is rate-limited or busy, the app automatically falls back to Gemini 2.5 Flash and then Gemini 3.1 Flash-Lite.
- For temporary API limits or congestion, the app waits 15, 30, 60, and 120 seconds before retrying automatically.
- After success, only the recording, PC audio, and PDF remain; intermediate files are deleted automatically.

---

## 4. Output files

For recordings, files are saved under `output/local_recordings/Recording_datetime/`. For a saved video, the app creates `video-name_Analysis_Results_datetime/` next to the source video.

| File or folder | Purpose |
|---|---|
| `Screen Recording.mp4` or `Screen Recording.avi` | Original video captured by the recording mode |
| `PC Audio.wav` | PC audio captured by the recording mode |
| `title_Analysis_Report.pdf` | English PDF containing scene timestamps, importance, descriptions, translated detected text, and the transcript |

The PDF does not embed the scene images. It contains their timestamps, importance, analysis results, and important detected text in a readable report. Extracted images, transcript text, Markdown, JSON, temporary analysis audio, and resume data are removed after the PDF is created successfully.

---

## 5. Resume interrupted work

The app first waits and retries automatically for temporary rate limits or congestion. If processing still cannot complete, or a network error or app close interrupts it, completed scene extraction, transcription, and image analysis are saved temporarily.

1. Open `Start_EN.bat`.
2. Select `3`.
3. Choose the video analysis to resume.

---

## 6. Troubleshooting

### “No module named ...”

Run the one-time setup command in the extracted folder:

```powershell
py -3 -m pip install -r requirements-app.txt
```

### API key is not configured

Launch `Start_EN.bat` and paste your own Gemini API key when prompted. If you accidentally expose a key, revoke it in Google AI Studio and create a new one.

### Analysis stops partway through

Check your network and Gemini quota, then resume from menu item `3`. If rate limits continue after the automatic model fallback, wait and try again later.

### PC audio cannot be recorded

Play a video or music during the three-second test before recording. In Windows Sound settings, confirm that your active speakers or headphones are selected. If you have just changed Bluetooth devices, close the app and open it again. If the real recording stream cannot start after the test, screen recording is also canceled automatically.

---

## 7. Sharing the app

You can share the folder as a ZIP. Before you do, delete recordings, audio, and reports from `output`, and exclude `.env` if it exists. Each user must create and enter their own Gemini API key.
