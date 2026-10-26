"""OCR wrapper around RapidOCR (onnxruntime, offline)."""
from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np
from PIL import Image

# Keep the background OCR from eating every core: onnxruntime defaults to one thread
# per CPU, which made this tool burn ~2 full cores while idling.
os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("ORT_NUM_THREADS", "2")

THREADS = int(os.environ.get("ORT_NUM_THREADS", "2"))


@dataclass
class Line:
    text: str
    x: float
    y: float
    w: float
    h: float
    score: float

    @property
    def x2(self) -> float:
        return self.x + self.w

    @property
    def yc(self) -> float:
        return self.y + self.h / 2

    def __repr__(self) -> str:  # pragma: no cover
        return f"Line({self.text!r}, x={self.x:.0f}, y={self.y:.0f}, h={self.h:.0f})"


def _limit_ort_threads() -> None:
    try:
        import onnxruntime as ort
    except Exception:  # pragma: no cover
        return
    original = ort.InferenceSession.__init__

    def patched(self, *args, **kwargs):
        opts = kwargs.get("sess_options") or ort.SessionOptions()
        opts.intra_op_num_threads = THREADS
        opts.inter_op_num_threads = 1
        kwargs["sess_options"] = opts
        return original(self, *args, **kwargs)

    if getattr(ort.InferenceSession.__init__, "_wf_patched", False):
        return
    patched._wf_patched = True  # type: ignore[attr-defined]
    ort.InferenceSession.__init__ = patched


class Ocr:
    def __init__(self) -> None:
        _limit_ort_threads()
        from rapidocr_onnxruntime import RapidOCR
        self._engine = RapidOCR()

    def lines(self, img: Image.Image, text_score: float | None = None) -> list[Line]:
        arr = np.array(img.convert("RGB"))[:, :, ::-1]  # RGB -> BGR
        kwargs = {"use_cls": False}
        if text_score is not None:
            kwargs["text_score"] = text_score
        res, _elapse = self._engine(arr, **kwargs)
        out: list[Line] = []
        for item in res or []:
            box, text, score = item
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            out.append(Line(text=str(text), x=float(min(xs)), y=float(min(ys)),
                            w=float(max(xs) - min(xs)), h=float(max(ys) - min(ys)),
                            score=float(score)))
        out.sort(key=lambda l: (round(l.y / 8), l.x))
        return out
