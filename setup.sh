#!/bin/bash
# One-command setup for voice-claude on macOS
# Usage: curl -sSL https://raw.githubusercontent.com/sergeistrafficcpo-crypto/voice-claude/master/setup.sh | bash

set -e

REPO="https://github.com/sergeistrafficcpo-crypto/voice-claude.git"
DIR="$HOME/voice-claude"

echo "=== Voice-Claude Setup ==="

# Clone or update
if [ -d "$DIR" ]; then
    echo "Updating existing installation..."
    cd "$DIR" && git pull
else
    echo "Cloning repository..."
    git clone "$REPO" "$DIR"
    cd "$DIR"
fi

# Create virtual environment
if [ ! -d "$DIR/venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

echo "Installing dependencies..."
source venv/bin/activate
pip install -q -r requirements.txt

echo ""
echo "=== Setup complete! ==="
echo ""
echo "To test (standalone mode):"
echo "  cd $DIR && source venv/bin/activate && python main.py --standalone"
echo ""
echo "Hold Right Option to record, release to hear transcription."
echo ""
echo "NOTE: macOS will ask for Microphone and Accessibility permissions."
