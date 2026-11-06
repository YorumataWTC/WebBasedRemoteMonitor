"""Face detection, face recognition and facial-emotion recognition (OpenCV DNN, CPU only).

Models (OpenCV Model Zoo, Apache-2.0 / MIT licensed, downloaded by ``remote-monitor models``):

* YuNet (2023mar)        - face detector, 5 landmarks per face, ~1 ms per 640x360 image.
* SFace (2021dec)        - 128-d face embedding; cosine similarity >= 0.363 means same person.
* MobileFaceNet FER      - 7 facial expressions from a 112x112 aligned face crop.

Recognition flow: detected face -> similarity alignment using the 5 landmarks (alignCrop)
-> SFace embedding -> nearest neighbour in the enrolled gallery. The same aligned crop feeds
the expression classifier, so alignment is computed once per face.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)

MODEL_URLS = {
    "face_detection_yunet_2023mar.onnx": "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "face_recognition_sface_2021dec.onnx": "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
    "facial_expression_recognition_mobilefacenet_2022july.onnx": "https://github.com/opencv/opencv_zoo/raw/main/models/facial_expression_recognition/"
    "facial_expression_recognition_mobilefacenet_2022july.onnx",
}
EMOTIONS = ["angry", "disgust", "fearful", "happy", "neutral", "sad", "surprised"]
COSINE_THRESHOLD = 0.363  # from the SFace paper / OpenCV zoo evaluation (LFW)


@dataclass
class FaceResult:
    box: tuple[float, float, float, float]  # normalised
    score: float
    landmarks: list[tuple[float, float]]
    name: str | None = None
    similarity: float | None = None
    emotion: str | None = None
    emotion_score: float | None = None

    def to_json(self) -> dict:
        return {
            "box": [round(v, 4) for v in self.box],
            "score": round(self.score, 3),
            "name": self.name,
            "similarity": None if self.similarity is None else round(self.similarity, 3),
            "emotion": self.emotion,
            "emotionScore": None if self.emotion_score is None else round(self.emotion_score, 3),
            "landmarks": [[round(x, 4), round(y, 4)] for x, y in self.landmarks],
        }


class FaceGallery:
    """Enrolled identities persisted as JSON (embeddings) + JPEG thumbnails."""

    def __init__(self, data_dir: Path):
        self.dir = data_dir / "faces"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.file = self.dir / "gallery.json"
        self._lock = threading.Lock()
        self.entries: list[dict] = []
        if self.file.exists():
            try:
                self.entries = json.loads(self.file.read_text("utf-8"))
            except Exception:
                log.warning("face gallery unreadable, starting empty")
        self._rebuild()

    def _rebuild(self) -> None:
        if self.entries:
            m = np.array([e["embedding"] for e in self.entries], np.float32)
            self._matrix = m / np.linalg.norm(m, axis=1, keepdims=True)
        else:
            self._matrix = np.zeros((0, 128), np.float32)

    def _save(self) -> None:
        self.file.write_text(json.dumps(self.entries), "utf-8")

    def add(self, name: str, embedding: np.ndarray, thumb: np.ndarray) -> dict:
        entry = {
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "created": time.time(),
            "embedding": embedding.astype(float).ravel().tolist(),
        }
        cv2.imwrite(str(self.dir / f"{entry['id']}.jpg"), thumb)
        with self._lock:
            self.entries.append(entry)
            self._rebuild()
            self._save()
        return self.public(entry)

    def remove(self, entry_id: str) -> bool:
        with self._lock:
            n = len(self.entries)
            self.entries = [e for e in self.entries if e["id"] != entry_id]
            self._rebuild()
            self._save()
        (self.dir / f"{entry_id}.jpg").unlink(missing_ok=True)
        return len(self.entries) != n

    def match(self, embedding: np.ndarray) -> tuple[str | None, float]:
        m = self._matrix
        if len(m) == 0:
            return None, 0.0
        e = embedding.ravel().astype(np.float32)
        e /= np.linalg.norm(e) + 1e-9
        sims = m @ e
        i = int(np.argmax(sims))
        sim = float(sims[i])
        return (self.entries[i]["name"] if sim >= COSINE_THRESHOLD else None), sim

    @staticmethod
    def public(e: dict) -> dict:
        return {"id": e["id"], "name": e["name"], "created": e["created"]}

    def list(self) -> list[dict]:
        return [self.public(e) for e in self.entries]


class FaceAnalyzer:
    DETECT_WIDTH = 640

    def __init__(self, models_dir: Path, gallery: FaceGallery):
        self.gallery = gallery
        self.detector = self.recognizer = self.fer = None
        det = models_dir / "face_detection_yunet_2023mar.onnx"
        rec = models_dir / "face_recognition_sface_2021dec.onnx"
        fer = models_dir / "facial_expression_recognition_mobilefacenet_2022july.onnx"
        try:
            if det.exists():
                self.detector = cv2.FaceDetectorYN.create(str(det), "", (320, 320), 0.75, 0.3, 50)
            if rec.exists():
                self.recognizer = cv2.FaceRecognizerSF.create(str(rec), "")
            if fer.exists():
                self.fer = cv2.dnn.readNet(str(fer))
        except Exception as exc:
            log.warning("face models could not be loaded: %s", exc)
        log.info(
            "face analytics: detection=%s recognition=%s emotion=%s",
            self.detector is not None,
            self.recognizer is not None,
            self.fer is not None,
        )

    @property
    def available(self) -> dict:
        return {
            "detection": self.detector is not None,
            "recognition": self.recognizer is not None,
            "emotion": self.fer is not None,
        }

    def _detect(self, img: np.ndarray) -> tuple[np.ndarray, float]:
        """Returns raw YuNet rows in *full-resolution* pixel coordinates."""
        h, w = img.shape[:2]
        scale = min(1.0, self.DETECT_WIDTH / w)
        small = cv2.resize(img, (int(w * scale), int(h * scale))) if scale < 1 else img
        self.detector.setInputSize((small.shape[1], small.shape[0]))
        _, faces = self.detector.detect(small)
        if faces is None:
            return np.zeros((0, 15), np.float32), scale
        faces = faces.copy()
        faces[:, :14] /= scale
        return faces, scale

    def analyze(
        self, img: np.ndarray, recognize: bool = True, emotion: bool = True, max_faces: int = 6
    ) -> list[FaceResult]:
        if self.detector is None:
            return []
        h, w = img.shape[:2]
        faces, _ = self._detect(img)
        results = []
        for row in faces[:max_faces]:
            x, y, bw, bh = row[:4]
            lm = [(float(row[4 + 2 * k] / w), float(row[5 + 2 * k] / h)) for k in range(5)]
            r = FaceResult((float(x / w), float(y / h), float(bw / w), float(bh / h)), float(row[14]), lm)
            if (recognize or emotion) and self.recognizer is not None and bw >= 24:
                aligned = self.recognizer.alignCrop(img, row)
                if recognize and self.gallery.entries:
                    feat = self.recognizer.feature(aligned)
                    r.name, r.similarity = self.gallery.match(feat)
                if emotion and self.fer is not None:
                    r.emotion, r.emotion_score = self._emotion(aligned)
            results.append(r)
        return results

    def _emotion(self, aligned_bgr: np.ndarray) -> tuple[str, float]:
        rgb = cv2.cvtColor(aligned_bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        rgb = (rgb - 0.5) / 0.5
        self.fer.setInput(cv2.dnn.blobFromImage(rgb))
        logits = self.fer.forward().ravel()
        p = np.exp(logits - logits.max())
        p /= p.sum()
        i = int(np.argmax(p))
        return EMOTIONS[i], float(p[i])

    def enroll(self, img: np.ndarray, name: str) -> dict:
        """Enroll the largest face in ``img`` under ``name``."""
        if self.detector is None or self.recognizer is None:
            raise RuntimeError("face models not installed (run: remote-monitor models)")
        faces, _ = self._detect(img)
        if len(faces) == 0:
            raise ValueError("no face visible in the current frame")
        row = max(faces, key=lambda r: r[2] * r[3])
        aligned = self.recognizer.alignCrop(img, row)
        feat = self.recognizer.feature(aligned)
        return self.gallery.add(name, feat, aligned)


def download_models(models_dir: Path) -> None:
    import urllib.request

    models_dir.mkdir(parents=True, exist_ok=True)
    for name, url in MODEL_URLS.items():
        target = models_dir / name
        if target.exists() and target.stat().st_size > 1000:
            print(f"ok      {name}")
            continue
        print(f"fetch   {name} ...", flush=True)
        tmp = target.with_suffix(".part")
        urllib.request.urlretrieve(url, tmp)
        tmp.replace(target)
        print(f"saved   {target} ({target.stat().st_size // 1024} KiB)")
