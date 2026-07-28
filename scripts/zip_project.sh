#!/usr/bin/env bash
set -e

# Output zip filename
ZIP_NAME="kelioli_pilot_production.zip"

echo "📦 Packaging Kelioli AI Pilot Core into ${ZIP_NAME}..."

# Remove previous zip if exists
rm -f "${ZIP_NAME}"

# Clean up any transient python compiled artifacts locally
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find . -type f -name "*.pyc" -delete 2>/dev/null || true

# Execute zip command with exclusions
zip -r "${ZIP_NAME}" . \
  -x "*.venv*" \
  -x "*__pycache__*" \
  -x "*.pytest_cache*" \
  -x "*.DS_Store*" \
  -x "*.git*" \
  -x "dev.db" \
  -x "src/app/*.db*" \
  -x "*.log" \
  -x "${ZIP_NAME}"

echo "✅ Production archive created successfully: ${ZIP_NAME}"
ls -lh "${ZIP_NAME}"
