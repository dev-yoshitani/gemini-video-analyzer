"""Audio compression and optimization for Gemini transcription and storage.

Converts recording audio to:
- Storage format: M4A (AAC 48 kbps / mono / 16 kHz) ~21.6 MB/h
- Gemini upload format: Ogg (Opus 32 kbps / mono / 16 kHz) ~14.4 MB/h
- Fallback: 16-bit PCM WAV (16 kHz / mono) ~115 MB/h via wave + numpy
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Any

try:
    import numpy as np
except ImportError:
    np = None


def find_ffmpeg() -> str | None:
    """Find ffmpeg executable from env, app local folder, PATH, WinGet, Scoop, or Chocolatey."""
    # 1. Check explicit environment variables
    for env_var in ("FFMPEG_PATH", "FFMPEG_BINARY"):
        val = os.environ.get(env_var, "").strip()
        if val and Path(val).is_file():
            return val

    # 2. Check local app directory (portable placement)
    app_dir = Path(__file__).resolve().parent
    local_candidates = [
        app_dir / "bin" / "ffmpeg.exe",
        app_dir / "tools" / "ffmpeg.exe",
        app_dir / "ffmpeg.exe",
        app_dir / "ffmpeg" / "bin" / "ffmpeg.exe",
    ]
    for candidate in local_candidates:
        if candidate.is_file():
            return str(candidate)

    # 3. Check system PATH
    found = shutil.which("ffmpeg")
    if found:
        return found

    # 4. Check Windows-specific package managers and locations
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        winget_pattern = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
        if winget_pattern.is_dir():
            for exe in winget_pattern.glob("**/ffmpeg.exe"):
                if exe.is_file():
                    return str(exe)

    user_profile = os.environ.get("USERPROFILE", "")
    if user_profile:
        scoop_candidates = [
            Path(user_profile) / "scoop" / "shims" / "ffmpeg.exe",
            Path(user_profile) / "scoop" / "apps" / "ffmpeg" / "current" / "bin" / "ffmpeg.exe",
        ]
        for candidate in scoop_candidates:
            if candidate.is_file():
                return str(candidate)

    choco_path = Path(r"C:\ProgramData\chocolatey\bin\ffmpeg.exe")
    if choco_path.is_file():
        return str(choco_path)

    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    for base in (program_files, program_files_x86):
        if not base:
            continue
        for candidate in [Path(base) / "ffmpeg" / "bin" / "ffmpeg.exe", Path(base) / "ffmpeg" / "ffmpeg.exe"]:
            if candidate.is_file():
                return str(candidate)

    return None


def get_compression_status(language: str = "ja") -> dict[str, Any]:
    """Return status of audio compression support (FFmpeg presence and active mode)."""
    ffmpeg = find_ffmpeg()
    has_ffmpeg = ffmpeg is not None
    is_en = language == "en"
    if has_ffmpeg:
        desc = "Opus 32 kbps / M4A 48 kbps" if is_en else "Opus 32 kbps / M4A 48 kbps（超軽量）"
        engine = f"FFmpeg ({Path(ffmpeg).name})"
    else:
        desc = "16 kHz mono WAV (Fallback)" if is_en else "16 kHz mono WAV（フォールバック）"
        engine = "NumPy / wave"
    return {
        "has_ffmpeg": has_ffmpeg,
        "ffmpeg_path": ffmpeg,
        "mode_description": desc,
        "engine": engine,
    }


def get_wav_duration_and_params(path: Path) -> dict[str, Any] | None:
    """Read WAV parameters using Python standard wave module."""
    try:
        with wave.open(os.fspath(path), "rb") as wf:
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            nframes = wf.getnframes()
            duration = nframes / framerate if framerate > 0 else 0.0
            return {
                "channels": channels,
                "sampwidth": sampwidth,
                "framerate": framerate,
                "nframes": nframes,
                "duration": duration,
            }
    except Exception:
        return None


def get_audio_duration_ffprobe(path: Path) -> float | None:
    """Extract audio duration using ffmpeg/ffprobe stderr."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return None
    try:
        cmd = [ffmpeg, "-i", os.fspath(path)]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace", timeout=10)
        # Match Duration: 00:01:23.45
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", res.stderr)
        if match:
            hours, minutes, seconds = match.groups()
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except Exception:
        pass
    return None


def get_audio_duration(path: Path) -> float | None:
    """Get duration of any supported audio file."""
    if path.suffix.lower() == ".wav":
        info = get_wav_duration_and_params(path)
        if info:
            return info["duration"]
    return get_audio_duration_ffprobe(path)


def verify_audio_integrity(path: Path, expected_duration: float, tolerance: float = 2.0) -> bool:
    """Verify that compressed audio exists, is non-empty, and matches expected duration."""
    if not path.is_file() or path.stat().st_size < 1024:
        return False
    duration = get_audio_duration(path)
    if duration is None:
        return False
    return abs(duration - expected_duration) <= tolerance


def convert_wav_to_16k_mono_numpy(input_wav: Path, output_wav: Path) -> bool:
    """Convert WAV to 16 kHz mono using numpy (zero external dependency)."""
    if np is None:
        return False
    info = get_wav_duration_and_params(input_wav)
    if not info:
        return False

    temp_output = output_wav.with_name(f"{output_wav.stem}.tmp{output_wav.suffix}")
    try:
        with wave.open(os.fspath(input_wav), "rb") as source:
            raw = source.readframes(info["nframes"])

        # Convert to float/int16 array
        channels = info["channels"]
        if info["sampwidth"] == 2:
            data = np.frombuffer(raw, dtype=np.int16)
        elif info["sampwidth"] == 1:
            data = (np.frombuffer(raw, dtype=np.uint8).astype(np.int16) - 128) * 256
        else:
            return False

        # Stereo / multi-channel to mono
        if channels > 1:
            data = data.reshape(-1, channels).mean(axis=1).astype(np.int16)

        # Resample to 16000 Hz if needed
        src_rate = info["framerate"]
        target_rate = 16000
        if src_rate != target_rate and len(data) > 0:
            target_length = int(round(len(data) * target_rate / src_rate))
            indices = np.linspace(0, len(data) - 1, target_length)
            resampled = np.interp(indices, np.arange(len(data)), data).astype(np.int16)
        else:
            resampled = data

        with wave.open(os.fspath(temp_output), "wb") as dest:
            dest.setnchannels(1)
            dest.setsampwidth(2)
            dest.setframerate(target_rate)
            dest.writeframes(resampled.tobytes())

        if verify_audio_integrity(temp_output, info["duration"]):
            os.replace(temp_output, output_wav)
            return True
    except Exception:
        pass
    finally:
        temp_output.unlink(missing_ok=True)
    return False


def compress_to_m4a(input_wav: Path, output_m4a: Path, bitrate: str = "48k", rate: int = 16000) -> bool:
    """Encode WAV to AAC M4A using ffmpeg, verify integrity, then atomic rename."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return False
    info = get_wav_duration_and_params(input_wav)
    expected_duration = info["duration"] if info else get_audio_duration_ffprobe(input_wav)
    if expected_duration is None or expected_duration <= 0.1:
        return False

    temp_output = output_m4a.with_name(f"{output_m4a.stem}.tmp.m4a")
    try:
        cmd = [
            ffmpeg, "-y", "-i", os.fspath(input_wav),
            "-vn",
            "-ac", "1",
            "-ar", str(rate),
            "-c:a", "aac",
            "-b:a", bitrate,
            os.fspath(temp_output),
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        if res.returncode == 0 and verify_audio_integrity(temp_output, expected_duration):
            os.replace(temp_output, output_m4a)
            return True
    except Exception:
        pass
    finally:
        temp_output.unlink(missing_ok=True)
    return False


def compress_to_opus_ogg(input_wav: Path, output_ogg: Path, bitrate: str = "32k", rate: int = 16000) -> bool:
    """Encode WAV to Opus in Ogg container using ffmpeg, verify integrity, then atomic rename."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return False
    info = get_wav_duration_and_params(input_wav)
    expected_duration = info["duration"] if info else get_audio_duration_ffprobe(input_wav)
    if expected_duration is None or expected_duration <= 0.1:
        return False

    temp_output = output_ogg.with_name(f"{output_ogg.stem}.tmp.ogg")
    try:
        cmd = [
            ffmpeg, "-y", "-i", os.fspath(input_wav),
            "-vn",
            "-ac", "1",
            "-ar", str(rate),
            "-c:a", "libopus",
            "-b:a", bitrate,
            os.fspath(temp_output),
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        if res.returncode == 0 and verify_audio_integrity(temp_output, expected_duration):
            os.replace(temp_output, output_ogg)
            return True
    except Exception:
        pass
    finally:
        temp_output.unlink(missing_ok=True)
    return False


def prepare_storage_audio(source_wav: Path, delete_source_on_success: bool = True) -> Path:
    """Create lightweight storage audio (M4A 48kbps or 16k mono WAV).
    
    CRITICAL: Never deletes source_wav unless the compressed file is strictly verified!
    """
    source_wav = Path(source_wav).resolve()
    if not source_wav.is_file():
        return source_wav

    target_m4a = source_wav.with_suffix(".m4a")
    if compress_to_m4a(source_wav, target_m4a, bitrate="48k", rate=16000):
        if delete_source_on_success:
            source_wav.unlink(missing_ok=True)
        return target_m4a

    # Fallback to 16kHz mono WAV via NumPy
    target_mono_wav = source_wav.with_name(f"{source_wav.stem}_mono.wav")
    if convert_wav_to_16k_mono_numpy(source_wav, target_mono_wav):
        if delete_source_on_success:
            source_wav.unlink(missing_ok=True)
            # Replace name back to clean name
            final_wav = source_wav.with_suffix(".wav")
            os.replace(target_mono_wav, final_wav)
            return final_wav
        return target_mono_wav

    # If all compression failed, preserve source WAV untouched
    return source_wav


def prepare_gemini_audio(source_audio: Path, output_dir: Path | None = None) -> tuple[Path, str, bool]:
    """Prepare audio for Gemini upload with priority:
    1. Ogg Opus 32 kbps (MIME: audio/ogg)
    2. M4A AAC 32 kbps (MIME: audio/m4a)
    3. 16k mono WAV (MIME: audio/wav)
    
    Returns: (path, mime_type, is_temp)
    """
    source_audio = Path(source_audio).resolve()
    dest_dir = Path(output_dir).resolve() if output_dir else source_audio.parent
    dest_dir.mkdir(parents=True, exist_ok=True)

    ext = source_audio.suffix.lower()
    # Already in a compressed streaming format
    if ext in (".ogg", ".opus"):
        return source_audio, "audio/ogg", False
    if ext == ".m4a":
        return source_audio, "audio/m4a", False
    if ext == ".mp3":
        return source_audio, "audio/mp3", False

    # For WAV, directly encode to Opus OGG (no double lossy transcoding!)
    target_ogg = dest_dir / f"{source_audio.stem}_gemini.ogg"
    if compress_to_opus_ogg(source_audio, target_ogg, bitrate="32k", rate=16000):
        return target_ogg, "audio/ogg", True

    # Fallback 1: M4A AAC
    target_m4a = dest_dir / f"{source_audio.stem}_gemini.m4a"
    if compress_to_m4a(source_audio, target_m4a, bitrate="32k", rate=16000):
        return target_m4a, "audio/m4a", True

    # Fallback 2: 16k mono WAV
    target_wav = dest_dir / f"{source_audio.stem}_gemini_16k.wav"
    if convert_wav_to_16k_mono_numpy(source_audio, target_wav):
        return target_wav, "audio/wav", True

    return source_audio, "audio/wav", False
