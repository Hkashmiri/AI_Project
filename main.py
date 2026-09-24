"""
main.py
Focus-SENSE — Posture & Eye-Strain Prevention Tool
Ultra-minimalist GUI with background camera thread.
"""

import threading
import time
import queue
import tkinter as tk
from tkinter import messagebox
from typing import Optional
import json
import os
import sys
import subprocess
from PIL import Image, ImageDraw
import pystray

import customtkinter as ctk
from plyer import notification

from vision_engine import VisionEngine, FrameResult


def _beep(frequency: int = 1000, duration_ms: int = 200) -> None:
    """Cross-platform alert tone (winsound is Windows-only)."""
    try:
        if sys.platform == "win32":
            import winsound
            winsound.Beep(frequency, duration_ms)
        elif sys.platform == "darwin":
            # afplay is available on macOS; pick a short system sound
            sound = "/System/Library/Sounds/Tink.aiff"
            if frequency >= 1500:
                sound = "/System/Library/Sounds/Sosumi.aiff"
            subprocess.Popen(
                ["afplay", sound],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            # Linux / other: terminal bell
            print("\a", end="", flush=True)
    except Exception:
        pass


# ──────────────────────────────────────────────
#  Design tokens — pure black + white high contrast
# ──────────────────────────────────────────────

BG          = "#050505"
FG          = "#FFFFFF"
ACCENT      = "#00F2FF"
DIM         = "#1A1A1A"
STATUS_OK   = "#00FF9D"
STATUS_WARN = "#FFB800"
STATUS_CRIT = "#FF3D3D"
STATUS_NONE = "#333333"

# Modern Typography
FONT_FAMILY = "Inter, Segoe UI, Roboto, Helvetica, Arial"
FONT_MONO   = (FONT_FAMILY, 11)
FONT_LABEL  = (FONT_FAMILY, 11, "bold")
FONT_HERO   = (FONT_FAMILY, 52, "bold")
FONT_SUB    = (FONT_FAMILY, 14, "bold")
FONT_SMALL  = (FONT_FAMILY, 10, "bold")


# ──────────────────────────────────────────────
#  Camera Worker Thread
# ──────────────────────────────────────────────

class CameraThread(threading.Thread):
    """
    Runs continuously in the background.
    Puts FrameResult objects onto result_queue for the GUI thread to consume.
    Uses an Event for clean shutdown.
    """

    def __init__(self, engine: VisionEngine, result_queue: queue.Queue) -> None:
        super().__init__(daemon=True, name="CameraThread")
        self.engine       = engine
        self.result_queue = result_queue
        self._stop_event  = threading.Event()
        self._pause_event = threading.Event()   # set = running, clear = paused

    def run(self) -> None:
        self._pause_event.set()   # start in running state
        while not self._stop_event.is_set():
            self._pause_event.wait()            # block if paused
            try:
                result = self.engine.process_frame(draw=False)
            except Exception:
                time.sleep(0.05)
                continue                        # skip bad frame, keep thread alive
            # Never let the queue grow stale — keep only the freshest result
            try:
                self.result_queue.get_nowait()
            except queue.Empty:
                pass
            self.result_queue.put(result)
            time.sleep(0.05)                    # ~20 fps polling

    def pause(self)  -> None: self._pause_event.clear()
    def resume(self) -> None: self._pause_event.set()
    def stop(self)   -> None: self._stop_event.set(); self._pause_event.set()


# ──────────────────────────────────────────────
#  Persistence Managers
# ──────────────────────────────────────────────

class SettingsManager:
    def __init__(self, path="settings.json"):
        self.path = path
        self.defaults = {
            "use_sound": True, "use_banner": True,
            "use_flash": True, "use_notif": True,
            "sensitivity": 1.0,
            "pomo_time": 25 * 60,
            "pomo_mode": "WORK",
            "pomo_active": False
        }
        self.data = self.defaults.copy()
        self.load()

    def load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, 'r') as f:
                    self.data.update(json.load(f))
            except: pass

    def save(self, key, value):
        self.data[key] = value
        try:
            with open(self.path, 'w') as f:
                json.dump(self.data, f)
        except: pass


class AnalyticsManager:
    def __init__(self, path="history.json"):
        self.path = path
        self.history = [] # list of [timestamp, score]
        self.load()

    def load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, 'r') as f:
                    self.history = json.load(f)
            except: pass

    def log(self, score):
        self.history.append([int(time.time()), round(score, 1)])
        if len(self.history) > 2000: self.history = self.history[-2000:]
        try:
            with open(self.path, 'w') as f:
                json.dump(self.history, f)
        except: pass


class TrayIconManager:
    def __init__(self, app):
        self.app = app
        self.icon = pystray.Icon(
            "Focus-SENSE",
            self._create_image(),
            "Focus-SENSE",
            menu=pystray.Menu(
                pystray.MenuItem("Restore", self._on_show, default=True),
                pystray.MenuItem("Exit", self._on_exit)
            )
        )
        # Start the icon immediately in the background
        threading.Thread(target=self.icon.run, daemon=True).start()

    def _create_image(self):
        # Simple minimalist icon: white circle on black
        img = Image.new('RGB', (64, 64), color='black')
        draw = ImageDraw.Draw(img)
        draw.ellipse([16, 16, 48, 48], fill='white')
        return img

    def _on_show(self, icon, item):
        # Bring window back without stopping the icon
        self.app.after(0, self.app.deiconify)
        self.app.after(0, lambda: self.app.state('normal'))

    def _on_exit(self, icon, item):
        self.icon.stop()
        self.app.after(0, self.app._on_close)


# ──────────────────────────────────────────────
#  Floating Alert Banner
# ──────────────────────────────────────────────

class PostureAlertBanner(ctk.CTkToplevel):
    """A large, always-on-top window that appears at the top of the screen."""
    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.95)
        self.configure(fg_color="#FF0000") # Pure aggressive red

        # Dimensions and positioning (top center) - Much larger
        w, h = 640, 90
        screen_w = self.winfo_screenwidth()
        x = (screen_w // 2) - (w // 2)
        y = 30
        self.geometry(f"{w}x{h}+{x}+{y}")

        self.label = ctk.CTkLabel(
            self, text="🚨  FIX YOUR POSTURE  🚨",
            font=("Courier New", 28, "bold"),
            text_color="#FFFFFF"
        )
        self.label.pack(expand=True, pady=10)
        
        # Start hidden
        self.withdraw()

    def show(self):
        self.deiconify()
        self.lift()

    def hide(self):
        self.withdraw()


class FullscreenFlash(ctk.CTkToplevel):
    """A fullscreen window that flashes red momentarily."""
    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.0)
        self.configure(fg_color="#FF0000")
        
        # Full screen dimensions
        w = self.winfo_screenwidth()
        h = self.winfo_screenheight()
        self.geometry(f"{w}x{h}+0+0")
        self.withdraw()

    def flash(self):
        """Show the red overlay briefly then hide."""
        self.deiconify()
        self.attributes("-alpha", 0.4) # Semi-transparent flash
        self.after(250, lambda: self.withdraw())


# ──────────────────────────────────────────────
#  Main Application Window
# ──────────────────────────────────────────────

class FocusSenseApp(ctk.CTk):

    # Notification throttle: don't spam more than once per N seconds
    _NOTIF_COOLDOWN_S = 30

    def __init__(self) -> None:
        super().__init__()

        # ── Window setup ─────────────────────
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.title("FOCUS-SENSE")
        self.geometry("520x720")
        self.resizable(False, False)
        self.configure(fg_color=BG)

        # ── State ────────────────────────────
        self._engine         = VisionEngine()
        self._result_queue: queue.Queue[FrameResult] = queue.Queue(maxsize=1)
        self._camera_thread: Optional[CameraThread]  = None
        self._is_running     = False
        self._is_calibrating = False
        self._last_notif_ts  = 0.0
        self._session_start  = 0.0

        # ── Persistence ───────────────────────
        self._settings = SettingsManager()
        self._analytics = AnalyticsManager()
        self._score_history: list[float] = []

        # ── Alert Preferences ─────────────────
        s_data = self._settings.data
        self._pref_sound  = tk.BooleanVar(value=s_data["use_sound"])
        self._pref_banner = tk.BooleanVar(value=s_data["use_banner"])
        self._pref_flash  = tk.BooleanVar(value=s_data["use_flash"])
        self._pref_notif  = tk.BooleanVar(value=s_data["use_notif"])

        # Add traces to save on change
        for var, key in [
            (self._pref_sound, "use_sound"),
            (self._pref_banner, "use_banner"),
            (self._pref_flash, "use_flash"),
            (self._pref_notif, "use_notif")
        ]:
            var.trace_add("write", lambda *a, k=key, v=var: self._settings.save(k, v.get()))

        # ── Alert state ───────────────────────
        self._alert_banner = PostureAlertBanner(self)
        self._flash_screen = FullscreenFlash(self)
        #self._tray_manager = TrayIconManager(self)
        self._was_critical = False
        self._pulse_val    = 0.0      # for "LIVE" lightning effect
        self._pulse_dir    = 1

        # ── Pomodoro state (Restored from settings) ────
        self._pomo_time    = s_data.get("pomo_time", 25 * 60)
        self._pomo_active  = s_data.get("pomo_active", False)
        self._pomo_mode    = s_data.get("pomo_mode", "WORK")

        # ── Build UI ─────────────────────────
        self._build_ui()
        self._update_loop()

        # ── Keyboard Shortcuts ────────────────
        self.bind("<KeyPress-c>", lambda e: self._run_calibration())
        self.bind("<KeyPress-C>", lambda e: self._run_calibration())
        self.bind("<space>",      lambda e: self._run_calibration())

        # Clean shutdown on window close
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Unmap>", self._on_minimize)

    # ── UI construction ───────────────────────

    def _build_ui(self) -> None:
        pad = {"padx": 32}

        # ── Layout: Sidebar + Main ──────────────────────────────────────────
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._sidebar = ctk.CTkFrame(self, width=72, corner_radius=0, fg_color="#0A0A0A")
        self._sidebar.grid(row=0, column=0, sticky="nsew")
        self._sidebar.grid_propagate(False)

        self._btn_dash = self._nav_btn(self._sidebar, "▤", "dashboard")
        self._btn_dash.pack(pady=(40, 10))
        
        self._btn_anal = self._nav_btn(self._sidebar, "📈", "analytics")
        self._btn_anal.pack(pady=20)

        self._btn_sett = self._nav_btn(self._sidebar, "⚙", "settings")
        self._btn_sett.pack(pady=20)

        # Active indicator (neon line)
        self._nav_indicator = ctk.CTkFrame(self._sidebar, width=3, height=24, fg_color=ACCENT)
        self._nav_indicator.place(x=0, y=40)

        self._main_content = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        self._main_content.grid(row=0, column=1, sticky="nsew")

        # ── Header ───────────────────────────────────────────────────────────
        header = ctk.CTkFrame(self._main_content, fg_color=BG, corner_radius=0)
        header.pack(fill="x", padx=32, pady=(32, 0))

        ctk.CTkLabel(
            header, text="FOCUS-SENSE",
            font=("Courier New", 22, "bold"),
            text_color=FG,
        ).pack(side="left")

        self._session_label = ctk.CTkLabel(
            header, text="──:──",
            font=FONT_SMALL, text_color=DIM,
        )
        self._session_label.pack(side="right", pady=(6, 0))

        # ── Content Frames ───────────────────────────────────────────────────
        self._dash_frame = ctk.CTkFrame(self._main_content, fg_color=BG, corner_radius=0)
        self._anal_frame = ctk.CTkFrame(self._main_content, fg_color=BG, corner_radius=0)
        self._sett_frame = ctk.CTkFrame(self._main_content, fg_color=BG, corner_radius=0)

        # ── DASHBOARD PAGE ───────────────────────────────────────────────────
        dash = self._dash_frame
        dash.pack(fill="both", expand=True) # default page
        
        self._hdivider(dash, pady=(16, 16))

        self._status_label = ctk.CTkLabel(
            dash, text="●  INACTIVE",
            font=("Courier New", 28, "bold"),
            text_color=DIM,
        )
        self._status_label.pack(**pad, pady=(0, 4))

        self._status_detail = ctk.CTkLabel(
            dash, text="Start the camera to begin monitoring.",
            font=FONT_SMALL, text_color=DIM,
        )
        self._status_detail.pack(**pad)

        self._hdivider(dash, pady=(20, 20))

        ctk.CTkLabel(dash, text="FOCUS SCORE", font=FONT_LABEL,
                     text_color=DIM).pack(**pad)

        self._score_label = ctk.CTkLabel(
            dash, text="---",
            font=FONT_HERO, text_color=FG,
        )
        self._score_label.pack(**pad, pady=(0, 4))

        self._score_bar = ctk.CTkProgressBar(
            dash, width=400, height=4,
            corner_radius=0,
            fg_color=DIM, progress_color=FG,
        )
        self._score_bar.set(0)
        self._score_bar.pack(**pad, pady=(0, 2))

        ctk.CTkLabel(dash, text="rolling average · last 60 s",
                     font=FONT_SMALL, text_color=DIM).pack(**pad, pady=(0, 4))

        self._hdivider(dash, pady=(20, 20))

        tiles_row = ctk.CTkFrame(dash, fg_color=BG)
        tiles_row.pack(fill="x", padx=32)

        self._dist_tile   = self._metric_tile(tiles_row, "DISTANCE")
        self._posture_tile = self._metric_tile(tiles_row, "POSTURE")
        self._dist_tile.pack(side="left", expand=True, fill="x", padx=(0, 8))
        self._posture_tile.pack(side="left", expand=True, fill="x")

        self._hdivider(dash, pady=(20, 20))

        # ── POMODORO TIMER ───────────────────
        self._pomo_frame = ctk.CTkFrame(dash, fg_color="#080808", corner_radius=24, border_width=1, border_color=DIM)
        self._pomo_frame.pack(fill="x", padx=32, pady=(0, 10))
        
        pomo_inner = ctk.CTkFrame(self._pomo_frame, fg_color="transparent")
        pomo_inner.pack(pady=10)

        self._pomo_label = ctk.CTkLabel(pomo_inner, text="25:00", font=("Courier New", 28, "bold"), text_color=FG)
        self._pomo_label.pack(side="left", padx=20)
        
        self._pomo_btn = ctk.CTkButton(
            pomo_inner, text="START TIMER", width=120, height=36,
            font=(FONT_FAMILY, 12, "bold"),
            fg_color="#1A1A1A", text_color=FG, hover_color="#2A2A2A",
            corner_radius=18, command=self._toggle_pomo
        )
        self._pomo_btn.pack(side="left", padx=10)
        
        self._pomo_status = ctk.CTkLabel(dash, text="SMART PAUSE: ACTIVE", font=FONT_SMALL, text_color=DIM)
        self._pomo_status.pack(pady=(0, 10))

        self._hdivider(dash, pady=(10, 20))

        ctrl_frame = ctk.CTkFrame(dash, fg_color=BG)
        ctrl_frame.pack(fill="x", padx=32)

        self._start_btn = ctk.CTkButton(
            ctrl_frame, text="START",
            font=(FONT_FAMILY, 14, "bold"),
            fg_color=FG, text_color=BG,
            hover_color=ACCENT, corner_radius=24, height=48,
            command=self._toggle_camera,
        )
        self._start_btn.pack(side="left", expand=True, fill="x", padx=(0, 8))

        self._calib_btn = ctk.CTkButton(
            ctrl_frame, text="CALIBRATE",
            font=(FONT_FAMILY, 14, "bold"),
            fg_color="#0C0C0C", text_color=FG,
            hover_color=ACCENT, border_width=1, border_color="#1A1A1A",
            corner_radius=24, height=48,
            command=self._run_calibration,
            state="disabled",
        )
        self._calib_btn.pack(side="left", expand=True, fill="x", padx=(0, 8))

        self._reset_btn = ctk.CTkButton(
            ctrl_frame, text="↺", width=48, height=48,
            font=(FONT_FAMILY, 18, "bold"),
            fg_color="#0C0C0C", text_color=DIM,
            hover_color="#FFFFFF", border_width=1, border_color="#1A1A1A",
            corner_radius=24, command=self._reset_calibration
        )
        self._reset_btn.pack(side="left")
        
        # Hover effect: Show "RESET" when cursor enters
        def _on_reset_enter(e):
            self._reset_btn.configure(text="RESET", font=(FONT_FAMILY, 11, "bold"), width=72, text_color=FG)
            self._status_detail.configure(text="Click to clear your current calibration baseline.")
            
        def _on_reset_leave(e):
            self._reset_btn.configure(text="↺", font=(FONT_FAMILY, 18, "bold"), width=48, text_color=DIM)
            # Restore previous detail if possible, or just clear
            if not self._engine.calibration.is_calibrated:
                self._status_detail.configure(text="Calibration cleared. Recalibrate to start.")

        self._reset_btn.bind("<Enter>", _on_reset_enter)
        self._reset_btn.bind("<Leave>", _on_reset_leave)

        ctk.CTkLabel(
            dash, text="GUIDE: Sit in your natural working position, look straight at the camera, then click CALIBRATE.",
            font=(FONT_FAMILY, 9, "italic"), text_color=DIM, wraplength=400
        ).pack(pady=(12, 0))

        # ── ANALYTICS PAGE ───────────────────────────────────────────────────
        anal = self._anal_frame
        self._hdivider(anal, pady=(16, 24))
        
        ctk.CTkLabel(anal, text="FOCUS HISTORY", font=FONT_LABEL, text_color=DIM).pack(padx=32, anchor="w")
        
        self._canvas = tk.Canvas(anal, bg="#050505", highlightthickness=0, height=220)
        self._canvas.pack(fill="x", padx=32, pady=24)
        
        self._stat_label = ctk.CTkLabel(anal, text="Average Focus: --%", font=FONT_SMALL, text_color=DIM)
        self._stat_label.pack(padx=32, anchor="w")

        # ── SETTINGS PAGE ────────────────────────────────────────────────────
        sett = self._sett_frame
        
        self._hdivider(sett, pady=(16, 24))

        ctk.CTkLabel(sett, text="ALERT PREFERENCES",
                     font=FONT_LABEL, text_color=DIM).pack(**pad, anchor="w")

        self._switch(sett, "Audio Alarm (Beep)", self._pref_sound).pack(**pad, pady=(12, 8), anchor="w")
        self._switch(sett, "Floating Banner", self._pref_banner).pack(**pad, pady=8, anchor="w")
        self._switch(sett, "Fullscreen Flash", self._pref_flash).pack(**pad, pady=8, anchor="w")
        self._switch(sett, "System Notifications", self._pref_notif).pack(**pad, pady=8, anchor="w")

        self._hdivider(sett, pady=(20, 20))

        ctk.CTkLabel(sett, text="SENSITIVITY",
                     font=FONT_LABEL, text_color=DIM).pack(**pad, anchor="w")

        sens_row = ctk.CTkFrame(sett, fg_color=BG)
        sens_row.pack(fill="x", padx=32, pady=(8, 0))

        self._sens_value_label = ctk.CTkLabel(
            sens_row, text="1.0×",
            font=FONT_LABEL, text_color=FG,
        )
        self._sens_value_label.pack(side="right")

        self._sens_slider = ctk.CTkSlider(
            sett, from_=0.5, to=2.0,
            number_of_steps=15,
            fg_color=DIM, progress_color=FG,
            button_color=FG, button_hover_color="#CCCCCC",
            command=self._on_sensitivity_change,
        )
        self._sens_slider.set(self._settings.data["sensitivity"])
        self._on_sensitivity_change(self._settings.data["sensitivity"])
        self._sens_slider.pack(fill="x", padx=32, pady=(4, 0))

        # ── Footer ───────────────────────────────────────────────────────────
        ctk.CTkLabel(
            self._main_content,
            text="v1.2  ·  Press 'C' to calibrate anytime",
            font=FONT_SMALL, text_color=DIM,
        ).pack(side="bottom", pady=20)

        # Init nav state
        self._show_page("dashboard")

    def _nav_btn(self, parent, text, page):
        """Pill-shaped navigation icon button."""
        return ctk.CTkButton(
            parent, text=text, width=44, height=44,
            font=(FONT_FAMILY, 18, "bold"),
            fg_color="transparent", text_color=DIM,
            hover_color="#1A1A1A", corner_radius=22,
            command=lambda: self._show_page(page)
        )

    def _show_page(self, page):
        """Switch between dashboard, analytics and settings frames."""
        # Reset all
        self._dash_frame.pack_forget()
        self._anal_frame.pack_forget()
        self._sett_frame.pack_forget()
        self._btn_dash.configure(text_color=DIM, fg_color="transparent")
        self._btn_anal.configure(text_color=DIM, fg_color="transparent")
        self._btn_sett.configure(text_color=DIM, fg_color="transparent")

        # Update indicator position with a smooth animation feel (immediate for now, but aligned)
        y_pos = {"dashboard": 40, "analytics": 104, "settings": 168}.get(page, 40)
        self._nav_indicator.place(x=0, y=y_pos)

        if page == "dashboard":
            self._dash_frame.pack(fill="both", expand=True)
            self._btn_dash.configure(text_color=FG, fg_color="#1A1A1A")
        elif page == "analytics":
            self._anal_frame.pack(fill="both", expand=True)
            self._btn_anal.configure(text_color=FG, fg_color="#1A1A1A")
            self._draw_history_graph()
        else:
            self._sett_frame.pack(fill="both", expand=True)
            self._btn_sett.configure(text_color=FG, fg_color="#1A1A1A")

    def _on_minimize(self, event=None):
        """When minimized, withdraw the window (it stays alive in tray)."""
        if self.state() == 'iconic':
            self.withdraw()

    def _draw_history_graph(self):
        """Draw a minimalist focus graph on the canvas."""
        self._canvas.delete("all")
        history = self._analytics.history
        if not history:
            self._canvas.create_text(228, 110, text="No data yet. Start monitoring to log focus.", 
                                     fill=DIM, font=FONT_SMALL)
            return

        w, h = 456, 220
        pad = 20
        points = history[-50:] # last 50 points
        
        # Calculate stats
        avg = sum(p[1] for p in points) / len(points)
        self._stat_label.configure(text=f"Last 50 points average focus: {avg:.1f}%")

        # Scale
        x_step = (w - 2 * pad) / (len(points) - 1) if len(points) > 1 else 0
        
        pts = []
        for i, (ts, score) in enumerate(points):
            x = pad + i * x_step
            y = h - pad - (score / 100 * (h - 2 * pad))
            pts.append((x, y))

        # Flatten for tkinter
        flat_pts = [coord for pt in pts for coord in pt]

        # Draw grid & Y-axis labels
        for i in range(0, 101, 25):
            gy = h - pad - (i / 100 * (h - 2 * pad))
            # Grid line
            self._canvas.create_line(pad, gy, w-pad, gy, fill="#111111", dash=(2, 4))
            # Percentage label
            self._canvas.create_text(pad - 10, gy, text=f"{i}%", fill=DIM, font=("Courier New", 10), anchor="e")

        # Draw line
        if len(flat_pts) >= 4:
            self._canvas.create_line(flat_pts, fill=FG, width=2, smooth=True)
        
        # Draw gradient area
        if len(pts) > 1:
            poly_pts = [pad, h-pad] + flat_pts + [pts[-1][0], h-pad]
            self._canvas.create_polygon(poly_pts, fill="#0F0F0F")

        # Draw X-axis time labels
        if len(points) >= 2:
            t_start = time.strftime("%H:%M:%S", time.localtime(points[0][0]))
            t_end   = time.strftime("%H:%M:%S", time.localtime(points[-1][0]))
            self._canvas.create_text(pad, h - pad + 15, text=t_start, fill=DIM, font=("Courier New", 10), anchor="nw")
            self._canvas.create_text(w - pad, h - pad + 15, text=t_end, fill=DIM, font=("Courier New", 10), anchor="ne")

    def _hdivider(self, parent=None, pady=(8, 8)):
        """Render a 1-px horizontal rule."""
        target = parent if parent else self
        line = tk.Frame(target, bg=DIM, height=1)
        line.pack(fill="x", padx=32, pady=pady)

    def _switch(self, parent, text, variable):
        """Helper to create a minimalist switch."""
        return ctk.CTkSwitch(
            parent, text=text, variable=variable,
            font=FONT_LABEL, text_color=FG,
            progress_color=DIM, fg_color="#111111",
            button_color=FG, button_hover_color="#CCCCCC"
        )

    def _metric_tile(self, parent, label_text: str) -> ctk.CTkFrame:
        """Create a premium metric display tile with modern typography."""
        tile = ctk.CTkFrame(
            parent,
            fg_color="#0C0C0C",
            corner_radius=16,
            border_width=1, border_color="#1A1A1A",
        )
        ctk.CTkLabel(tile, text=label_text,
                     font=FONT_SMALL, text_color=DIM).pack(pady=(12, 2))
        val_label = ctk.CTkLabel(tile, text="---",
                                 font=(FONT_FAMILY, 20, "bold"),
                                 text_color=FG)
        val_label.pack(pady=(0, 12))
        # Store for updates
        tile._val_label = val_label
        return tile

    # ── Camera control ────────────────────────

    def _toggle_camera(self) -> None:
        if self._is_running:
            self._stop_camera()
        else:
            self._start_camera()

    def _start_camera(self) -> None:
        if not self._engine.open_camera(0):
            messagebox.showerror(
                "Camera Error",
                "Could not open webcam.\n"
                "Check that no other app is using it, then try again."
            )
            return

        self._is_running   = True
        self._session_start = time.time()
        self._score_history.clear()

        self._camera_thread = CameraThread(self._engine, self._result_queue)
        self._camera_thread.start()

        self._start_btn.configure(text="STOP", fg_color="#222222",
                                  text_color=FG, hover_color=ACCENT,
                                  border_width=1, border_color=DIM)
        self._calib_btn.configure(state="normal")
        self._status_label.configure(text="●  LIVE", text_color=STATUS_NONE)
        self._status_detail.configure(
            text="Calibrate for accurate posture detection."
        )

    def _stop_camera(self) -> None:
        if self._camera_thread:
            self._camera_thread.stop()
            self._camera_thread.join(timeout=2)
            self._camera_thread = None

        self._engine.release_camera()
        self._is_running = False

        self._start_btn.configure(text="START", fg_color=FG,
                                  text_color=BG, hover_color="#CCCCCC",
                                  border_width=0)
        self._calib_btn.configure(state="disabled")
        self._status_label.configure(text="●  INACTIVE", text_color=DIM)
        self._status_detail.configure(
            text="Start the camera to begin monitoring."
        )
        self._score_label.configure(text="---", text_color=FG)
        self._score_bar.set(0)
        self._dist_tile._val_label.configure(text="---", text_color=FG)
        self._posture_tile._val_label.configure(text="---", text_color=FG)
        self._session_label.configure(text="──:──")

    # ── Calibration ───────────────────────────

    def _reset_calibration(self) -> None:
        """Clear the current calibration baseline."""
        self._engine.calibration.is_calibrated = False
        self._status_label.configure(text="●  UNSET", text_color=DIM)
        self._status_detail.configure(text="Calibration cleared. Recalibrate to start.")
        self._score_label.configure(text="---")
        self._score_bar.set(0)
        self._dist_tile._val_label.configure(text="---")
        self._posture_tile._val_label.configure(text="---")

    def _run_calibration(self) -> None:
        if self._is_calibrating or not self._is_running:
            return

        self._is_calibrating = True

        self._calib_btn.configure(state="disabled", text="CALIBRATING…")
        self._status_label.configure(text="●  CALIBRATING", text_color=STATUS_WARN)
        self._status_detail.configure(
            text="Hold your ideal posture and look straight at the camera."
        )

        def _do_calibrate():
            try:
                success = self._engine.calibrate()
            except Exception:
                success = False
            self.after(0, lambda: self._on_calibration_done(success))

        threading.Thread(target=_do_calibrate, daemon=True,
                         name="CalibThread").start()

    def _on_calibration_done(self, success: bool) -> None:
        self._is_calibrating = False
        # Resume background monitoring

        self._calib_btn.configure(state="normal", text="CALIBRATE")
        if success:
            self._status_label.configure(text="●  CALIBRATED", text_color=STATUS_OK)
            self._status_detail.configure(
                text="Baseline locked. Monitoring active."
            )
        else:
            self._status_label.configure(text="●  CALIB FAILED", text_color=STATUS_CRIT)
            self._status_detail.configure(
                text="No face detected. Ensure good lighting and try again."
            )

    # ── Sensitivity ───────────────────────────

    def _on_sensitivity_change(self, value) -> None:
        try:
            val = float(value)
        except (ValueError, TypeError):
            return
        self._engine.sensitivity = round(val, 2)
        self._sens_value_label.configure(text=f"{val:.1f}×")
        self._settings.save("sensitivity", round(val, 2))

    # ── GUI update loop ───────────────────────

    def _update_loop(self) -> None:
        """Poll the result queue every 50 ms and refresh widgets."""
        if not self.winfo_exists(): return
        
        try:
            result: FrameResult = self._result_queue.get_nowait()
            self._apply_result(result)
        except queue.Empty:
            # Still update status glow if live but no new frame
            if self._is_running and not self._engine.calibration.is_calibrated:
                self._update_live_glow()
        finally:
            self._after_id = self.after(50, self._update_loop)

        # ── Timers update (once per second) ───
        now = time.time()
        if now - getattr(self, "_last_sec_ts", 0) >= 1.0:
            self._last_sec_ts = now
            self._tick_second()

    def _tick_second(self):
        # Session timer
        if self._is_running and self._session_start:
            elapsed = int(time.time() - self._session_start)
            mins, secs = divmod(elapsed, 60)
            self._session_label.configure(text=f"{mins:02d}:{secs:02d}")
        
        # Pomodoro timer
        if self._pomo_active:
            # Smart Pause: only countdown if face is detected
            if getattr(self, "_last_face_detected", False):
                self._pomo_time -= 1
                self._pomo_status.configure(text="TIMER RUNNING", text_color=STATUS_OK)
            else:
                self._pomo_status.configure(text="SMART PAUSE: AWAY", text_color=STATUS_WARN)

            if self._pomo_time <= 0:
                self._on_pomo_complete()
            
            m, s = divmod(self._pomo_time, 60)
            self._pomo_label.configure(text=f"{m:02d}:{s:02d}")

    def _toggle_pomo(self):
        self._pomo_active = not self._pomo_active
        btn_text = "PAUSE" if self._pomo_active else "RESUME"
        self._pomo_btn.configure(text=btn_text)
        if not self._pomo_active:
            self._pomo_status.configure(text="TIMER PAUSED", text_color=DIM)
        
        # Save toggle state
        self._settings.save("pomo_active", self._pomo_active)

    def _on_pomo_complete(self):
        _beep(1000, 800)
        if self._pomo_mode == "WORK":
            messagebox.showinfo("Focus-SENSE", "Work session complete! Take a 5-minute break.")
            self._pomo_mode = "BREAK"
            self._pomo_time = 5 * 60
        else:
            messagebox.showinfo("Focus-SENSE", "Break over! Ready for another work session?")
            self._pomo_mode = "WORK"
            self._pomo_time = 25 * 60
        
        self._pomo_active = False
        self._pomo_btn.configure(text="START TIMER")
        self._pomo_status.configure(text="SESSION COMPLETE", text_color=STATUS_OK)
        
        # Save mode and inactive state
        self._settings.save("pomo_mode", self._pomo_mode)
        self._settings.save("pomo_active", False)

    def _update_live_glow(self) -> None:
        """Pulsing lightning effect for the 'LIVE' status."""
        self._pulse_val += 0.15 * self._pulse_dir
        if self._pulse_val >= 1.0 or self._pulse_val <= 0.0:
            self._pulse_dir *= -1
            self._pulse_val = max(0.0, min(1.0, self._pulse_val))
        
        # Animate from DIM (#444444) to bright FG (#FFFFFF)
        gray = int(68 + (255 - 68) * self._pulse_val)
        color = f"#{gray:02x}{gray:02x}{gray:02x}"
        self._status_label.configure(text_color=color)

    def _apply_result(self, result: FrameResult) -> None:
        """Update all widgets from a FrameResult."""
        status = result.overall_status

        status_text = {
            "optimal":      "●  OPTIMAL",
            "warning":      "●  WARNING",
            "critical":     "●  CRITICAL",
            "no_face":      "●  NO FACE",
            "uncalibrated": "●  LIVE",
        }.get(status, "●  LIVE")

        status_colour = {
            "optimal":      STATUS_OK,
            "warning":      STATUS_WARN,
            "critical":     STATUS_CRIT,
            "no_face":      DIM,
            "uncalibrated": STATUS_NONE,
        }.get(status, DIM)

        detail_text = {
            "optimal":      "Posture and distance are within optimal range.",
            "warning":      "Minor deviation detected — adjust your position.",
            "critical":     "Significant issue! Please correct immediately.",
            "no_face":      "Face not visible — ensure camera is unobstructed.",
            "uncalibrated": "Calibrate for accurate posture detection.",
        }.get(status, "")

        self._status_label.configure(text=status_text)
        if status == "uncalibrated":
            self._update_live_glow()
        else:
            self._status_label.configure(text_color=status_colour)
        
        self._status_detail.configure(text=detail_text)

        # Focus score
        self._last_face_detected = result.face_detected
        if result.face_detected:
            self._score_history.append(result.focus_score)
            # Keep ~60 s at 20 fps = 1200 frames
            if len(self._score_history) > 1200:
                self._score_history.pop(0)

            avg_score = sum(self._score_history) / len(self._score_history)
            self._score_label.configure(
                text=f"{avg_score:.0f}",
                text_color=self._score_to_colour(avg_score),
            )
            self._score_bar.configure(progress_color=self._score_to_colour(avg_score))
            self._score_bar.set(avg_score / 100.0)
            
            # ── Analytics Logging (every 30s) ──────
            now = time.time()
            if now - getattr(self, "_last_log_ts", 0) > 30:
                self._analytics.log(avg_score)
                self._last_log_ts = now

        # Tile values
        d_color = self._status_to_colour(result.distance_status)
        p_color = self._status_to_colour(result.posture_status)
        d_text  = result.distance_status.upper() if result.face_detected else "---"
        p_text  = result.posture_status.upper()  if result.face_detected else "---"

        self._dist_tile._val_label.configure(text=d_text, text_color=d_color)
        self._posture_tile._val_label.configure(text=p_text, text_color=p_color)
        
        # Dynamic border glow
        self._dist_tile.configure(border_color=d_color if result.face_detected else "#1A1A1A")
        self._posture_tile.configure(border_color=p_color if result.face_detected else "#1A1A1A")

        # ── Alert Banner & Sound ────────────────
        if status == "critical":
            if self._pref_banner.get():
                self._alert_banner.show()
            
            if not self._was_critical:
                if self._pref_flash.get():
                    self._flash_screen.flash()
                
                if self._pref_sound.get():
                    _beep(1800, 400)
                self._was_critical = True
        else:
            self._alert_banner.hide()
            self._was_critical = False

        # Notifications
        if status == "critical" and self._pref_notif.get():
            self._maybe_notify(
                title="Focus-SENSE Alert",
                message="Critical posture or distance issue detected. Please adjust.",
            )

    @staticmethod
    def _status_to_colour(status: str) -> str:
        return {
            "optimal":  STATUS_OK,
            "warning":  STATUS_WARN,
            "critical": STATUS_CRIT,
        }.get(status, DIM)

    @staticmethod
    def _score_to_colour(score: float) -> str:
        if score >= 80:
            return STATUS_OK
        if score >= 50:
            return STATUS_WARN
        return STATUS_CRIT

    # ── Notifications ─────────────────────────

    def _maybe_notify(self, title: str, message: str) -> None:
        now = time.time()
        if now - self._last_notif_ts < self._NOTIF_COOLDOWN_S:
            return
        self._last_notif_ts = now
        try:
            notification.notify(
                title=title,
                message=message,
                app_name="Focus-SENSE",
                timeout=6,
            )
        except Exception:
            pass   # Notifications are best-effort

    # ── Shutdown ──────────────────────────────

    def _on_close(self) -> None:
        """Cleanup and exit."""
        self._is_running = False
        
        # Stop timers and loops
        if hasattr(self, "_after_id"):
            self.after_cancel(self._after_id)
            
        try:
            if self._camera_thread:
                self._camera_thread.stop()
            self._engine.release_camera()
        except Exception:
            pass
            
        self.destroy()
        os._exit(0) # Force exit background threads


# ──────────────────────────────────────────────
#  Entry Point
# ──────────────────────────────────────────────

def main() -> None:
    app = FocusSenseApp()
    app.mainloop()


if __name__ == "__main__":
    main()
