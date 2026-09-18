"""Comprehensive tests for audio compression, integrity verification and Gemini optimization."""
import os
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

import audio_compression as comp


def _generate_test_wav(path: Path, duration_sec: float = 3.0, rate: int = 48000, channels: int = 2) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        nframes = int(duration_sec * rate)
        # Stereo 16-bit samples
        data = b"\x10\x00" * (nframes * channels)
        wf.writeframes(data)
    return path


class AudioCompressionEstimationTests(unittest.TestCase):
    def test_estimated_sizes_match_target_bitrates(self):
        one_hour_seconds = 3600

        # 48 kHz stereo 16-bit WAV
        raw_wav_bytes = 48000 * 2 * 2 * one_hour_seconds
        raw_wav_mb = raw_wav_bytes / (1024 * 1024)
        self.assertAlmostEqual(raw_wav_mb, 659.18, delta=5.0)

        # 16 kHz mono 16-bit WAV
        mono_wav_bytes = 16000 * 1 * 2 * one_hour_seconds
        mono_wav_mb = mono_wav_bytes / (1024 * 1024)
        self.assertAlmostEqual(mono_wav_mb, 109.86, delta=2.0)

        # Opus 32 kbps (target: ~14.4 MB / hour)
        opus_32k_bytes = (32 * 1000 / 8) * one_hour_seconds
        opus_32k_mb = opus_32k_bytes / (1024 * 1024)
        self.assertAlmostEqual(opus_32k_mb, 13.73, delta=1.5)

        # M4A 48 kbps (target: ~21.6 MB / hour)
        m4a_48k_bytes = (48 * 1000 / 8) * one_hour_seconds
        m4a_48k_mb = m4a_48k_bytes / (1024 * 1024)
        self.assertAlmostEqual(m4a_48k_mb, 20.60, delta=2.0)

        # Reduction ratios
        self.assertGreater(raw_wav_bytes / opus_32k_bytes, 40)  # ~1/48
        self.assertGreater(raw_wav_bytes / m4a_48k_bytes, 25)   # ~1/32


class NumpyFallbackConversionTests(unittest.TestCase):
    def test_stereo_48k_to_mono_16k_conversion(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            temp_path = Path(temp_dir)
            source_wav = _generate_test_wav(temp_path / "stereo48k.wav", duration_sec=2.0, rate=48000, channels=2)
            dest_wav = temp_path / "mono16k.wav"

            ok = comp.convert_wav_to_16k_mono_numpy(source_wav, dest_wav)
            self.assertTrue(ok)
            self.assertTrue(dest_wav.is_file())

            info = comp.get_wav_duration_and_params(dest_wav)
            self.assertIsNotNone(info)
            self.assertEqual(info["channels"], 1)
            self.assertEqual(info["framerate"], 16000)
            self.assertEqual(info["sampwidth"], 2)
            self.assertAlmostEqual(info["duration"], 2.0, delta=0.05)

            # File size should be roughly 1/6th of the original
            self.assertLess(dest_wav.stat().st_size, source_wav.stat().st_size * 0.25)


class IntegrityAndSafetyTests(unittest.TestCase):
    def test_verify_audio_integrity_rejects_empty_or_broken_file(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            temp_path = Path(temp_dir)
            empty_file = temp_path / "empty.wav"
            empty_file.write_bytes(b"")
            self.assertFalse(comp.verify_audio_integrity(empty_file, expected_duration=10.0))

            tiny_file = temp_path / "tiny.wav"
            tiny_file.write_bytes(b"RIFF" + b"\x00" * 20)
            self.assertFalse(comp.verify_audio_integrity(tiny_file, expected_duration=10.0))

    def test_prepare_storage_audio_never_deletes_source_on_failure(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            temp_path = Path(temp_dir)
            source_wav = _generate_test_wav(temp_path / "protected.wav", duration_sec=1.5)

            with (
                mock.patch.object(comp, "compress_to_m4a", return_value=False),
                mock.patch.object(comp, "convert_wav_to_16k_mono_numpy", return_value=False),
            ):
                result = comp.prepare_storage_audio(source_wav, delete_source_on_success=True)

            # Crucial requirement: source WAV is PRESERVED when compression fails
            self.assertTrue(source_wav.is_file())
            self.assertEqual(result, source_wav)

    def test_prepare_storage_audio_deletes_source_only_on_verified_success(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            temp_path = Path(temp_dir)
            source_wav = _generate_test_wav(temp_path / "test.wav", duration_sec=1.5)

            def fake_m4a_success(in_path, out_path, **kwargs):
                out_path.write_bytes(b"fake-verified-m4a" + b"\x00" * 2048)
                return True

            with mock.patch.object(comp, "compress_to_m4a", side_effect=fake_m4a_success):
                result = comp.prepare_storage_audio(source_wav, delete_source_on_success=True)

            self.assertTrue(result.name.endswith(".m4a"))
            self.assertTrue(result.is_file())
            self.assertFalse(source_wav.exists())


class GeminiMimeTypeAndOptimizationTests(unittest.TestCase):
    def test_prepare_gemini_audio_mime_types(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            temp_path = Path(temp_dir)
            source_wav = _generate_test_wav(temp_path / "meeting.wav", duration_sec=2.0)

            # 1. Opus Ogg -> audio/ogg
            def fake_opus(in_path, out_path, **kwargs):
                out_path.write_bytes(b"OggS" + b"\x00" * 2048)
                return True

            with mock.patch.object(comp, "compress_to_opus_ogg", side_effect=fake_opus):
                path, mime, is_temp = comp.prepare_gemini_audio(source_wav)
                self.assertEqual(mime, "audio/ogg")
                self.assertTrue(path.name.endswith(".ogg"))
                self.assertTrue(is_temp)

            # 2. Fallback to M4A -> audio/m4a
            def fake_m4a(in_path, out_path, **kwargs):
                out_path.write_bytes(b"ftypM4A " + b"\x00" * 2048)
                return True

            with (
                mock.patch.object(comp, "compress_to_opus_ogg", return_value=False),
                mock.patch.object(comp, "compress_to_m4a", side_effect=fake_m4a),
            ):
                path, mime, is_temp = comp.prepare_gemini_audio(source_wav)
                self.assertEqual(mime, "audio/m4a")
                self.assertTrue(path.name.endswith(".m4a"))
                self.assertTrue(is_temp)

            # 3. Fallback to 16k WAV -> audio/wav
            with (
                mock.patch.object(comp, "compress_to_opus_ogg", return_value=False),
                mock.patch.object(comp, "compress_to_m4a", return_value=False),
            ):
                path, mime, is_temp = comp.prepare_gemini_audio(source_wav)
                self.assertEqual(mime, "audio/wav")


class FFmpegRealEncodingTests(unittest.TestCase):
    def test_ffmpeg_encodes_m4a_and_opus_when_available(self):
        ffmpeg = comp.find_ffmpeg()
        if not ffmpeg:
            self.skipTest("FFmpeg not available on this machine")

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            temp_path = Path(temp_dir)
            source_wav = _generate_test_wav(temp_path / "real_speech.wav", duration_sec=3.0, rate=48000, channels=2)
            source_size = source_wav.stat().st_size

            # Test M4A 48k mono
            m4a_path = temp_path / "real_speech.m4a"
            m4a_ok = comp.compress_to_m4a(source_wav, m4a_path, bitrate="48k", rate=16000)
            self.assertTrue(m4a_ok)
            self.assertTrue(m4a_path.is_file())
            # Size reduction check (> 80% reduction)
            self.assertLess(m4a_path.stat().st_size, source_size * 0.2)

            # Test Opus Ogg 32k mono
            ogg_path = temp_path / "real_speech.ogg"
            ogg_ok = comp.compress_to_opus_ogg(source_wav, ogg_path, bitrate="32k", rate=16000)
            self.assertTrue(ogg_ok)
            self.assertTrue(ogg_path.is_file())
            # Size reduction check (> 85% reduction)
            self.assertLess(ogg_path.stat().st_size, source_size * 0.15)
