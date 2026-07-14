"""
Gemini 1.5 Flash wrapper.
Edit this file to switch AI providers or change generation settings.
"""
import requests
from .config import GEMINI_API_KEY

_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-1.5-flash:generateContent?key={key}"
)
_SAFETY = [
    {"category": c, "threshold": "BLOCK_NONE"}
    for c in (
        "HARM_CATEGORY_HARASSMENT",
        "HARM_CATEGORY_HATE_SPEECH",
        "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "HARM_CATEGORY_DANGEROUS_CONTENT",
    )
]


def gemini(system: str, user: str, max_tokens: int = 2000) -> str:
    """Call Gemini 1.5 Flash. Returns text or '' on failure."""
    if not GEMINI_API_KEY:
        return ""
    combined = f"{system}\n\n---\n\n{user}" if system else user
    payload  = {
        "contents":       [{"parts": [{"text": combined}]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature":     0.2,
            "topP":            0.8,
        },
        "safetySettings": _SAFETY,
    }
    try:
        r = requests.post(
            _GEMINI_URL.format(key=GEMINI_API_KEY),
            json=payload, timeout=60,
        )
        if r.ok:
            parts = (r.json()
                     .get("candidates", [{}])[0]
                     .get("content", {})
                     .get("parts", []))
            return parts[0].get("text", "") if parts else ""
        print(f"[Gemini] HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[Gemini] Error: {e}")
    return ""
