from __future__ import annotations

import logging
from typing import Tuple

import httpx

logger = logging.getLogger(__name__)

# Languages considered native/readable — do not translate if detected as these
_SKIP_LANGS = {"en", "bn"}  # English, Bengali


def detect_and_translate(text: str) -> Tuple[str, str | None]:
    """
    Detect the language of `text`.
    If text is empty or already in English/Bengali, returns original text.
    If in any other language, translates to English using a multi-engine fallback.
    Returns: (translated_text, detected_lang_code)
    """
    if not text or not text.strip():
        return text, None

    # 1. Detect language
    detected_lang: str | None = None
    try:
        from langdetect import detect, LangDetectException

        try:
            detected_lang = detect(text)
        except LangDetectException:
            detected_lang = None
    except Exception as e:
        logger.debug("Language detection error: %s", e)

    # 2. Skip translation if English or Bengali
    if detected_lang in _SKIP_LANGS:
        return text, detected_lang

    # 3. Try primary translation: GoogleTranslator (deep_translator)
    try:
        from deep_translator import GoogleTranslator

        translated = GoogleTranslator(source="auto", target="en").translate(text)
        if translated and translated.strip():
            return translated.strip(), detected_lang
    except Exception as e:
        logger.debug("GoogleTranslator failed: %s", e)

    # 4. Fallback translation: MyMemory API
    try:
        pair = f"{detected_lang}|en" if detected_lang else "autodetect|en"
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(
                "https://api.mymemory.translated.net/get",
                params={"q": text, "langpair": pair},
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
    return text, detected_lang
