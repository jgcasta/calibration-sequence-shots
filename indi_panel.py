"""Direct INDI control via TCP sockets and XML, without PyIndi/dbus.

The server publishes properties as def*Vector; many drivers publish light panel
properties only after receiving CONNECTION/CONNECT.
"""
import socket
import threading
import time
import xml.etree.ElementTree as ET


class IndiPanel:
    def __init__(self):
        self.client = None  # socket TCP, conservado para compatibilidad con GUI
        self.device_name = ''
        self.lock = threading.RLock()
        self.parser = None
        self.properties = {}
        self.devices = set()
        self.host = ''
        self.port = 0
        self.last_messages = []

    def _send(self, xml):
        if self.client is None:
            raise RuntimeError('No TCP connection to INDI')
        try:
            self.client.sendall((xml + '\n').encode('utf-8'))
        except OSError as ex:
            self.disconnect()
            raise RuntimeError(f'TCP send failure: {ex}') from ex

    def _poll(self, seconds=0.25):
        if self.client is None:
            raise RuntimeError('TCP connection closed')
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.client.settimeout(min(0.2, max(0.01, deadline - time.monotonic())))
            try:
                chunk = self.client.recv(16384)
            except socket.timeout:
                continue
            except OSError as ex:
                raise RuntimeError(f'INDI TCP receive failure: {ex}') from ex
            if not chunk:
                raise RuntimeError('INDI server closed the TCP connection')
            try:
                self.parser.feed(chunk.decode('utf-8', errors='replace'))
                for event, node in self.parser.read_events():
                    if event != 'end':
                        continue
                    if node.tag in ('defSwitchVector','defNumberVector','setSwitchVector','setNumberVector',
                                    'defTextVector','setTextVector','defLightVector','setLightVector'):
                        device, name = node.get('device', ''), node.get('name', '')
                        if device and name:
                            self.devices.add(device)
                            entries = {child.get('name'): {'value': (child.text or '').strip(),
                                       'min': child.get('min'), 'max': child.get('max')}
                                       for child in node if child.get('name')}
                            if node.tag.startswith('set') and (device, name) in self.properties:
                                old = self.properties[(device, name)]
                                old['elements'].update(entries)
                                old['state'] = node.get('state', old.get('state',''))
                            else:
                                self.properties[(device, name)] = {'kind': node.tag[3:],
                                    'elements': entries, 'state': node.get('state','')}
                    elif node.tag == 'message':
                        message = (node.get('message') or '').strip()
                        if message:
                            self.last_messages.append(message)
                            self.last_messages = self.last_messages[-15:]
                    elif node.tag == 'delProperty':
                        dev, name = node.get('device',''), node.get('name')
                        if name:
                            self.properties.pop((dev, name), None)
                        else:
                            for key in list(self.properties):
                                if key[0] == dev: self.properties.pop(key)
                    if node.tag.endswith('Vector') or node.tag == 'delProperty':
                        node.clear()
            except ET.ParseError as ex:
                raise RuntimeError(f'Invalid INDI XML: {ex}') from ex

    def _wait_for(self, predicate, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate(): return True
            self._poll(min(0.3, deadline - time.monotonic()))
        return predicate()

    def _switch(self, device, prop, selected):
        from xml.sax.saxutils import escape, quoteattr
        p = self.properties.get((device, prop))
        if not p:
            raise RuntimeError(f'Property not received: {prop} para {device}')
        names = list(p['elements'])
        if selected not in names:
            raise RuntimeError(f'{prop}: falta {selected}; elementos: {names}')
        children = ''.join(f'<oneSwitch name={quoteattr(name)}>{"On" if name == selected else "Off"}</oneSwitch>'
                           for name in names)
        self._send(f'<newSwitchVector device={quoteattr(device)} name={quoteattr(prop)}>{children}</newSwitchVector>')

    def _set_serial_port(self, device, serial_port):
        from xml.sax.saxutils import quoteattr, escape
        prop = self.properties.get((device, 'DEVICE_PORT'))
        if not prop or prop['kind'] not in ('Text', 'TextVector'):
            raise RuntimeError('The driver does not publish DEVICE_PORT as an INDI text vector (defTextVector)')
        field = 'PORT' if 'PORT' in prop['elements'] else next(iter(prop['elements']), None)
        if not field:
            raise RuntimeError('DEVICE_PORT contains no elements')
        self._send(f'<newTextVector device={quoteattr(device)} name="DEVICE_PORT">'
                   f'<oneText name={quoteattr(field)}>{escape(serial_port)}</oneText></newTextVector>')
        self._wait_for(lambda: self.properties.get((device, 'DEVICE_PORT'), {}).get('elements', {}).get(field, {}).get('value') == serial_port, 2.5)

    def connect(self, host, port, device_name='', timeout=16, serial_port=''):

        with self.lock:
            self.disconnect()
            self.host, self.port = str(host).strip(), int(port)
            self.last_messages.clear()
            try:
                self.client = socket.create_connection((self.host, self.port), timeout=3)
                self.parser = ET.XMLPullParser(events=('end',))
                self.parser.feed('<INDI_ROOT>')
                self._send('<getProperties version="1.7"/>')
                self._wait_for(lambda: bool(self.devices), min(3.5, timeout))
                if not self.devices:
                    raise RuntimeError('TCP connected, but the INDI server reported no devices. Check indi_flipflat.')
                candidates = [device_name] if device_name else sorted(self.devices,
                    key=lambda n: (not any(w in n.lower() for w in ('flip', 'flat', 'optec')), n.lower()))
                selected = None
                for dev in candidates:
                    if dev in self.devices:
                        selected = dev
                        break
                if not selected:
                    raise RuntimeError(f'Device {device_name!r} not found. Available: {sorted(self.devices)}')
                self.device_name = selected
                # Connect the hardware via CONNECTION; FLAT_* properties usually appear afterwards.
                self._wait_for(lambda: (selected, 'CONNECTION') in self.properties, 3)
                conn = self.properties.get((selected, 'CONNECTION'))
                if serial_port.strip():
                    self._wait_for(lambda: (selected, 'DEVICE_PORT') in self.properties, 3)
                    self._set_serial_port(selected, serial_port.strip())
                if conn and 'CONNECT' in conn['elements']:
                    self._switch(selected, 'CONNECTION', 'CONNECT')
                else:
                    raise RuntimeError(f'Device {selected}: CONNECTION/CONNECT was not published. '
                                       f'Propiedades: {self.list_properties(selected)}')
                def is_ready():
                    return ((selected,'FLAT_LIGHT_CONTROL') in self.properties and
                            (selected,'FLAT_LIGHT_INTENSITY') in self.properties)
                if not self._wait_for(is_ready, timeout):
                    connection = self.properties.get((selected, 'CONNECTION'), {})
                    state = connection.get('state', '(desconocido)')
                    switches = connection.get('elements', {})
                    connected = switches.get('CONNECT', {}).get('value', '?')
                    messages = ' | '.join(self.last_messages[-4:]) or '(no diagnostic messages)'
                    port_prop = self.properties.get((selected, 'DEVICE_PORT'), {})
                    ports = port_prop.get('elements', {})
                    port_value = ', '.join(f'{n}={v.get("value", "")}' for n, v in ports.items())
                    raise RuntimeError(
                        f'Driver {selected}: light-control properties are not available.\n'
                        f'CONNECTION: estado={state}, CONNECT={connected}.\n'
                        f'Serial port: {port_value or "(desconocido)"}.\n'
                        f'Mensajes INDI: {messages}\n'
                        f'Propiedades: {self.list_properties(selected)}')
                return selected
            except Exception:
                self.disconnect()
                raise

    def list_properties(self, dev=None):
        return ', '.join(sorted(name for d, name in self.properties if dev is None or d == dev)) or '(ninguna)'

    def disconnect(self):
        if self.client is not None:
            try: self.client.close()
            except OSError: pass
        self.client = None
        self.device_name = ''
        self.parser = None
        self.properties.clear()
        self.devices.clear()

    def set_brightness(self, value):
        value = int(value)
        if not 0 <= value <= 255:
            raise ValueError('Brightness outside 0–255')
        with self.lock:
            key = (self.device_name, 'FLAT_LIGHT_INTENSITY')
            prop = self.properties.get(key)
            if not prop:
                self._poll(0.3)
                prop = self.properties.get(key)
            if not prop:
                raise RuntimeError('INDI does not provide FLAT_LIGHT_INTENSITY')
            names = prop['elements']
            name = 'FLAT_LIGHT_INTENSITY_VALUE' if 'FLAT_LIGHT_INTENSITY_VALUE' in names else next(iter(names))
            info = names[name]
            lo = float(info['min']) if info['min'] is not None else 0
            hi = float(info['max']) if info['max'] is not None else 255
            if not lo <= value <= hi:
                raise ValueError(f'Brightness outside INDI-supported range: {lo:g}–{hi:g}')
            from xml.sax.saxutils import quoteattr
            self._send(f'<newNumberVector device={quoteattr(self.device_name)} name="FLAT_LIGHT_INTENSITY">'
                       f'<oneNumber name={quoteattr(name)}>{value}</oneNumber></newNumberVector>')

    def light(self, enabled):
        with self.lock:
            self._switch(self.device_name, 'FLAT_LIGHT_CONTROL',
                         'FLAT_LIGHT_ON' if enabled else 'FLAT_LIGHT_OFF')

    def prepare_dark(self): self.light(False)

    def prepare_flat(self, brightness):
        self.set_brightness(brightness)
        self.light(True)
