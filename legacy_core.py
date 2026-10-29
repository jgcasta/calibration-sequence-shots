#!/usr/bin/env python3
"""ZWO Calibration Library v1.0.0 - RAW16 FITS acquisition using the ASI SDK."""
from __future__ import annotations

import csv
import os
import queue
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np
from astropy.io import fits

try:
    import zwoasi as asi
except ImportError:
    asi = None


def safe_token(value):
    return re.sub(r"[^A-Za-z0-9+-]", "-", str(value)).strip("-") or "NA"


def numeric_token(value):
    return (f"{value:g}" if isinstance(value, (int, float)) else str(value)).replace("-", "m").replace(".", "p")


class DarkLibraryApp:
    def __init__(self, root):
        self.root = root
        root.title("ZWO Calibration Library · 1.1.0")
        root.geometry("900x725")
        self.camera = None
        self.caminfo = {}
        self.controls = {}
        self.camera_names = []
        self.running = False
        self.stop_event = threading.Event()
        self.messages = queue.Queue()
        self.lock = threading.RLock()
        self.sdk_ready = False
        self.selected = tk.StringVar()
        self.sdk_path = tk.StringVar(value=os.environ.get("ZWO_ASI_LIB", "/usr/local/lib/libASICamera2.so"))
        self.target_temp = tk.StringVar(value="-10")
        self.gain = tk.StringVar(value="100")
        self.offset = tk.StringVar(value="10")
        self.filter_name = tk.StringVar(value="DARK")
        self.exposure = tk.StringVar(value="60")
        self.count = tk.StringVar(value="30")
        self.directory = tk.StringVar(value=str(Path.home() / "Darks"))
        self.status = tk.StringVar(value="Not connected")
        self.camera_details = tk.StringVar(value="No camera selected.")
        self.progress = tk.StringVar(value="0 / 0")
        self._build()
        root.after(120, self._poll)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build(self):
        # Use a vertically scrollable settings area so lower capture controls
        # remain reachable on screens with different sizes or DPI scaling.
        viewport = ttk.Frame(self.root)
        viewport.pack(fill="both", expand=True)
        canvas = tk.Canvas(viewport, highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(viewport, orient="vertical", command=canvas.yview)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        outer = ttk.Frame(canvas, padding=12)
        canvas_item = canvas.create_window((0, 0), window=outer, anchor="nw")
        outer.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(canvas_item, width=event.width))
        def scroll_wheel(event):
            if event.num == 4:
                canvas.yview_scroll(-3, "units")
            elif event.num == 5:
                canvas.yview_scroll(3, "units")
            elif getattr(event, "delta", 0):
                canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        canvas.bind_all("<MouseWheel>", scroll_wheel, add="+")
        canvas.bind_all("<Button-4>", scroll_wheel, add="+")
        canvas.bind_all("<Button-5>", scroll_wheel, add="+")
        self.settings_canvas = canvas
        sdk = ttk.LabelFrame(outer, text="SDK ZWO ASI", padding=10)
        sdk.pack(fill="x", pady=(0, 8))
        ttk.Entry(sdk, textvariable=self.sdk_path).pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.detect_button = ttk.Button(sdk, text="Find cameras", command=self.detect)
        self.detect_button.pack(side="left")

        device = ttk.LabelFrame(outer, text="Camera", padding=10)
        device.pack(fill="x", pady=(0, 8))
        selection = ttk.Frame(device)
        selection.pack(fill="x")
        self.cameras_box = ttk.Combobox(selection, textvariable=self.selected, state="readonly")
        self.cameras_box.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.connect_button = ttk.Button(selection, text="Connect", command=self.connect)
        self.connect_button.pack(side="left")
        ttk.Label(device, textvariable=self.camera_details, justify="left", wraplength=830).pack(anchor="w", pady=(8, 0))

        settings = ttk.LabelFrame(outer, text="Capture settings", padding=10)
        settings.pack(fill="x", pady=(0, 8))
        fields = [
            ("Target temperature (°C)", self.target_temp),
            ("Gain", self.gain),
            ("Offset / Brightness", self.offset),
            ("Exposure (seconds)", self.exposure),
            ("Number of DARKS", self.count),
        ]
        self.entries = []
        for i, (label, variable) in enumerate(fields):
            r, c = divmod(i, 2)
            ttk.Label(settings, text=label).grid(row=r, column=c * 2, sticky="w", padx=(0, 8), pady=7)
            entry = ttk.Entry(settings, textvariable=variable, width=18)
            entry.grid(row=r, column=c * 2 + 1, sticky="ew", pady=7, padx=(0, 20))
            self.entries.append(entry)
        settings.columnconfigure(1, weight=1)
        settings.columnconfigure(3, weight=1)
        ttk.Label(settings, text="Output directory").grid(row=3, column=0, sticky="w", pady=7)
        self.dir_entry = ttk.Entry(settings, textvariable=self.directory)
        self.dir_entry.grid(row=3, column=1, columnspan=2, sticky="ew", pady=7)
        self.dir_button = ttk.Button(settings, text="Browse…", command=self._browse)
        self.dir_button.grid(row=3, column=3, sticky="e")
        ttk.Label(settings, text="The filter is informational: no filter wheel is controlled. Completely cover the sensor.", foreground="#555").grid(row=4, column=0, columnspan=4, sticky="w", pady=(7, 0))

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(2, 8))
        self.start_button = ttk.Button(actions, text="Start DARK library", command=self.start, state="disabled")
        self.start_button.pack(side="left", padx=(0, 8))
        self.stop_button = ttk.Button(actions, text="Stop after current frame", command=self.stop, state="disabled")
        self.stop_button.pack(side="left")
        ttk.Label(actions, textvariable=self.progress).pack(side="right")
        self.bar = ttk.Progressbar(outer, mode="determinate")
        self.bar.pack(fill="x", pady=(0, 8))
        ttk.Label(outer, textvariable=self.status).pack(anchor="w", pady=(0, 4))
        log_frame = ttk.LabelFrame(outer, text="Log", padding=6)
        log_frame.pack(fill="both", expand=True)
        self.log_widget = tk.Text(log_frame, height=9, wrap="word", state="disabled")
        self.log_widget.pack(fill="both", expand=True)

    def _browse(self):
        path = filedialog.askdirectory(initialdir=self.directory.get())
        if path:
            self.directory.set(path)

    def _log(self, message):
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", f"{datetime.now():%H:%M:%S}  {message}\n")
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")

    def detect(self):
        if asi is None:
            messagebox.showerror("Missing dependency", "Install the zwoasi Python package: pip install zwoasi")
            return
        if self.running:
            return
        try:
            if not self.sdk_ready:
                asi.init(self.sdk_path.get().strip())
                self.sdk_ready = True
            names = asi.list_cameras()
            self.camera_names = names
            self.cameras_box["values"] = [f"{idx}: {name}" for idx, name in enumerate(names)]
            if names:
                self.cameras_box.current(0)
                self._log(f"Cameras found: {len(names)}")
            else:
                self.selected.set("")
                self._log("No connected cameras found.")
        except Exception as exc:
            messagebox.showerror("SDK error", f"Could not initialize/enumerate ZWO ASI:\n{exc}")

    def connect(self):
        if self.running:
            return
        index = self.cameras_box.current()
        if index < 0:
            messagebox.showwarning("Camera", "Select Find cameras, then choose a camera.")
            return
        try:
            with self.lock:
                if self.camera is not None:
                    self.camera.close()
                self.camera = asi.Camera(index)
                self.caminfo = self.camera.get_camera_property()
                self.controls = self.camera.get_controls()
                self.camera.disable_dark_subtract()
                self.camera.set_image_type(asi.ASI_IMG_RAW16)
                self.camera.set_control_value(asi.ASI_EXPOSURE, 1000000, auto=False)
                if "Gain" in self.controls:
                    self.gain.set(str(self.camera.get_control_value(asi.ASI_GAIN)[0]))
                if "Brightness" in self.controls:
                    self.offset.set(str(self.camera.get_control_value(asi.ASI_BRIGHTNESS)[0]))
                else:
                    self.offset.set("N/A")
                dims = f"{self.caminfo.get('MaxWidth')} × {self.caminfo.get('MaxHeight')}"
                cooled = bool(self.caminfo.get("IsCoolerCam"))
                temp = self._get_temperature()
                description = (f"Modelo: {self.caminfo.get('Name')}    ID: {self.caminfo.get('CameraID', index)}\n"
                               f"Sensor: {dims}    Color: {'Yes' if self.caminfo.get('IsColorCam') else 'No'}"
                               f"    Cooling: {'Yes' if cooled else 'No'}    Current temperature: {temp if temp is not None else 'N/A'} °C\n"
                               f"Available controls: {', '.join(sorted(self.controls))}")
                self.camera_details.set(description)
            self.start_button.configure(state="normal")
            self.status.set("Camera connected. Ready to capture.")
            self._log(f"Conectada: {self.caminfo.get('Name')}")
        except Exception as exc:
            self.camera = None
            self.start_button.configure(state="disabled")
            messagebox.showerror("Connection error", str(exc))

    def _get_temperature(self):
        if "Temperature" not in self.controls:
            return None
        try:
            return self.camera.get_control_value(asi.ASI_TEMPERATURE)[0] / 10.0
        except Exception:
            return None

    def _get_control(self, name, constant):
        if name not in self.controls:
            return None
        try:
            return int(self.camera.get_control_value(constant)[0])
        except Exception:
            return None

    def _checked_int(self, name, value, minimum=None, maximum=None):
        parsed = int(value)
        if minimum is not None and parsed < minimum:
            raise ValueError(f"{name}: value below {minimum}")
        if maximum is not None and parsed > maximum:
            raise ValueError(f"{name}: value above {maximum}")
        return parsed

    def _validate(self):
        if self.camera is None:
            raise ValueError("Connect a camera first")
        gain = self._checked_int("Gain", self.gain.get())
        gcap = self.controls.get("Gain")
        if gcap and not gcap['MinValue'] <= gain <= gcap['MaxValue']:
            raise ValueError(f"Gain out of range: {gcap['MinValue']}–{gcap['MaxValue']}")
        if not gcap:
            raise ValueError("The camera does not support gain control")
        offset = None
        if "Brightness" in self.controls:
            offset = self._checked_int("Offset", self.offset.get())
            ocap = self.controls["Brightness"]
            if not ocap['MinValue'] <= offset <= ocap['MaxValue']:
                raise ValueError(f"Offset out of range: {ocap['MinValue']}–{ocap['MaxValue']}")
        exposure_sec = float(self.exposure.get().replace(",", "."))
        if not np.isfinite(exposure_sec) or exposure_sec <= 0:
            raise ValueError("Exposure must be positive")
        microseconds = round(exposure_sec * 1_000_000)
        ecap = self.controls.get("Exposure")
        if not ecap or not ecap['MinValue'] <= microseconds <= ecap['MaxValue']:
            raise ValueError(f"Exposure outside permitted range ({ecap['MinValue']/1e6:g}–{ecap['MaxValue']/1e6:g} s)" if ecap else "Exposure control unavailable")
        count = self._checked_int("Number of frames", self.count.get(), 1, 100000)
        cooled = bool(self.caminfo.get("IsCoolerCam"))
        temp = float(self.target_temp.get().replace(",", ".")) if cooled else None
        if cooled and not np.isfinite(temp):
            raise ValueError("Invalid temperature")
        out = Path(self.directory.get().strip()).expanduser()
        if not str(self.directory.get()).strip():
            raise ValueError("Choose an output directory")
        out.mkdir(parents=True, exist_ok=True)
        if not out.is_dir() or not os.access(out, os.W_OK):
            raise ValueError("Directory is not writable")
        return dict(gain=gain, offset=offset, exp=exposure_sec, exp_us=microseconds,
                    count=count, temp=temp, cooled=cooled, filter=self.filter_name.get().strip() or "DARK", out=out)

    def start(self):
        try:
            config = self._validate()
        except Exception as exc:
            messagebox.showerror("Invalid settings", str(exc))
            return
        if not messagebox.askokcancel("Confirmation", "Make sure the camera is COMPLETELY dark before acquisition.\n\nStart the session?"):
            return
        self.running = True
        self.stop_event.clear()
        self.bar.configure(maximum=config["count"], value=0)
        self.progress.set(f"0 / {config['count']}")
        self.start_button.configure(state="disabled")
        self.connect_button.configure(state="disabled")
        self.detect_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        for w in self.entries + [self.dir_entry, self.dir_button]:
            w.configure(state="disabled")
        self.messages.put(("status", "Preparing camera…"))
        threading.Thread(target=self._capture_worker, args=(config,), daemon=True).start()

    def _capture_worker(self, c):
        completed = 0
        try:
            with self.lock:
                self.camera.stop_exposure()
                try:
                    self.camera.stop_video_capture()
                except Exception:
                    pass
                self.camera.disable_dark_subtract()
                self.camera.set_image_type(asi.ASI_IMG_RAW16)
                self.camera.set_control_value(asi.ASI_GAIN, c["gain"], auto=False)
                if c["offset"] is not None:
                    self.camera.set_control_value(asi.ASI_BRIGHTNESS, c["offset"], auto=False)
                self.camera.set_control_value(asi.ASI_EXPOSURE, c["exp_us"], auto=False)
                if c["cooled"]:
                    if "TargetTemp" not in self.controls or "CoolerOn" not in self.controls:
                        raise RuntimeError("Cooled camera does not expose TargetTemp/CoolerOn controls")
                    # ZWO SDK uses integer degrees Celsius for TargetTemp.
                    target = round(c["temp"])
                    cap = self.controls["TargetTemp"]
                    if not cap['MinValue'] <= target <= cap['MaxValue']:
                        raise ValueError("Target temperature is outside the allowed range")
                    self.camera.set_control_value(asi.ASI_TARGET_TEMP, target, auto=False)
                    self.camera.set_control_value(asi.ASI_COOLER_ON, 1, auto=False)
            if c["cooled"]:
                self.messages.put(("log", f"Cooling enabled at {round(c['temp'])} °C. Waiting for stabilization (±1 °C for 30 s, maximum 15 min)…"))
                limit = time.monotonic() + 900
                stable_since = None
                while not self.stop_event.is_set():
                    with self.lock:
                        actual = self._get_temperature()
                    if actual is not None and abs(actual - round(c["temp"])) <= 1:
                        stable_since = stable_since or time.monotonic()
                        if time.monotonic() - stable_since >= 30:
                            break
                    else:
                        stable_since = None
                    if time.monotonic() > limit:
                        raise RuntimeError("Thermal stability not reached in 15 minutes. Check ventilation and target temperature")
                    self.messages.put(("status", f"Stabilizing camera: {actual} °C / {round(c['temp'])} °C"))
                    self.stop_event.wait(2)
            day = datetime.now(timezone.utc).strftime("%Y%m%d")
            offset_token = str(c["offset"]) if c["offset"] is not None else "NA"
            temp_token = numeric_token(round(c["temp"])) if c["cooled"] else "NA"
            duration_token = numeric_token(c["exp"]) + "s"
            session_csv = c["out"] / f"DARK_session_{day}.csv"
            for idx in range(1, c["count"] + 1):
                if self.stop_event.is_set():
                    break
                start_utc = datetime.now(timezone.utc)
                self.messages.put(("status", f"Exponiendo DARK {idx}/{c['count']} ({c['exp']:g} s)"))
                with self.lock:
                    image = self.camera.capture(initial_sleep=0.01, poll=0.15)
                    actual_temp = self._get_temperature()
                    actual_gain = self._get_control("Gain", asi.ASI_GAIN)
                    actual_offset = self._get_control("Brightness", asi.ASI_BRIGHTNESS)
                if image.dtype != np.uint16 or image.ndim != 2:
                    raise RuntimeError(f"Incompatible image, shape={image.shape}, dtype={image.dtype}")
                # Nombre solicitado. El filtro se guarda en la cabecera FITS (no aparece en el nombre).
                stem = f"DARK_{day}_{temp_token}_{c['gain']}_{offset_token}_{duration_token}"
                number = idx
                while (c["out"] / f"{stem}_{number:04d}.fits").exists():
                    number += 1
                path = c["out"] / f"{stem}_{number:04d}.fits"
                hdr = fits.Header()
                hdr['IMAGETYP'] = ('DARK', 'Tipo de imagen')
                hdr['DATE-OBS'] = (start_utc.isoformat(timespec='milliseconds'), 'UTC inicio exposicion')
                hdr['EXPTIME'] = (c['exp'], 'Segundos')
                hdr['EXPOSURE'] = (c['exp'], 'Segundos')
                hdr['GAIN'] = (actual_gain if actual_gain is not None else c['gain'], 'Gain ZWO')
                if actual_offset is not None:
                    hdr['OFFSET'] = (actual_offset, 'Brightness/offset ZWO')
                hdr['FILTER'] = (c['filter'][:68], 'Filter recorded, not controlled')
                hdr['INSTRUME'] = (str(self.caminfo.get('Name', 'ZWO ASI'))[:68], 'Modelo camara')
                hdr['XBINNING'] = 1
                hdr['YBINNING'] = 1
                hdr['BZERO'] = 32768
                # Astropy automatically handles BZERO/BSCALE for uint16; avoid duplicates.
                del hdr['BZERO']
                if actual_temp is not None:
                    hdr['CCD-TEMP'] = (actual_temp, 'Celsius, medida real')
                if c['cooled']:
                    hdr['SET-TEMP'] = (round(c['temp']), 'Celsius, objetivo')
                hdr['SEQNUM'] = (idx, 'Orden toma en esta sesion')
                fits.PrimaryHDU(data=np.ascontiguousarray(image), header=hdr).writeto(path, overwrite=False, output_verify="exception")
                record = dict(fecha_utc=start_utc.isoformat(), archivo=path.name, camara=self.caminfo.get('Name', ''),
                              temperatura_objetivo=c['temp'] if c['cooled'] else '', temperatura_real=actual_temp if actual_temp is not None else '',
                              ganancia=actual_gain if actual_gain is not None else c['gain'], offset=actual_offset if actual_offset is not None else '',
                              filtro=c['filter'], exposicion_s=c['exp'], secuencial=idx)
                # Append-only log with header. Multiple sessions may share the same day.
                with session_csv.open('a', newline='', encoding='utf-8') as stream:
                    writer = csv.DictWriter(stream, fieldnames=list(record))
                    if stream.tell() == 0:
                        writer.writeheader()
                    writer.writerow(record)
                completed += 1
                self.messages.put(("progress", (completed, c['count'])))
                self.messages.put(("log", f"Saved {path.name}  ·  T={actual_temp if actual_temp is not None else 'N/A'} °C"))
            self.messages.put(("status", f"{'Stopped' if self.stop_event.is_set() else 'Completed'}: {completed}/{c['count']} FITS en {c['out']}"))
        except Exception as exc:
            self.messages.put(("error", str(exc)))
        finally:
            self.messages.put(("done", None))

    def stop(self):
        self.stop_event.set()
        self.stop_button.configure(state="disabled")
        self.status.set("Stop requested; waiting for current exposure to finish…")

    def _poll(self):
        try:
            while True:
                kind, data = self.messages.get_nowait()
                if kind == "log":
                    self._log(data)
                elif kind == "status":
                    self.status.set(data)
                elif kind == "progress":
                    done, total = data
                    self.bar['value'] = done
                    self.progress.set(f"{done} / {total}")
                elif kind == "error":
                    self.status.set("Acquisition error")
                    self._log("ERROR: " + data)
                    messagebox.showerror("Acquisition error", data)
                elif kind == "done":
                    self.running = False
                    self.start_button.configure(state="normal" if self.camera else "disabled")
                    self.stop_button.configure(state="disabled")
                    self.detect_button.configure(state="normal")
                    self.connect_button.configure(state="normal")
                    for w in self.entries + [self.dir_entry, self.dir_button]:
                        w.configure(state="normal")
        except queue.Empty:
            pass
        self.root.after(120, self._poll)

    def _on_close(self):
        if self.running:
            messagebox.showwarning("Acquisition in progress", "Select Stop and wait for the current exposure before exiting.")
            return
        try:
            if self.camera is not None:
                self.camera.close()
        except Exception:
            pass
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    DarkLibraryApp(root)
    root.mainloop()
