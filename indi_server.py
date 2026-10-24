"""Launch and manage only the INDI server owned by this application."""
import os
import shutil
import socket
import subprocess
import threading
import time


class IndiServerManager:
    def __init__(self):
        self.process = None
        self.lock = threading.RLock()

    @staticmethod
    def listening(host, port, timeout=0.4):
        try:
            with socket.create_connection((host, int(port)), timeout=timeout):
                return True
        except OSError:
            return False

    def start(self, host, port, driver='indi_flipflat', timeout=8):
        if host.strip().lower() not in ('localhost', '127.0.0.1', '::1'):
            raise ValueError('The INDI server can only be started locally (localhost).')
        port = int(port)
        if not 1 <= port <= 65535:
            raise ValueError('Invalid INDI port.')
        if not driver or not all(ch.isalnum() or ch in '_-' for ch in driver):
            raise ValueError('Invalid INDI driver name.')
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise RuntimeError('INDI server already started by this application.')
            if self.listening('127.0.0.1', port):
                raise RuntimeError(f'Port {port} is already in use. Connect to the existing server instead.')
            executable = shutil.which('indiserver')
            if executable is None:
                raise RuntimeError('indiserver not found. Install the INDI server and driver.')
            if shutil.which(driver) is None:
                raise RuntimeError(f'Cannot find INDI driver {driver}. Install it or specify the correct executable.')
            # Do not use shell=True. A dedicated process group avoids killing unrelated services.
            self.process = subprocess.Popen([executable, '-p', str(port), driver],
                                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL, start_new_session=True)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                rc = self.process.returncode
                self.process = None
                raise RuntimeError(f'indiserver exited at startup (code {rc}). Run indiserver manually to see diagnostic messages.')
            if self.listening('127.0.0.1', port):
                return
            time.sleep(0.15)
        self.stop()
        raise TimeoutError('INDI server did not open the port before the timeout.')

    def stop(self):
        with self.lock:
            proc = self.process
            self.process = None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)

    def owned_running(self):
        return self.process is not None and self.process.poll() is None
