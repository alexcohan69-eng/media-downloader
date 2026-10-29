from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import Literal

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

PositionType = Literal["bottom-left", "bottom-right", "top-left", "top-right"]


def update_image_timestamp(
    image_path: Path | str,
    output_path: Path | str | None = None,
    custom_text: str | None = None,
    position: PositionType = "bottom-left",
) -> Path:
    """
    Seamlessly cleans existing timestamp watermark in the specified corner
    using gradient interpolation and photographic noise matching,
    then overlays an authentic smartphone camera timestamp.
    """
    in_path = Path(image_path)
    if not in_path.exists():
        raise FileNotFoundError(f"Image not found: {in_path}")

    img_bgr = cv2.imread(str(in_path))
    if img_bgr is None:
        raise ValueError(f"Could not decode image at: {in_path}")

    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]

    # Timestamp text
    now = datetime.datetime.now()
    text = custom_text.strip() if custom_text and custom_text.strip() else now.strftime("%b %d, %Y, %H:%M")

    # Responsive typography matching mobile camera watermarks (Roboto)
    font_size = max(14, int(h * 0.0178))
    font_candidates = [
        "/usr/share/fonts/truetype/roboto/unhinted/Roboto-Regular.ttf",
        "/usr/share/fonts/truetype/roboto/unhinted/Roboto-Medium.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    font_path = next((f for f in font_candidates if Path(f).exists()), None)
    if font_path:
        font = ImageFont.truetype(font_path, font_size)
    else:
        font = ImageFont.load_default()

    bbox = font.getbbox(text)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    # Position calculations
    margin_x = int(w * 0.030)
    margin_y = int(h * 0.035)

    if position == "bottom-left":
        tx = margin_x
        ty = h - margin_y - font_size
    elif position == "bottom-right":
        tx = w - margin_x - text_w
        ty = h - margin_y - font_size
    elif position == "top-left":
        tx = margin_x
        ty = margin_y
    else:
        tx = w - margin_x - text_w
        ty = margin_y

    # Clean existing watermark box
    y1 = max(4, ty - int(font_size * 0.35))
    y2 = min(h - 4, ty + text_h + int(font_size * 0.55))
    x1 = max(4, tx - int(font_size * 0.4))
    x2 = min(w - 4, tx + text_w + int(font_size * 1.5))
    h_box = y2 - y1
    w_box = x2 - x1

    # Vertical gradient interpolation from surrounding borders
    top_strip = rgb[y1 - 3 : y1, x1 : x2].mean(axis=0)
    bot_strip = rgb[y2 : y2 + 3, x1 : x2].mean(axis=0)
    alpha = np.linspace(0, 1, h_box)[:, None, None]
    gradient = (1.0 - alpha) * top_strip[None, :, :] + alpha * bot_strip[None, :, :]

    # Sample natural noise from nearby region
    noise_y1 = max(0, y1 - h_box - 5)
    noise_y2 = noise_y1 + h_box
    clean_noise_src = rgb[noise_y1:noise_y2, x1:x2].astype(np.float32)
    if clean_noise_src.shape[0] == h_box and clean_noise_src.shape[1] == w_box:
        smooth_src = cv2.GaussianBlur(clean_noise_src, (7, 7), 0)
        noise = clean_noise_src - smooth_src
    else:
        noise = np.random.normal(0, 2.0, (h_box, w_box, 3)).astype(np.float32)

    clean_patch = np.clip(gradient + noise, 0, 255).astype(np.float32)

    # Edge feathering to avoid visible seams
    feather = min(6, min(h_box, w_box) // 4)
    mask = np.ones((h_box, w_box), dtype=np.float32)
    for i in range(feather):
        val = (i + 1) / (feather + 1)
        mask[i, :] = np.minimum(mask[i, :], val)
        mask[-(i + 1), :] = np.minimum(mask[-(i + 1), :], val)
        mask[:, i] = np.minimum(mask[:, i], val)
        mask[:, -(i + 1)] = np.minimum(mask[:, -(i + 1)], val)
    mask = mask[:, :, None]

    orig_dest = rgb[y1:y2, x1:x2].astype(np.float32)
    blended_bg = mask * clean_patch + (1.0 - mask) * orig_dest
    rgb[y1:y2, x1:x2] = np.clip(blended_bg, 0, 255).astype(np.uint8)

    # Render authentic camera watermark text
    pil_img = Image.fromarray(rgb)
    draw = ImageDraw.Draw(pil_img)
    draw.text((tx, ty), text, font=font, fill=(248, 246, 242))

    out_file = Path(output_path) if output_path else in_path.with_name(f"{in_path.stem}_timestamped.jpg")
    pil_img.save(str(out_file), "JPEG", quality=98, subsampling=0)
    logger.info("Saved timestamped image to %s", out_file)
    return out_file
