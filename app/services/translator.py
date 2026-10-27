from __future__ import annotations

import logging
from typing import Tuple

import httpx

logger = logging.getLogger(__name__)

# Languages considered native/readable — do not translate if detected as these
_SKIP_LANGS = {"en", "bn"}


def is_bengali(text: str) -> bool:
    """Checks if text contains Bengali characters."""
    return any("\u0980" <= ch <= "\u09ff" for ch in text)


def detect_and_translate(text: str) -> Tuple[str, str | None]:
    """
    Detect the language of `text`.
    If text is Bengali or English, returns original text without translation.
    If in any other language, translates to English using a multi-engine fallback.
    Returns: (translated_text, detected_lang_code)
    """
    if not text or not text.strip():
        return text, None

    clean_text = text.strip()

    # 1. Fast check for Bengali script
    if is_bengali(clean_text):
        return clean_text, "bn"

    # 2. Detect language via langdetect
    detected_lang: str | None = None
    try:
        from langdetect import detect, LangDetectException

        try:
            detected_lang = detect(clean_text)
        except LangDetectException:
            detected_lang = None
    except Exception as e:
        logger.debug("Language detection error: %s", e)

    # 3. Skip translation if detected as English or Bengali
    if detected_lang in _SKIP_LANGS:
        return clean_text, detected_lang

    # 4. Try primary translation: GoogleTranslator
    try:
        from deep_translator import GoogleTranslator

        translated = GoogleTranslator(source="auto", target="en").translate(clean_text)
        if translated and translated.strip():
            return translated.strip(), detected_lang
    except Exception as e:
        logger.debug("GoogleTranslator failed: %s", e)

    # 5. Fallback translation: MyMemory API
    try:
        pair = f"{detected_lang}|en" if detected_lang else "autodetect|en"
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(
                "https://api.mymemory.translated.net/get",
                params={"q": clean_text, "langpair": pair},
            )
            if resp.status_code == 200:
                data = resp.json()
                trans_text = data.get("responseData", {}).get("translatedText")
                if trans_text and trans_text.strip():
                    if not detected_lang:
                        detected_lang = data.get("responseData", {}).get("detectedLanguage")
                    return trans_text.strip(), detected_lang
    except Exception as e:
        logger.warning("MyMemory translation fallback failed: %s", e)

    # If all translations fail, return original text safely
    return clean_text, detected_lang
