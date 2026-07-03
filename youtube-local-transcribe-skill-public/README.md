# youtube-local-transcribe

Caption-first local transcription workflow for YouTube, Bilibili, and other `yt-dlp` supported video URLs.

This repository contains two pieces:

- `ytlt`: a Python CLI that probes the machine, recommends an install profile, downloads captions when available, falls back to local Whisper transcription, writes HTML reports, and serves a local dashboard.
- `codex-skill`: a Codex skill wrapper that tells Codex how to run the CLI and write grounded summaries.

## Install

Base install and hardware-aware setup:

```bash
python -m pip install -e .
ytlt probe
ytlt setup --dry-run
ytlt setup --execute
```

Setup detects the local hardware, installs the matching transcription backend plus `imageio-ffmpeg`, verifies an ffmpeg binary, downloads the recommended Whisper model into `<workspace>/models`, and writes `<workspace>/config.json` with the resolved ffmpeg path. On capable Apple Silicon and NVIDIA machines this selects a large-v3-turbo profile; constrained hardware gets a smaller safe model.

## Process a video

```bash
ytlt process "VIDEO_URL" --language zh
```

The processor first tries manual subtitles, then auto subtitles, then local Whisper transcription. Generated artifacts are written under `~/Documents/youtube/processed/`.

## Open the report dashboard

```bash
ytlt serve --open
```

The dashboard binds to `127.0.0.1` and indexes past generated reports from the workspace.

## Codex skill install

Copy `codex-skill` into your Codex skills directory as `youtube-local-transcribe`:

```bash
mkdir -p "$HOME/.codex/skills"
cp -R codex-skill "$HOME/.codex/skills/youtube-local-transcribe"
```
