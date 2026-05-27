# Voice-Activated Claude Code for macOS

Hold Right Option to talk to Claude Code. Claude responds via voice.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Test standalone (record → transcribe → speak back)
python main.py --standalone

# Run with Claude Code
claude --dangerously-load-development-channels voice-mac
```

## Requirements

- macOS (uses `say` for TTS, `afplay` for chime)
- Python 3.10+
- Microphone access (System Settings > Privacy > Microphone)
- Accessibility access for pynput (System Settings > Privacy > Accessibility)

## How it works

1. Hold **Right Option** key to record
2. Release to transcribe (faster-whisper, local)
3. In standalone mode: speaks back what you said
4. In MCP mode: sends transcription to Claude Code, Claude replies via voice
