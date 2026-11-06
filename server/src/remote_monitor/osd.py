"""On-screen display: burns timing information into the video (visible in every viewer
and in every recording, as required for evidence-grade surveillance footage)."""

from __future__ import annotations

from datetime import datetime

import cv2
import numpy as np

FONT = cv2.FONT_HERSHEY_SIMPLEX


def _text(img: np.ndarray, text: str, org: tuple[int, int], scale: float, color=(255, 255, 255)) -> None:
    thick = max(1, int(round(scale * 2)))
    cv2.putText(img, text, org, FONT, scale, (0, 0, 0), thick + 2, cv2.LINE_AA)  # outline
    cv2.putText(img, text, org, FONT, scale, color, thick, cv2.LINE_AA)


def draw_osd(img: np.ndarray, camera_name: str, ts_us: int, recording: bool = False, motion: bool = False) -> None:
    h, w = img.shape[:2]
    scale = max(0.45, h / 900)
    pad = int(12 * scale / 0.8)
    ts = datetime.fromtimestamp(ts_us / 1e6).strftime("%Y-%m-%d %H:%M:%S.") + f"{(ts_us // 1000) % 1000:03d}"
    (tw, th), _ = cv2.getTextSize(ts, FONT, scale, 2)
    _text(img, ts, (w - tw - pad, th + pad), scale)
    _text(img, camera_name, (pad, th + pad), scale)
    y = h - pad
    if recording:
        cv2.circle(img, (pad + int(8 * scale / 0.6), y - th // 2), int(8 * scale / 0.6), (0, 0, 255), -1)
        _text(img, "REC", (pad + int(22 * scale / 0.6), y), scale, (0, 0, 255))
    if motion:
        (mw, _), _ = cv2.getTextSize("MOTION", FONT, scale, 2)
        _text(img, "MOTION", (w - mw - pad, y), scale, (0, 165, 255))
