#!/usr/bin/env python3
"""Voice-activated Claude Code interface for macOS.

Hold Right Option to record, release to send to Claude Code.
Claude's response is spoken back via macOS `say`.

Can run standalone (for testing) or as an MCP channel server
launched by Claude Code.

Usage:
  Standalone test (records → transcribes → speaks back):
    python main.py --standalone

  As MCP channel (launched by Claude Code):
    claude --dangerously-load-development-channels voice-mac
"""

import sys
import os
import signal
import subprocess
import threading
import time
import json
import logging
import argparse
from datetime import datetime, timezone

import numpy as np
import sounddevice as sd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
log = logging.getLogger("voice-claude")

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------
SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "float32"

whisper_model = None
recording = False
audio_frames = []
say_process = None
processing = False


# ---------------------------------------------------------------------------
# Whisper model (loaded once at startup)
# ---------------------------------------------------------------------------
def load_whisper():
    global whisper_model
    log.info("Loading whisper model (small, int8)...")
    from faster_whisper import WhisperModel
    whisper_model = WhisperModel("small", device="cpu", compute_type="int8")
    log.info("Whisper model loaded.")


def transcribe(audio_np):
    """Two-pass transcription: auto-detect then forced Hebrew."""
    if whisper_model is None:
        return ""

    prompt = "The speaker switches between English and Hebrew mid-sentence."

    # Pass 1: auto-detect
    segments, info = whisper_model.transcribe(
        audio_np, language=None, beam_size=1, initial_prompt=prompt
    )
    auto_text = " ".join(seg.text.strip() for seg in segments)
    detected = info.language

    if not auto_text.strip():
        return ""

    # If Hebrew detected, single pass is enough
    if detected == "he":
        return auto_text

    # Pass 2: forced Hebrew
    segments_he, _ = whisper_model.transcribe(audio_np, language="he", beam_size=1)
    he_text = " ".join(seg.text.strip() for seg in segments_he)

    # If similar, return auto
    words_a = set(auto_text.lower().split())
    words_b = set(he_text.lower().split())
    if words_a and words_b:
        overlap = len(words_a & words_b) / max(len(words_a), len(words_b))
        if overlap > 0.6:
            return auto_text

    # Return merged (let Claude handle it)
    return f"[auto-detect ({detected})]: {auto_text}\n[hebrew pass]: {he_text}"


# ---------------------------------------------------------------------------
# macOS TTS
# ---------------------------------------------------------------------------
def speak(text):
    """Speak text via macOS `say` (async, non-blocking)."""
    global say_process
    kill_speech()
    if not text.strip():
        return
    log.info(f"Speaking: {text[:80]}...")
    try:
        say_process = subprocess.Popen(
            ["say", text],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Watchdog: kill after 30s
        def watchdog():
            time.sleep(30)
            if say_process and say_process.poll() is None:
                say_process.kill()
                log.warning("say process killed after 30s timeout")
        threading.Thread(target=watchdog, daemon=True).start()
    except FileNotFoundError:
        log.error("`say` command not found — are you on macOS?")


def kill_speech():
    """Kill any running TTS."""
    global say_process
    if say_process and say_process.poll() is None:
        say_process.kill()
        say_process = None


def play_chime():
    """Play a short chime sound (async). Falls back to system beep."""
    chime_path = os.path.join(os.path.dirname(__file__), "sounds", "wake.wav")
    if os.path.exists(chime_path):
        threading.Thread(
            target=lambda: subprocess.run(
                ["afplay", chime_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ),
            daemon=True,
        ).start()
    else:
        # System beep fallback
        print("\a", end="", flush=True)


# ---------------------------------------------------------------------------
# Audio recording
# ---------------------------------------------------------------------------
def start_recording():
    """Start recording audio from microphone."""
    global recording, audio_frames
    audio_frames = []
    recording = True
    log.info("Recording started...")

    def callback(indata, frames, time_info, status):
        if status:
            log.warning(f"Audio status: {status}")
        if recording:
            audio_frames.append(indata.copy())

    try:
        stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            callback=callback,
            blocksize=1024,
        )
        stream.start()
        # Store stream so we can stop it
        return stream
    except Exception as e:
        log.error(f"Failed to start recording: {e}")
        recording = False
        return None


def stop_recording(stream):
    """Stop recording and return audio as numpy array."""
    global recording
    recording = False
    if stream:
        stream.stop()
        stream.close()

    if not audio_frames:
        return None

    audio = np.concatenate(audio_frames, axis=0).flatten()
    duration = len(audio) / SAMPLE_RATE
    log.info(f"Recording stopped. Duration: {duration:.1f}s")

    # Ignore very short recordings (< 0.3s — probably accidental)
    if duration < 0.3:
        log.info("Too short, ignoring.")
        return None

    return audio


# ---------------------------------------------------------------------------
# MCP Channel Server
# ---------------------------------------------------------------------------
def run_as_mcp_channel():
    """Run as an MCP channel server, communicating with Claude Code via stdio."""
    import asyncio
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp import types

    server = Server("voice-mac")
    response_event = asyncio.Event()
    response_text = ""

    @server.list_tools()
    async def list_tools():
        return [
            types.Tool(
                name="reply",
                description="Reply to the user via voice (macOS TTS). Use this to respond to voice messages.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "The text to speak back to the user",
                        }
                    },
                    "required": ["text"],
                },
            )
        ]

    @server.call_tool()
    async def call_tool(name, arguments):
        nonlocal response_text
        if name == "reply":
            text = arguments.get("text", "")
            speak(text)
            response_text = text
            response_event.set()
            return [types.TextContent(type="text", text=f"Spoke: {text[:100]}...")]
        raise ValueError(f"Unknown tool: {name}")

    async def voice_loop():
        """Main voice input loop running alongside MCP server."""
        nonlocal response_text

        from pynput import keyboard

        stream = None
        key_held = False

        def on_press(key):
            nonlocal stream, key_held
            if key == keyboard.Key.alt_r and not key_held and not processing:
                key_held = True
                kill_speech()
                play_chime()
                stream = start_recording()

        def on_release(key):
            nonlocal stream, key_held
            if key == keyboard.Key.alt_r and key_held:
                key_held = False
                if stream:
                    audio = stop_recording(stream)
                    stream = None
                    if audio is not None:
                        asyncio.get_event_loop().call_soon_threadsafe(
                            lambda: asyncio.ensure_future(process_audio(audio))
                        )

        async def process_audio(audio):
            global processing
            processing = True
            try:
                text = transcribe(audio)
                if not text.strip():
                    log.info("Empty transcription, ignoring.")
                    return

                log.info(f"Transcribed: {text[:100]}")

                # Send as channel notification
                await server.request_context.session.send_notification(
                    method="notifications/claude/channel",
                    params={
                        "content": text,
                        "meta": {
                            "source": "voice-mac",
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "user": "sergei",
                        },
                    },
                )
                log.info("Sent to Claude Code.")
            except Exception as e:
                log.error(f"Error processing audio: {e}")
                speak("Sorry, something went wrong.")
            finally:
                processing = False

        listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        listener.start()
        log.info("Voice loop started. Hold Right Option to talk.")

        # Keep running
        while True:
            await asyncio.sleep(1)

    async def main():
        # Start voice loop in background
        asyncio.ensure_future(voice_loop())

        # Run MCP server on stdio
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(main())


# ---------------------------------------------------------------------------
# Standalone mode (for testing without Claude Code)
# ---------------------------------------------------------------------------
def run_standalone():
    """Run in standalone mode: record → transcribe → speak back."""
    from pynput import keyboard

    stream = None
    key_held = False

    log.info("=== Standalone mode ===")
    log.info("Hold Right Option to record, release to transcribe and speak.")
    log.info("Press Ctrl+C to quit.")

    def on_press(key):
        nonlocal stream, key_held
        if key == keyboard.Key.alt_r and not key_held and not processing:
            key_held = True
            kill_speech()
            play_chime()
            stream = start_recording()

    def on_release(key):
        nonlocal stream, key_held
        if key == keyboard.Key.alt_r and key_held:
            key_held = False
            if stream:
                audio = stop_recording(stream)
                stream = None
                if audio is not None:
                    threading.Thread(
                        target=process_standalone, args=(audio,), daemon=True
                    ).start()

    def process_standalone(audio):
        global processing
        processing = True
        try:
            text = transcribe(audio)
            if not text.strip():
                log.info("Empty transcription, ignoring.")
                return
            log.info(f"Transcribed: {text}")
            speak(f"You said: {text}")
        finally:
            processing = False

    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        try:
            listener.join()
        except KeyboardInterrupt:
            log.info("Goodbye!")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Voice-activated Claude Code for macOS")
    parser.add_argument(
        "--standalone",
        action="store_true",
        help="Run in standalone test mode (no Claude Code connection)",
    )
    args = parser.parse_args()

    # Load whisper model
    load_whisper()

    if args.standalone:
        run_standalone()
    else:
        run_as_mcp_channel()


if __name__ == "__main__":
    main()
