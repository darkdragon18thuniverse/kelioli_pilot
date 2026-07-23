"""
Shared system constants for pricing defaults, directory paths, and configuration.
"""
import os

# System-wide pricing defaults
DEFAULT_PER_MINUTE_COST: float = 0.0
DEFAULT_INFRA_FIXED_COST: float = 0.0
DEFAULT_MAX_MONTHLY_MINUTES: float = 50.0

# Temporary audio storage directory path (configurable via environment)
TEMP_AUDIO_DIR: str = os.getenv("TEMP_AUDIO_DIR", "./media/temp_audio")
