"""Build and verify allowlisted Japanese/English ZIPs without local user data."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import zipfile


COMMON_FILES = (
    "desktop_app.py", "workflow_control.py", "timeline_analysis.py", "recording_recovery.py",
    "launcher.py", "local_screen_recorder.py", "local_video_analyzer.py",
    "gemini_hybrid_analyzer.py", "gemini_retry.py", "audio_transcriber.py",
    "key_slide_extractor.py", "requirements.txt", "requirements-local.txt",
    "requirements-app.txt", "README.md", "USAGE.md", "LICENSE", ".env.example",
)
VARIANTS = {"Japanese": "Start.bat", "English": "Start_EN.bat"}


def build_packages(root: Path, output: Path) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    archives = []
    for language, launcher in VARIANTS.items():
        names = sorted((*COMMON_FILES, launcher))
        archive = output / f"Gemini-Video-Analyzer-Windows-{language}.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
            for name in names:
                data = (root / name).read_bytes()
                info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                package.writestr(info, data)
        with zipfile.ZipFile(archive) as package:
            if package.namelist() != names or package.testzip() is not None:
                raise RuntimeError(f"Invalid ZIP: {archive.name}")
            for name in names:
                if package.read(name) != (root / name).read_bytes():
                    raise RuntimeError(f"Archive content mismatch: {name}")
            if b"GEMINI_API_KEY=your_api_key_here" not in package.read(".env.example"):
                raise RuntimeError("Unexpected API key template")
        archives.append(archive)
    checksums = "".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in archives)
    (output / "SHA256SUMS.txt").write_text(checksums, encoding="ascii")
    return archives


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("dist"))
    args = parser.parse_args()
    for path in build_packages(Path(__file__).resolve().parents[1], args.output):
        print(f"Verified: {path.name} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
