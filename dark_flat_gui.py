#!/usr/bin/env python3
"""ZWO Calibration Library GUI v1.0.0. Uncalibrated RAW16 captures."""
from __future__ import annotations
import csv
import os
import queue
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

import numpy as np
from astropy.io import fits
from legacy_core import DarkLibraryApp as BaseApp, asi, numeric_token
from indi_panel import IndiPanel
from indi_server import IndiServerManager
from serial_detector import detect_serial_ports, choose_serial_port


def file_token(value: object) -> str:
    """Filesystem-safe token without ambiguous separators."""
    return re.sub(r'[^A-Za-z0-9+-]+', '-', str(value).strip()).strip('-') or 'NA'


class DarkFlatApp(BaseApp):
    def _build(self):
        super()._build()
        self.root.title('ZWO Calibration Library · 1.0.0')
        self.root.geometry('1030x850')
        self.sdk_config = Path.home() / '.config' / 'zwo-dark-library' / 'settings.json'
        self.object_name = tk.StringVar(value='CALIBRATION')
        self.flat_exp = tk.StringVar(value='0.1')
        self.flat_count = tk.StringVar(value='30')
        self.use_flats = tk.BooleanVar(value=True)
        self.flats_dialog_event = None
        self.flats_dialog_answer = False
        self.panel = IndiPanel()
        self.indi_server = IndiServerManager()
        self.indi_driver = tk.StringVar(value="indi_flipflat")
        self.server_status = tk.StringVar(value="Server not started by this application")
        self.indi_host = tk.StringVar(value='localhost')
        self.indi_port = tk.StringVar(value='7624')
        self.indi_device = tk.StringVar(value='')
        self.indi_serial_port = tk.StringVar(value='')
        self.indi_brightness = tk.StringVar(value='140')
        self.indi_status = tk.StringVar(value='Panel disconnected')

        parent = self.start_button.master.master
        settings = self.entries[0].master
        general = ttk.LabelFrame(parent, text='General', padding=8)
        general.pack(fill='x', before=settings, pady=(0, 8))
        ttk.Label(general, text='Object / label').grid(row=0, column=0, sticky='w', padx=(0, 10), pady=4)
        self.object_entry = ttk.Entry(general, textvariable=self.object_name, width=28)
        self.object_entry.grid(row=0, column=1, sticky='ew', padx=(0, 20), pady=4)
        ttk.Label(general, text='Filter').grid(row=0, column=2, sticky='w', padx=(0, 10), pady=4)
        self.filter_entry = ttk.Entry(general, textvariable=self.filter_name, width=28)
        self.filter_entry.grid(row=0, column=3, sticky='ew', pady=4)
        general.columnconfigure(1, weight=1)
        general.columnconfigure(3, weight=1)

        extra = ttk.LabelFrame(parent, text='DARK-FLAT / FLAT acquisition', padding=8)
        extra.pack(fill='x', before=self.start_button.master, pady=(0, 8))
        fields = [('FLAT exposure (s)', self.flat_exp),
                  ('Number of FLATS', self.flat_count)]
        self.extra_entries = [self.object_entry, self.filter_entry]
        for i, (title, var) in enumerate(fields):
            ttk.Label(extra, text=title).grid(row=i, column=0, sticky='w', pady=3, padx=(0, 10))
            e = ttk.Entry(extra, textvariable=var, width=25)
            e.grid(row=i, column=1, sticky='ew', pady=3)
            self.extra_entries.append(e)
        self.flat_check = ttk.Checkbutton(extra, text='Capture DARK-FLATS and FLATS after DARKS', variable=self.use_flats)
        self.flat_check.grid(row=2, column=0, columnspan=2, sticky='w', pady=(5, 0))
        extra.columnconfigure(1, weight=1)
        ttk.Label(extra, text='The filter is recorded in FITS; filter wheel selection is manual. The light panel is controlled via INDI.',
                  wraplength=850).grid(row=3, column=0, columnspan=2, sticky='w', pady=(4, 0))

        indi_frame = ttk.LabelFrame(parent, text='FLAT panel · INDI server', padding=8)
        indi_frame.pack(fill='x', before=self.start_button.master, pady=(0,8))
        for col, (label, var, width) in enumerate([
            ('Host', self.indi_host, 17), ('Port', self.indi_port, 7),
            ('Device (optional)', self.indi_device, 27), ('Brightness (0–255)', self.indi_brightness, 7)
        ]):
            ttk.Label(indi_frame, text=label).grid(row=0, column=col*2, sticky='w', padx=(0,5))
            entry = ttk.Entry(indi_frame, textvariable=var, width=width)
            entry.grid(row=0, column=col*2+1, padx=(0,12), sticky='ew')
            self.extra_entries.append(entry)
        ttk.Label(indi_frame, text='Driver').grid(row=1, column=0, sticky='w', pady=(8,0))
        self.driver_entry = ttk.Entry(indi_frame, textvariable=self.indi_driver, width=20)
        self.driver_entry.grid(row=1, column=1, sticky='ew', pady=(8,0))
        self.indi_start_server_button = ttk.Button(indi_frame, text='Start INDI server', command=self.start_indi_server)
        self.indi_start_server_button.grid(row=1, column=2, columnspan=2, sticky='w', pady=(8,0))
        self.indi_stop_server_button = ttk.Button(indi_frame, text='Stop INDI server', command=self.stop_indi_server, state='disabled')
        self.indi_stop_server_button.grid(row=1, column=4, columnspan=2, sticky='w', pady=(8,0))
        ttk.Label(indi_frame, textvariable=self.server_status).grid(row=1, column=6, columnspan=2, sticky='w', pady=(8,0))
        ttk.Label(indi_frame, text='Flip-Flat serial port').grid(row=3, column=0, columnspan=2, sticky='w', pady=(8,0))
        self.indi_serial_entry = ttk.Combobox(indi_frame, textvariable=self.indi_serial_port, width=33, state='normal')
        self.indi_serial_entry.grid(row=3, column=2, columnspan=3, sticky='ew', pady=(8,0))
        self.serial_refresh_button = ttk.Button(indi_frame, text='Scan ports', command=self.refresh_serial_ports)
        self.serial_refresh_button.grid(row=3, column=5, sticky='w', padx=(8,0), pady=(8,0))
        self.extra_entries.append(self.indi_serial_entry)
        ttk.Label(indi_frame, text='Automatic USB detection; you may enter the path manually').grid(row=3, column=6, columnspan=2, sticky='w', pady=(8,0))
        self.indi_connect_button = ttk.Button(indi_frame, text='Connect panel', command=self.connect_panel)
        self.indi_connect_button.grid(row=2, column=0, columnspan=2, sticky='w', pady=(8,0))
        self.indi_test_button = ttk.Button(indi_frame, text='Set brightness / turn on', command=self.test_panel, state='disabled')
        self.indi_test_button.grid(row=2, column=2, columnspan=2, sticky='w', pady=(8,0))
        self.indi_off_button = ttk.Button(indi_frame, text='Turn off', command=self.panel_off, state='disabled')
        self.indi_off_button.grid(row=2, column=4, sticky='w', pady=(8,0))
        ttk.Label(indi_frame, textvariable=self.indi_status).grid(row=2, column=5, columnspan=3, sticky='w')
        self.start_button.configure(text='Start DARKS + DARK-FLATS + FLATS')
        self.sdk_button = ttk.Button(self.detect_button.master, text='Select SDK…', command=self.choose_sdk)
        self.sdk_button.pack(side='left', padx=(6,0))
        self.root.after(250, self.refresh_serial_ports)
        import json
        if self.sdk_config.exists() and not os.environ.get('ZWO_ASI_LIB'):
            try:
                sdk = json.loads(self.sdk_config.read_text(encoding='utf-8')).get('sdk_path')
                if sdk: self.sdk_path.set(sdk)
            except (ValueError, OSError): pass

    def start_indi_server(self):
        if self.running:
            messagebox.showwarning('INDI', 'Cannot start the server during an acquisition session.')
            return
        try:
            host = self.indi_host.get().strip()
            port = int(self.indi_port.get().strip())
            driver = self.indi_driver.get().strip()
            if host.lower() not in ('localhost', '127.0.0.1', '::1'):
                raise ValueError('Starting a local server requires localhost.')
        except Exception as exc:
            messagebox.showerror('INDI server', str(exc))
            return
        self.indi_start_server_button.configure(state='disabled')
        self.server_status.set('Starting server…')

        def work():
            try:
                self.indi_server.start(host, port, driver)
                self.root.after(0, lambda: self._server_started(port, driver))
            except Exception as exc:
                error = str(exc)
                self.root.after(0, lambda: self._server_failed(error))
        threading.Thread(target=work, daemon=True).start()

    def _server_started(self, port, driver):
        self.server_status.set(f'Running: {driver}, port {port}')
        self.indi_stop_server_button.configure(state='normal')
        self._log(f'INDI server started: {driver}, port {port}')

    def _server_failed(self, error):
        self.server_status.set('Server not started')
        self.indi_start_server_button.configure(state='normal')
        messagebox.showerror('INDI server', error)

    def stop_indi_server(self):
        if self.running:
            messagebox.showwarning('INDI', 'Stop the acquisition session before stopping the server.')
            return
        if self.panel.client is not None:
            try:
                self.panel.light(False)
            except Exception as exc:
                if not messagebox.askyesno('Panel safety',
                    f'Could not confirm that the panel was turned off: {exc}\nStop the server anyway?'):
                    return
            self.panel.disconnect()
            self.indi_status.set('Panel disconnected')
            self.indi_test_button.configure(state='disabled')
            self.indi_off_button.configure(state='disabled')
        self.indi_server.stop()
        self.server_status.set('Server stopped')
        self.indi_start_server_button.configure(state='normal')
        self.indi_stop_server_button.configure(state='disabled')
        self._log('Application-managed INDI server stopped')

    def refresh_serial_ports(self):
        """Find local serial ports and update the dropdown without blocking Tk."""
        ports = detect_serial_ports()
        self.indi_serial_entry.configure(values=[p.path for p in ports])
        current = self.indi_serial_port.get().strip()
        selected, reason = choose_serial_port(ports, current)
        if selected:
            self.indi_serial_port.set(selected)
            self._log(f'Selected serial port: {selected} ({reason})')
        else:
            if current and not Path(current).exists():
                self.indi_serial_port.set('')
            self._log(f'Serial port detection: {reason}')
        return selected

    def connect_panel(self):
        """TCP connection in background thread to keep Tkinter responsive."""
        host, device = self.indi_host.get().strip(), self.indi_device.get().strip()
        # The device might be re-enumerated after the UI starts.
        serial_port = self.refresh_serial_ports() or self.indi_serial_port.get().strip()
        if not serial_port or not Path(serial_port).exists():
            messagebox.showerror('Serial port',
                'No valid Flip-Flat serial port was found. '
                'Connect it via USB and select Scan ports. If multiple ports exist, choose the correct one.')
            return
        try:
            port = int(self.indi_port.get().strip())
        except ValueError:
            messagebox.showerror('INDI', 'Invalid INDI port')
            return
        self.indi_connect_button.configure(state='disabled')
        self.indi_status.set('Connecting TCP and device…')

        def worker():
            try:
                connected = self.panel.connect(host, port, device, timeout=16, serial_port=serial_port)
            except Exception as exc:
                err = str(exc)
                self.root.after(0, lambda: self._panel_connect_error(err))
            else:
                self.root.after(0, lambda: self._panel_connected(connected))
        threading.Thread(target=worker, daemon=True).start()

    def _panel_connected(self, device):
        self.indi_connect_button.configure(state='normal')
        self.indi_device.set(device)
        self.indi_status.set(f'Connected: {device} (TCP)')
        self.indi_test_button.configure(state='normal')
        self.indi_off_button.configure(state='normal')
        self._log(f'INDI panel connected over TCP: {device}')

    def _panel_connect_error(self, error):
        self.indi_connect_button.configure(state='normal')
        self.indi_status.set('INDI connection error')
        self._log(f'INDI connection error: {error}')
        messagebox.showerror('Panel INDI', error)

    def _brightness(self):
        value = int(self.indi_brightness.get().strip())
        if not 0 <= value <= 255:
            raise ValueError('Panel brightness must be between 0 and 255')
        return value

    def test_panel(self):
        try:
            self.panel.prepare_flat(self._brightness())
            self.indi_status.set(f'Panel on, brightness {self._brightness()}')
        except Exception as exc:
            messagebox.showerror('Panel INDI', str(exc))

    def panel_off(self):
        try:
            self.panel.light(False)
            self.indi_status.set('Panel off')
        except Exception as exc:
            messagebox.showerror('Panel INDI', str(exc))

    def _on_close(self):
        if self.running:
            messagebox.showwarning('Acquisition in progress', 'Stop the acquisition first so the panel can be turned off safely.')
            return
        if self.panel.client is not None:
            try:
                self.panel.light(False)
            except Exception as exc:
                messagebox.showwarning('Panel INDI', f'Panel shutdown could not be confirmed: {exc}')
            self.panel.disconnect()
        self.indi_server.stop()
        super()._on_close()

    def choose_sdk(self):
        from tkinter import filedialog
        import json
        path = filedialog.askopenfilename(title='Select libASICamera2.so', filetypes=[('Shared library','*.so*'),('All files','*')])
        if path:
            self.sdk_path.set(path)
            self.sdk_config.parent.mkdir(parents=True, exist_ok=True)
            self.sdk_config.write_text(json.dumps({'sdk_path':path}), encoding='utf-8')

    def detect(self):
        if not self.sdk_ready and not Path(self.sdk_path.get()).is_file():
            paths = [Path('/usr/local/lib/libASICamera2.so'), Path('/usr/lib/libASICamera2.so'),
                     Path('/usr/lib/x86_64-linux-gnu/libASICamera2.so')]
            found = next((p for p in paths if p.is_file()), None)
            if found: self.sdk_path.set(str(found))
            else:
                messagebox.showerror('Missing SDK', 'libASICamera2.so was not found. Set its path using Select SDK…')
                return
        super().detect()

    def _single_row(self):
        """Read a single acquisition configuration from the visible fields."""
        cooled = bool(self.caminfo.get('IsCoolerCam'))
        temp = float(self.target_temp.get().replace(',', '.')) if cooled else None
        gain = int(self.gain.get())
        offset_text = self.offset.get().strip()
        offset = None if offset_text.upper() in ('N/A', '') else int(offset_text)
        exp = float(self.exposure.get().replace(',', '.'))
        count = int(self.count.get())
        if not np.isfinite(exp) or exp <= 0 or not 1 <= count <= 100000:
            raise ValueError('Invalid DARK exposure or count')
        if temp is not None and not np.isfinite(temp):
            raise ValueError('Invalid temperature')
        return dict(temp=temp, gain=gain, offset=offset, exp=exp,
                    count=count, filter=self.filter_name.get().strip() or 'NoFilter')

    def _config(self):
        if self.camera is None: raise ValueError('Connect a camera')

        out_str = self.directory.get().strip()
        if not out_str: raise ValueError('Choose an output directory')
        # Output directory is the root; each object has DARK, DARKFLATS, FLATS, and a session CSV.
        object_label = self.object_name.get().strip()
        if not object_label:
            raise ValueError('Enter an Object / label')
        object_dir = file_token(object_label)
        out = Path(out_str).expanduser() / object_dir
        out.mkdir(parents=True, exist_ok=True)
        if not out.is_dir() or not os.access(out, os.W_OK): raise ValueError('Output directory is not writable')
        cooled = bool(self.caminfo.get('IsCoolerCam'))
        for key in ('Exposure', 'Gain'):
            if key not in self.controls: raise ValueError(f'No existe control {key}')
        if cooled and not all(k in self.controls for k in ('TargetTemp','CoolerOn','Temperature')):
            raise ValueError('Cooled camera does not report all required temperature controls')
        flats = self.use_flats.get()
        flat_exp = float(self.flat_exp.get().replace(',','.')) if flats else 0
        flat_count = int(self.flat_count.get()) if flats else 0
        if flats and (not np.isfinite(flat_exp) or flat_exp <= 0 or not 1 <= flat_count <= 100000):
            raise ValueError('Invalid FLAT exposure or count')
        if flats and not self.controls['Exposure']['MinValue'] <= round(flat_exp*1e6) <= self.controls['Exposure']['MaxValue']:
            raise ValueError('FLAT exposure is outside the camera range')
        brightness = self._brightness()
        if flats and self.panel.client is None:
            raise ValueError('Connect the INDI light panel before capturing FLATS')
        checked = []
        for i, row in enumerate([self._single_row()], 1):
            r = dict(row)
            if not self.controls['Exposure']['MinValue'] <= round(r['exp']*1e6) <= self.controls['Exposure']['MaxValue']:
                raise ValueError(f'Row {i}: DARK exposure outside permitted range')
            if not self.controls['Gain']['MinValue'] <= r['gain'] <= self.controls['Gain']['MaxValue']:
                raise ValueError(f'Row {i}: gain outside permitted range')
            if 'Brightness' in self.controls:
                if r['offset'] is None: raise ValueError(f'Row {i}: offset missing')
                if not self.controls['Brightness']['MinValue'] <= r['offset'] <= self.controls['Brightness']['MaxValue']:
                    raise ValueError(f'Row {i}: offset outside permitted range')
            elif r['offset'] is not None: raise ValueError(f'Row {i}: offset is not configurable on this camera')
            if cooled:
                if r['temp'] is None or not self.controls['TargetTemp']['MinValue'] <= round(r['temp']) <= self.controls['TargetTemp']['MaxValue']:
                    raise ValueError(f'Row {i}: temperature outside permitted range')
                r['temp'] = round(r['temp'])
            else: r['temp'] = None
            checked.append(r)
        total = sum(r['count'] for r in checked) + len(checked)*2*flat_count
        return dict(rows=checked, out=out, cooled=cooled, flat_exp=flat_exp, flat_count=flat_count,
                    flats=flats, object=object_dir,
                    camera=file_token(self.caminfo.get('Name','ZWO-ASI')), total=total, brightness=brightness)

    def start(self):
        try: cfg = self._config()
        except Exception as exc:
            messagebox.showerror('Invalid parameters', str(exc)); return
        if not messagebox.askokcancel('Start session',
             f'The session will capture {cfg["rows"][0]["count"]} DARKS, {cfg["flat_count"] if cfg["flats"] else 0} DARK-FLATS and {cfg["flat_count"] if cfg["flats"] else 0} FLATS.\n'
             f'Folder: {cfg["out"]}\n\n'
             'DARKS: cover the telescope/camera and block all light.\n'
             'DARK-FLATS are also captured in the dark, using the FLAT exposure.\n'
             'You will be prompted to uncover and illuminate before FLATS.\n\nStart now?'):
            return
        try:
            if cfg['flats']:
                self.panel.prepare_dark()
        except Exception as exc:
            messagebox.showerror('Panel safety', f'Could not turn the panel light off before DARKS: {exc}')
            return
        self.running = True
        self.stop_event.clear()
        self.bar.configure(maximum=cfg['total'], value=0)
        self.progress.set(f'0 / {cfg["total"]}')
        self.start_button.configure(state='disabled')
        self.stop_button.configure(state='normal')
        self.detect_button.configure(state='disabled')
        self.connect_button.configure(state='disabled')
        for widget in self.entries + self.extra_entries + [self.dir_entry, self.dir_button,
                    self.sdk_button, self.flat_check, self.indi_connect_button,
                    self.indi_test_button, self.indi_off_button,
                    self.driver_entry, self.indi_start_server_button, self.indi_stop_server_button]:
            widget.configure(state='disabled')
        threading.Thread(target=self._worker, args=(cfg,), daemon=True).start()

    def _wait_temp(self, target):
        import time
        deadline = time.monotonic() + 900
        stable = None
        while not self.stop_event.is_set():
            with self.lock: measured = self._get_temperature()
            if measured is not None and abs(measured - target) <= 1:
                if stable is None: stable = time.monotonic()
                if time.monotonic() - stable >= 30: return True
            else: stable = None
            if time.monotonic() > deadline:
                raise RuntimeError(f'Temperature did not stabilize at {target} °C within 15 minutes')
            self.messages.put(('status', f'Stabilizing sensor: {measured} °C / {target} °C'))
            self.stop_event.wait(2)
        return False

    def _request_user(self, prompt):
        event = threading.Event()
        result = {'approved': False}
        self.messages.put(('ask', (prompt, event, result)))
        while not self.stop_event.is_set() and not event.wait(0.2): pass
        return not self.stop_event.is_set() and result['approved']

    def _worker(self, cfg):
        done = 0
        csv_file = cfg['out'] / f'CALIBRATION_session_{datetime.now():%Y%m%d_%H%M%S}.csv'
        fieldnames = ['utc_date','file','type','object','camera','target_temperature',
                      'measured_temperature','gain','offset','filter','exposure_s','sequence']
        try:
            with self.lock:
                try: self.camera.stop_exposure()
                except Exception: pass
                try: self.camera.stop_video_capture()
                except Exception: pass
                self.camera.disable_dark_subtract()
                self.camera.set_image_type(asi.ASI_IMG_RAW16)
            folders = {kind: cfg['out'] / folder for kind, folder in
                       [('DARK', 'DARK'), ('DARKFLAT', 'DARKFLATS'), ('FLAT', 'FLATS')]}
            for folder in folders.values():
                folder.mkdir(parents=True, exist_ok=True)
            with csv_file.open('w', newline='', encoding='utf-8') as fp:
                writer = csv.DictWriter(fp, fieldnames=fieldnames)
                writer.writeheader()
                # Fases secuenciales: DARKS, DARK-FLATS (a oscuras), FLATS (iluminados).
                for frame_type in ('DARK', 'DARKFLAT', 'FLAT'):
                    if frame_type != 'DARK' and not cfg['flats']: break
                    if self.stop_event.is_set(): break
                    if frame_type in ('DARK', 'DARKFLAT') and cfg['flats']:
                        self.panel.prepare_dark()
                    if frame_type == 'DARKFLAT':
                        if not self._request_user('DARKS completed.\n\nKeep the cap on and the entire optical system completely dark. '
                                                  'DARK-FLATS will now be captured with the SAME exposure, gain, offset, temperature and filter as FLATS.\n\nStart DARK-FLATS?'):
                            self.stop_event.set(); break
                    if frame_type == 'FLAT':
                        if not self._request_user('DARK-FLATS completed.\n\nRemove the cap, install the specified filter and switch on the FLAT panel. '
                                                  'Verify uniform illumination without saturation.\n\nStart FLATS?'):
                            self.stop_event.set(); break
                        self.panel.prepare_flat(cfg['brightness'])
                        self.stop_event.wait(2)  # short light panel stabilization
                        if self.stop_event.is_set(): break
                        self.messages.put(('log', f'INDI panel on; brightness {cfg["brightness"]}/255'))
                    for index, row in enumerate(cfg['rows'], 1):
                        if self.stop_event.is_set(): break
                        exp = row['exp'] if frame_type == 'DARK' else cfg['flat_exp']
                        count = row['count'] if frame_type == 'DARK' else cfg['flat_count']
                        with self.lock:
                            self.camera.set_control_value(asi.ASI_GAIN, row['gain'], auto=False)
                            if row['offset'] is not None:
                                self.camera.set_control_value(asi.ASI_BRIGHTNESS, row['offset'], auto=False)
                            self.camera.set_control_value(asi.ASI_EXPOSURE, round(exp*1_000_000), auto=False)
                            if cfg['cooled']:
                                self.camera.set_control_value(asi.ASI_TARGET_TEMP, row['temp'], auto=False)
                                self.camera.set_control_value(asi.ASI_COOLER_ON, 1, auto=False)
                        if cfg['cooled'] and not self._wait_temp(row['temp']): break
                        for seq in range(1, count+1):
                            if self.stop_event.is_set(): break
                            ts = datetime.now().astimezone()
                            utc = datetime.now(timezone.utc)
                            self.messages.put(('status', f'{frame_type} · {seq}/{count}'))
                            with self.lock:
                                image = self.camera.capture(initial_sleep=0.01, poll=0.15)
                                measured = self._get_temperature()
                                real_gain = self._get_control('Gain', asi.ASI_GAIN)
                                real_offset = self._get_control('Brightness', asi.ASI_BRIGHTNESS)
                            if image.ndim != 2 or image.dtype != np.uint16:
                                raise RuntimeError(f'Expected single-channel RAW16, received {image.shape} {image.dtype}')
                            tval = row['temp'] if cfg['cooled'] else measured
                            temp_txt = numeric_token(tval) if tval is not None else 'NA'
                            prefix = (f'{cfg["object"]}_{cfg["camera"]}_{frame_type}_{ts:%Y%m%d-%H%M%S}_'
                                      f'{temp_txt}_{row["gain"]}_{numeric_token(exp)}s')
                            number = seq
                            path = folders[frame_type] / f'{prefix}_{number:04d}.fits'
                            while path.exists():
                                number += 1
                                path = folders[frame_type] / f'{prefix}_{number:04d}.fits'
                            hdr = fits.Header()
                            hdr['IMAGETYP'] = frame_type
                            hdr['OBJECT'] = cfg['object'][:68]
                            hdr['INSTRUME'] = str(self.caminfo.get('Name','ZWO ASI'))[:68]
                            hdr['DATE-OBS'] = utc.isoformat(timespec='milliseconds')
                            hdr['EXPTIME'] = (exp, 'Exposure in seconds')
                            hdr['GAIN'] = real_gain if real_gain is not None else row['gain']
                            if real_offset is not None: hdr['OFFSET'] = real_offset
                            hdr['FILTER'] = row['filter'][:68]
                            hdr['SEQNUM'] = number
                            hdr['XBINNING'] = 1
                            hdr['YBINNING'] = 1
                            if measured is not None: hdr['CCD-TEMP'] = (measured, 'Sensor C')
                            if cfg['cooled']: hdr['SET-TEMP'] = (row['temp'], 'Sensor target C')
                            fits.PrimaryHDU(np.ascontiguousarray(image), header=hdr).writeto(path, overwrite=False)
                            writer.writerow({'utc_date':utc.isoformat(), 'file':str(path.relative_to(cfg['out'])),'type':frame_type,
                                             'object':cfg['object'],'camera':cfg['camera'],
                                             'target_temperature':row['temp'] if cfg['cooled'] else '',
                                             'measured_temperature':measured if measured is not None else '',
                                             'gain':real_gain if real_gain is not None else row['gain'],
                                             'offset':real_offset if real_offset is not None else '',
                                             'filter':row['filter'],'exposure_s':exp,'sequence':number})
                            fp.flush()
                            done += 1
                            self.messages.put(('progress',(done,cfg['total'])))
                            self.messages.put(('log',f'Saved {path.relative_to(cfg["out"])}'))
            self.messages.put(('status', f'{"Stopped" if self.stop_event.is_set() else "Completed"}: {done}/{cfg["total"]} FITS'))
        except Exception as exc:
            self.messages.put(('error',str(exc)))
        finally:
            if cfg['flats']:
                try:
                    self.panel.light(False)
                    self.messages.put(('log', 'INDI panel turned off at session end'))
                except Exception as exc:
                    self.messages.put(('log', f'WARNING: could not turn the panel off: {exc}'))
            self.messages.put(('done', None))

    def _poll(self):
        try:
            while True:
                kind, data = self.messages.get_nowait()
                if kind == 'ask':
                    prompt, event, result = data
                    result['approved'] = messagebox.askokcancel('User action required', prompt)
                    event.set()
                elif kind == 'log': self._log(data)
                elif kind == 'status': self.status.set(data)
                elif kind == 'progress':
                    n, total = data
                    self.bar['value'] = n
                    self.progress.set(f'{n} / {total}')
                elif kind == 'error':
                    self.status.set('Acquisition error')
                    self._log('ERROR: ' + data)
                    messagebox.showerror('Acquisition error',data)
                elif kind == 'done':
                    self.running = False
                    self.start_button.configure(state='normal' if self.camera else 'disabled')
                    self.stop_button.configure(state='disabled')
                    self.detect_button.configure(state='normal')
                    self.connect_button.configure(state='normal')
                    for widget in self.entries + self.extra_entries + [self.dir_entry, self.dir_button,
                                  self.sdk_button, self.flat_check, self.indi_connect_button, self.driver_entry]:
                        widget.configure(state='normal')
                    self.indi_start_server_button.configure(state='disabled' if self.indi_server.owned_running() else 'normal')
                    self.indi_stop_server_button.configure(state='normal' if self.indi_server.owned_running() else 'disabled')
                    state = 'normal' if self.panel.client is not None else 'disabled'
                    self.indi_test_button.configure(state=state)
                    self.indi_off_button.configure(state=state)
        except queue.Empty: pass
        self.root.after(120, self._poll)


if __name__ == '__main__':
    root = tk.Tk()
    DarkFlatApp(root)
    root.mainloop()
