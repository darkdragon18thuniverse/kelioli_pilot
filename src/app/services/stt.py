import os
import httpx
from sarvamai import SarvamAI

TMP_DIR = "/tmp"
os.makedirs(TMP_DIR, exist_ok=True)

SARVAM_API_KEY = os.getenv("SARVAM_API_KEY")
if not SARVAM_API_KEY:
    raise RuntimeError("CRITICAL: SARVAM_API_KEY environment variable is not set")

SARVAM_CLIENT = SarvamAI(api_subscription_key=SARVAM_API_KEY)

import httpx

def download_and_transcribe_call(url: str, client: httpx.Client) -> str:
    """
    TEMPORARY MOCK FOR DEVELOPMENT
    Bypasses live downloads and Sarvam AI processing to return a structural response.
    Uncomment original implementation when ready for production.
    """
    # Returns a hardcoded sample matching your exact expected structure
    return (
        "ஹலோ, ஹலோ, இங்க ஆசிரி எங்கங்க இருக்கு? அம்மா இது சென்னைங்கம்மா, "
        "சென்னையில வளசர பக்கமா. நீங்க எங்க இருந்தும்மா பேசுறீங்க? நாங்க "
        "கும்பவாட்சியிலிருந்து பேசுறோமா? சொல்லுங்கம்மா, என்ன தகவல் வேணுங்கம்மா? "
        "இல்ல பல்லு சுத்தமா இல்ல, எல்லாம் விழுந்துருச்சு. ஓகேம்மா. அம்மா நான் "
        "உங்களுக்கு இங்க கோயம்புத்தூர் உங்கள் திரும்ப நான் கூப்பிடுறேம்மா இப்போ. ம் சரி."
    )
    #uncomment for prod
# def download_and_transcribe_call(url: str, client: httpx.Client) -> str:
    """Sequentially downloads an audio file link and requests an English-Indian transcription."""
    filename = os.path.basename(url.split("?")[0])
    if not filename.endswith((".mp3", ".wav")):
        filename = f"audio_{hash(url)}.mp3"
    dest_path = os.path.join(TMP_DIR, filename)

    try:
        # 1. Download tracking streams
        with client.stream("GET", url) as response:
            if response.status_code != 200:
                raise httpx.HTTPStatusError(
                    f"HTTP {response.status_code}", 
                    request=response.request, 
                    response=response
                )
            
            with open(dest_path, "wb") as f:
                for chunk in response.iter_bytes(chunk_size=16384):
                    f.write(chunk)

        # 2. Extract transcription analytics string
        with open(dest_path, "rb") as audio_file:
            sarvam_response = SARVAM_CLIENT.speech_to_text.transcribe(
                file=audio_file,
                model="saaras:v3",
                mode="transcribe",
            )
        return sarvam_response.transcript

    finally:
        if os.path.exists(dest_path):
            os.remove(dest_path)