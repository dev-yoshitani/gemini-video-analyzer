#!/usr/bin/env python3
"""Offline public-repository contract guard.

Standard-library only. Intended to run before publication and in GitHub Actions.
It checks repository structure, local Markdown links, obvious credential patterns,
personal absolute paths, and optional Codex Skill / fork attribution contracts.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

TEXT_EXTENSIONS = {
    ".md", ".txt", ".py", ".toml", ".yaml", ".yml", ".json", ".ini",
    ".cfg", ".ps1", ".bat", ".sh", ".spec", ".csv",
}
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "dist", "build", "__pycache__"}

SECRET_PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    "aws_access_key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "openai_style_key": re.compile(r"\bsk-[A-Za-z0-9_-]{24,}\b"),
}

# Real local paths are risky. Placeholder forms such as C:\\Users\\<name> are allowed.
LOCAL_PATH_PATTERNS = {
    "windows_user_path": re.compile(r"(?i)\b[A-Z]:\\Users\\(?![<%{])[A-Za-z0-9._ -]+\\"),
    "mac_user_path": re.compile(r"/Users/(?![<${])[A-Za-z0-9._-]+/"),
    "linux_user_path": re.compile(r"/home/(?![<${])[A-Za-z0-9._-]+/"),
}

MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)

@dataclass(frozen=True)
class Finding:
    severity: str
    code: str
    path: str
    detail: str


def iter_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def is_text_candidate(path: Path) -> bool:
    return path.suffix.lower() in TEXT_EXTENSIONS or path.name in {"LICENSE", "VERSION"}


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def parse_frontmatter(text: str) -> dict[str, str]:
    match = FRONTMATTER_RE.search(text)
    if not match:
        return {}
    out: dict[str, str] = {}
    for raw in match.group(1).splitlines():
        if ":" not in raw or raw.lstrip().startswith("#"):
            continue
        key, value = raw.split(":", 1)
        out[key.strip()] = value.strip().strip('"\'')
    return out


def markdown_link_findings(root: Path, path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for target in MARKDOWN_LINK_RE.findall(text):
        target = target.strip().split("#", 1)[0].strip()
        if not target or target.startswith(("http://", "https://", "mailto:", "tel:", "data:")):
            continue
        if target.startswith("<") and target.endswith(">"):
            target = target[1:-1]
        # Ignore templated and command-like pseudo-links.
        if any(token in target for token in ("${", "{{", "<version>", "<name>")):
            continue
        candidate = (path.parent / target).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            findings.append(Finding("error", "markdown-link-escape", rel(root, path), target))
            continue
        if not candidate.exists():
            findings.append(Finding("error", "broken-local-link", rel(root, path), target))
    return findings


def scan_sensitive(root: Path, path: Path, text: str, scan_email: bool) -> list[Finding]:
    findings: list[Finding] = []
    p = rel(root, path)
    for name, pattern in SECRET_PATTERNS.items():
        if pattern.search(text):
            findings.append(Finding("error", f"secret-{name}", p, "credential-like value detected"))
    for name, pattern in LOCAL_PATH_PATTERNS.items():
        if pattern.search(text):
            findings.append(Finding("error", name, p, "real-looking absolute user path detected"))
    if scan_email:
        email_re = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
        for match in email_re.finditer(text):
            value = match.group(0)
            if any(safe in value.lower() for safe in ("noreply.github.com", "example.com", "example.org")):
                continue
            findings.append(Finding("warning", "email-address", p, "public email-like value detected"))
            break
    return findings


def validate_required_paths(root: Path, manifest: dict) -> list[Finding]:
    findings: list[Finding] = []
    for item in manifest.get("required_paths", []):
        if not (root / item).exists():
            findings.append(Finding("error", "missing-required-path", item, "required by repo-guard.json"))
    for pattern in manifest.get("forbidden_globs", []):
        for path in iter_files(root):
            if fnmatch.fnmatch(rel(root, path), pattern):
                findings.append(Finding("error", "forbidden-path", rel(root, path), f"matched {pattern}"))
    return findings


def validate_skill(root: Path, config: dict) -> list[Finding]:
    findings: list[Finding] = []
    skill_root = root / config.get("skill_root", ".")
    skill_md = skill_root / "SKILL.md"
    if not skill_md.exists():
        return [Finding("error", "skill-missing", rel(root, skill_md), "SKILL.md not found")]
    text = read_text(skill_md) or ""
    fm = parse_frontmatter(text)
    expected_name = config.get("expected_name")
    if expected_name and fm.get("name") != expected_name:
        findings.append(Finding("error", "skill-name", rel(root, skill_md), f"expected {expected_name!r}, got {fm.get('name')!r}"))
    if not fm.get("description"):
        findings.append(Finding("error", "skill-description", rel(root, skill_md), "frontmatter description missing"))

    refs = config.get("required_references", [])
    for item in refs:
        target = skill_root / "references" / item
        if not target.exists():
            findings.append(Finding("error", "skill-reference", rel(root, target), "required reference missing"))

    agent_path = skill_root / "agents" / "openai.yaml"
    if not agent_path.exists():
        findings.append(Finding("error", "agent-config", rel(root, agent_path), "openai.yaml missing"))
    else:
        agent_text = read_text(agent_path) or ""
        implicit = config.get("allow_implicit_invocation")
        if implicit is not None:
            expected = f"allow_implicit_invocation: {'true' if implicit else 'false'}"
            if expected not in agent_text:
                findings.append(Finding("error", "implicit-policy", rel(root, agent_path), f"expected {expected}"))
        token = f"${expected_name}" if expected_name else None
        if token and token not in agent_text:
            findings.append(Finding("error", "default-prompt-token", rel(root, agent_path), f"expected {token}"))
    return findings


def validate_attribution(root: Path, config: dict) -> list[Finding]:
    findings: list[Finding] = []
    for path_str in config.get("required_files", []):
        if not (root / path_str).exists():
            findings.append(Finding("error", "attribution-file", path_str, "required attribution file missing"))
    for check in config.get("text_checks", []):
        path = root / check["path"]
        text = read_text(path) if path.exists() else None
        if text is None or check["contains"] not in text:
            findings.append(Finding("error", "attribution-text", check["path"], f"missing required text: {check['contains']}"))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument("--manifest", default="repo-guard.json")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    manifest_path = root / args.manifest
    if not manifest_path.exists():
        print(f"ERROR: manifest not found: {manifest_path}", file=sys.stderr)
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    findings: list[Finding] = []
    findings.extend(validate_required_paths(root, manifest))
    scan_email = bool(manifest.get("scan_email", False))

    for path in iter_files(root):
        if not is_text_candidate(path):
            continue
        text = read_text(path)
        if text is None:
            continue
        findings.extend(scan_sensitive(root, path, text, scan_email))
        if path.suffix.lower() == ".md":
            findings.extend(markdown_link_findings(root, path, text))

    if "skill_contract" in manifest:
        findings.extend(validate_skill(root, manifest["skill_contract"]))
    if "attribution_contract" in manifest:
        findings.extend(validate_attribution(root, manifest["attribution_contract"]))

    # Stable ordering keeps CI output deterministic.
    findings.sort(key=lambda f: (f.severity, f.code, f.path, f.detail))
    errors = [f for f in findings if f.severity == "error"]

    if args.json:
        print(json.dumps({
            "ok": not errors,
            "error_count": len(errors),
            "warning_count": sum(f.severity == "warning" for f in findings),
            "findings": [f.__dict__ for f in findings],
        }, ensure_ascii=False, indent=2))
    else:
        for f in findings:
            print(f"{f.severity.upper():7} {f.code:24} {f.path}: {f.detail}")
        if not findings:
            print("Repository guard: PASS (no findings)")
        else:
            print(f"Repository guard: {'FAIL' if errors else 'PASS'} ({len(errors)} errors, {len(findings)-len(errors)} warnings)")
    return 1 if errors else 0

if __name__ == "__main__":
    raise SystemExit(main())
