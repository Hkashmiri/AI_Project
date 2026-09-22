"""
vision_engine.py
Focus-SENSE — Posture & Eye-Strain Prevention Tool
Core computer vision engine using OpenCV + MediaPipe Face Landmarker.
"""

import cv2
import mediapipe as mp
import numpy as np
from dataclasses import dataclass
from typing import Optional
import threading
import math
import time
import os
import sys
import urllib.request


# ──────────────────────────────────────────────
#  Data Structures
# ──────────────────────────────────────────────

@dataclass
class CalibrationData:
    """Stores the user's 'Optimal Position' baseline."""
    eye_distance_px: float = 0.0        # pixel distance between inner-eye landmarks
    eye_y_normalized: float = 0.0       # average normalised Y of eyes (0-1)
    is_calibrated: bool = False


@dataclass
class FrameResult:
    """Snapshot of a single processed frame."""
    face_detected: bool = False
    eye_distance_px: float = 0.0
    eye_y_normalized: float = 0.0

    # Derived status strings: "optimal" | "warning" | "critical"
    distance_status: str = "no_face"
    posture_status: str = "no_face"

    # Overall composite status
    overall_status: str = "no_face"

    # Focus score 0-100
    focus_score: float = 100.0

    # Raw annotated frame (optional — only set when preview is enabled)
    annotated_frame: Optional[np.ndarray] = None


_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "face_landmarker/face_landmarker/float16/1/face_landmarker.task"
)
_MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
_MODEL_PATH = os.path.join(_MODEL_DIR, "face_landmarker.task")


def _ensure_face_landmarker_model() -> str:
    """Download the Face Landmarker .task model if it is not already present."""
    if os.path.isfile(_MODEL_PATH) and os.path.getsize(_MODEL_PATH) > 0:
        return _MODEL_PATH
    os.makedirs(_MODEL_DIR, exist_ok=True)
    urllib.request.urlretrieve(_MODEL_URL, _MODEL_PATH)
    return _MODEL_PATH


# ──────────────────────────────────────────────
#  VisionEngine
# ──────────────────────────────────────────────

class VisionEngine:
    """
    Encapsulates all webcam + MediaPipe logic.
    Designed to be called from a background thread.
    """

    # MediaPipe landmark indices (Aligned with README)
    _LEFT_INNER_EYE  = 33
    _RIGHT_INNER_EYE = 263
    _NOSE_TIP        = 1

    # How many frames to average for calibration (reduced for speed)
    _CALIB_FRAMES = 10

    def __init__(self) -> None:
        self._cap: Optional[cv2.VideoCapture] = None
        self._lock = threading.RLock()  # Reentrant lock to prevent deadlocks
        self._face_landmarker = None
        self._frame_timestamp_ms = 0
        self._setup_face_mesh()
        self.calibration = CalibrationData()

        # Sensitivity multiplier (1.0 = default, >1 = stricter)
        self.sensitivity: float = 1.0

        # Thresholds — set during calibration
        self._dist_warning_factor  = 0.85   # < 85 % of baseline → too close
        self._dist_critical_factor = 0.65
        self._posture_warning_delta  = 0.08  # downward shift in eye Y (further relaxed)
        self._posture_critical_delta = 0.15  # (further relaxed)

        # Internal state
        self._is_calibrating = False
        self._stable_frames  = 0
        self._last_face_pos  = None
        self._calib_buffer   = []  # Buffer for collecting data during calibration
        self._is_collecting  = False

    def _setup_face_mesh(self) -> None:
        """Initialize or reset the MediaPipe FaceLandmarker object."""
        with self._lock:
            try:
                if self._face_landmarker is not None:
                    self._face_landmarker.close()
            except Exception:
                pass

            model_path = _ensure_face_landmarker_model()
            # Force CPU — Metal GPU init can crash on macOS (DrishtiMetalHelper).
            options = mp.tasks.vision.FaceLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(
                    model_asset_path=model_path,
                    delegate=mp.tasks.BaseOptions.Delegate.CPU,
                ),
                running_mode=mp.tasks.vision.RunningMode.VIDEO,
                num_faces=1,
                min_face_detection_confidence=0.6,
                min_face_presence_confidence=0.6,
                min_tracking_confidence=0.6,
            )
            self._face_landmarker = mp.tasks.vision.FaceLandmarker.create_from_options(
                options
            )
            self._frame_timestamp_ms = 0

    # ── Camera lifecycle ──────────────────────

    def open_camera(self, index: int = 0) -> bool:
        """Open the webcam. Returns True on success."""
        # CAP_DSHOW is Windows-only; use the default backend elsewhere.
        if sys.platform == "win32":
            self._cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        else:
            self._cap = cv2.VideoCapture(index)

        if not self._cap.isOpened():
            # Fallback: try without an explicit backend on Windows too
            self._cap = cv2.VideoCapture(index)
        if not self._cap.isOpened():
            self._cap = None
            return False
        # Prefer 640×480 for performance
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        return True

    def release_camera(self) -> None:
        """Release the webcam and MediaPipe resources."""
        if self._cap and self._cap.isOpened():
            self._cap.release()
        self._cap = None
        with self._lock:
            try:
                if self._face_landmarker is not None:
                    self._face_landmarker.close()
            except Exception:
                pass
            self._face_landmarker = None

    # ── Calibration ───────────────────────────

    def calibrate(self) -> bool:
        """Collect calibration data from the active stream."""
        if self._is_calibrating:
            return False
        self._is_calibrating = True
        self._calib_buffer = []
        self._is_collecting = True

        try:
            # Wait for 1.5 seconds while the active stream populates the buffer
            start_t = time.time()
            while len(self._calib_buffer) < self._CALIB_FRAMES and (time.time() - start_t) < 3.0:
                time.sleep(0.1)

            self._is_collecting = False

            if len(self._calib_buffer) < 5:
                return False

            dists = [d[0] for d in self._calib_buffer]
            ys    = [d[1] for d in self._calib_buffer]

            self.calibration.eye_distance_px    = float(np.mean(dists))
            self.calibration.eye_y_normalized   = float(np.mean(ys))
            self.calibration.is_calibrated      = True
            return True
        finally:
            self._is_collecting = False
            self._is_calibrating = False

    # ── Per-frame processing ──────────────────

    def process_frame(self, draw: bool = False) -> FrameResult:
        """
        Grab the next frame from the camera and analyse it.
        Call this in a tight loop from the background thread.
        """
        if not self._cap or not self._cap.isOpened():
            return FrameResult()

        success, frame = self._cap.read()
        if not success or frame is None:
            return FrameResult()

        return self._process_single_frame(frame, draw=draw)

    def _process_single_frame(self, frame, draw: bool) -> FrameResult:
        with self._lock:
            # Mirror the frame for a more natural preview
            frame = cv2.flip(frame, 1)
            h, w = frame.shape[:2]

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        # MediaPipe Image requires a contiguous array
        rgb = np.ascontiguousarray(rgb)

        try:
            with self._lock:
                self._frame_timestamp_ms += 33  # ~30 fps clock for VIDEO mode
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                mp_result = self._face_landmarker.detect_for_video(
                    mp_image, self._frame_timestamp_ms
                )
        except Exception:
            # If MediaPipe fails (timestamp mismatch), reset it and skip frame
            self._setup_face_mesh()
            return FrameResult()

        result = FrameResult()

        if not mp_result.face_landmarks:
            result.overall_status = "no_face"
            if draw:
                result.annotated_frame = frame
            return result

        landmarks = mp_result.face_landmarks[0]

        # ── 3D Eye distance (Rotation Immune) ────────
        # Using 3D Euclidean distance (x,y,z) ensures the distance
        # stays constant even if you rotate your head.
        l_pt = np.array([landmarks[self._LEFT_INNER_EYE].x,
                         landmarks[self._LEFT_INNER_EYE].y,
                         landmarks[self._LEFT_INNER_EYE].z])
        r_pt = np.array([landmarks[self._RIGHT_INNER_EYE].x,
                         landmarks[self._RIGHT_INNER_EYE].y,
                         landmarks[self._RIGHT_INNER_EYE].z])

        # Scale by width to keep the values in a familiar range
        eye_dist = float(np.linalg.norm(l_pt - r_pt)) * w

        # ── Posture Tracking (Nose Tip) ───────────────
        eye_y = landmarks[self._NOSE_TIP].y

        result.face_detected      = True
        result.eye_distance_px    = eye_dist
        result.eye_y_normalized   = eye_y

        # ── Calibration Buffer ────────────────────────
        if self._is_collecting:
            self._calib_buffer.append((eye_dist, eye_y))

        # ── Evaluate against calibration ─────────────
        if self.calibration.is_calibrated:
            result.distance_status = self._eval_distance(eye_dist)
            result.posture_status  = self._eval_posture(eye_y)
            result.overall_status  = self._merge_status(
                result.distance_status, result.posture_status
            )
            result.focus_score = self._compute_focus_score(
                result.distance_status, result.posture_status
            )
        else:
            result.overall_status = "uncalibrated"
            result.focus_score    = 100.0

        # ── Optional drawing ──────────────────────────
        if draw:
            result.annotated_frame = self._draw_overlay(
                frame, landmarks, w, h, result
            )

        # ── Auto-Calibration Heuristic ────────────────
        if not self.calibration.is_calibrated and result.face_detected:
            nose = landmarks[self._NOSE_TIP]
            center = (nose.x, nose.y)

            if self._last_face_pos:
                dist = math.sqrt((center[0]-self._last_face_pos[0])**2 +
                                 (center[1]-self._last_face_pos[1])**2)
                if dist < 0.02:  # Very stable
                    self._stable_frames += 1
                else:
                    self._stable_frames = 0
            self._last_face_pos = center

            if self._stable_frames > 100:  # ~5 seconds at 20fps
                self.calibrate()
                self._stable_frames = 0

        return result

    # ── Status evaluation helpers ─────────────

    def _eval_distance(self, eye_dist: float) -> str:
        baseline = self.calibration.eye_distance_px
        s = self.sensitivity
        ratio = eye_dist / baseline if baseline else 1.0

        warn_thresh     = self._dist_warning_factor  ** (1 / s)
        critical_thresh = self._dist_critical_factor ** (1 / s)
        # 1. Too Close (Leaning in)
        if ratio > (1 / critical_thresh): return "critical"
        if ratio > (1 / warn_thresh):     return "warning"

        # 2. Too Far (Moving away)
        if ratio < critical_thresh:       return "critical"
        if ratio < warn_thresh:           return "warning"

        return "normal"

    def _eval_posture(self, eye_y: float) -> str:
        baseline = self.calibration.eye_y_normalized
        s = self.sensitivity
        delta = eye_y - baseline   # positive = head moved down (slumping)

        warn_thresh     = self._posture_warning_delta  / s
        critical_thresh = self._posture_critical_delta / s

        if delta > critical_thresh:
            return "critical"
        if delta > warn_thresh:
            return "warning"
        return "optimal"

    @staticmethod
    def _merge_status(d: str, p: str) -> str:
        priority = {"critical": 2, "warning": 1, "optimal": 0,
                    "no_face": -1, "uncalibrated": -1}
        worst = max(priority.get(d, -1), priority.get(p, -1))
        return {2: "critical", 1: "warning", 0: "optimal"}.get(worst, "no_face")

    @staticmethod
    def _compute_focus_score(d: str, p: str) -> float:
        penalty = {"critical": 40, "warning": 20, "optimal": 0}
        score = 100.0 - penalty.get(d, 0) - penalty.get(p, 0)
        return max(0.0, min(100.0, score))

    # ── Overlay drawing ───────────────────────

    def _draw_overlay(self, frame, landmarks, w, h, result: FrameResult):
        """Draw minimal landmark dots and status text onto the frame."""
        colour_map = {
            "optimal":      (0, 255, 128),
            "warning":      (0, 200, 255),
            "critical":     (0, 60, 255),
            "no_face":      (128, 128, 128),
            "uncalibrated": (200, 200, 200),
        }
        colour = colour_map.get(result.overall_status, (255, 255, 255))

        # Draw key landmarks
        for idx in (self._LEFT_INNER_EYE, self._RIGHT_INNER_EYE, self._NOSE_TIP):
            x = int(landmarks[idx].x * w)
            y = int(landmarks[idx].y * h)
            cv2.circle(frame, (x, y), 4, colour, -1)

        # Status label
        label = result.overall_status.upper()
        cv2.putText(frame, label, (12, 28),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, colour, 2, cv2.LINE_AA)

        # Focus score
        score_txt = f"FOCUS {result.focus_score:.0f}%"
        cv2.putText(frame, score_txt, (12, 56),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, colour, 1, cv2.LINE_AA)

        return frame
