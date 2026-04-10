#!/bin/bash
# ============================================================
# Video-Claude Tool - Startup Script
# ============================================================

echo "🎬 Video-Claude Tool Setup"
echo "=========================="

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 not found. Please install Python 3.9+"
    exit 1
fi

# Check ffmpeg
if ! command -v ffmpeg &> /dev/null; then
    echo "⚠️  ffmpeg not found. Installing..."
    if [[ "$OSTYPE" == "darwin"* ]]; then
        brew install ffmpeg
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        sudo apt-get install -y ffmpeg
    fi
fi

# Check/Install whisper
if ! command -v whisper &> /dev/null; then
    echo "⚠️  Whisper not found. Installing openai-whisper..."
    pip3 install openai-whisper
fi

# Install Python deps
cd backend
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate
pip install -r requirements.txt -q

# Set API key
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo ""
    read -p "🔑 Enter your Anthropic API key: " key
    export ANTHROPIC_API_KEY="$key"
fi

echo ""
echo "✅ Starting backend on http://localhost:8000"
echo "🌐 Open frontend/index.html in your browser"
echo ""
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
