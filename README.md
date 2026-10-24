# ZWO Calibration Library GUI

**Version 1.0.0 — English Edition**

A desktop Python application for acquiring calibration frames with ZWO ASI astronomy cameras on Linux. It captures **DARK**, **DARKFLAT**, and **FLAT** images in RAW16 FITS format, organizes files by object, and controls a compatible INDI light panel (tested in design against the Optec Flip-Flat driver interface).

This English edition is based on the former Spanish version 2.2.3, with application-facing text and documentation translated. The 1.0.0 number is a release reset, not a downgrade of the acquisition features.

## Features

- Tkinter desktop GUI with camera discovery and connection via the ZWO ASI SDK.
- Display of camera model, resolution, color/cooling capability, sensor temperature, and available controls.
- Configurable object label, filter metadata, gain, offset (where available), target temperature (cooled cameras), DARK exposure and DARK count.
- Independent FLAT exposure and FLAT count; automatically records the same number of DARK-FLATS at the same FLAT exposure.
- Direct INDI TCP/XML client; **no `pyindi-client` or `dbus-python` dependency**.
- Option to start/stop a *locally managed* INDI server (`indiserver`) and to connect to an existing server.
- Automatic local serial-port discovery from `/dev/serial/by-id/`, `/dev/ttyUSB*`, and `/dev/ttyACM*`, with manual override.
- Light control and brightness setting from **0–255**, subject to the INDI driver's actual limits.
- Session progress, log, user confirmations, CSV inventory, and lossless FITS metadata.

## Requirements

- Ubuntu or another Linux distribution with Python 3, Tkinter and a working USB interface.
- ZWO **ASI Camera SDK** shared library, `libASICamera2.so`.
- Python packages in `requirements.txt`: `zwoasi`, `numpy`, `astropy`.
- For automated flats: installed `indiserver` and the `indi_flipflat` driver, or an existing compatible INDI server/lightbox driver.
- A ZWO ASI camera, and a compatible FLAT panel if the FLAT/DARK-FLAT option is enabled.

### Install on Ubuntu

```bash
sudo apt update
sudo apt install python3 python3-tk python3-venv
# Install the INDI server and Flip-Flat driver if not already available.
# Confirm: command -v indiserver && command -v indi_flipflat

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python dark_flat_gui.py
```

Download the ASI Camera SDK from the [official ZWO SDK page](https://www.zwoastro.com/software/product-sdk/). Install the Linux shared library or choose its existing path using **Select SDK…**. A typical path is `/usr/local/lib/libASICamera2.so`. You can also set `ZWO_ASI_LIB` in the environment.

```bash
python -c "import ctypes; ctypes.CDLL('/usr/local/lib/libASICamera2.so'); print('ASI SDK OK')"
```

If Linux cannot access the camera or the panel, check USB permissions and udev rules. Install device-specific rules as recommended by the manufacturer. For serial ports owned by `dialout`, ensure your account belongs to that group, then sign out and back in.

## User interface and settings

| Section | Controls | Notes |
|---|---|---|
| ZWO ASI SDK | SDK library path, **Find cameras**, **Select SDK…** | Locate the native library and enumerate cameras |
| Camera | Camera selector, **Connect** | Shows connected-camera information |
| General | **Object / label**, **Filter** | Filter is metadata; no filter-wheel commands are sent |
| Capture settings | Target temperature, gain, offset, DARK exposure, **Number of DARKS**, output directory | Temperature control applies only to cooled models; noncooled cameras log measured temperature when available |
| DARK-FLAT / FLAT acquisition | FLAT exposure, **Number of FLATS**, capture option | DARK-FLAT count automatically equals FLAT count |
| FLAT panel · INDI server | Host, TCP port, optional device, driver, serial port, brightness | The serial-port detector scans the **local** computer only |
| Actions | Start acquisition, stop after current frame | FITS files are saved incrementally |

### Configure the camera

1. Connect the ZWO camera to USB.
2. Specify the ASI SDK library path and select **Find cameras**.
3. Choose the camera and select **Connect**.
4. Under **General**, specify the object label (for example `M42`) and filter (for example `Bessell-V`).
5. Set target temperature (if cooled), gain, offset, DARK exposure and DARK count.
6. Set the FLAT exposure and FLAT count and choose an output root folder.

**Important:** ensure the DARK exposure and FLAT exposure are within the camera's allowed limits. For proper calibration, use the same gain, offset and camera acquisition mode as the corresponding science frames. The program does not automatically select physical filters.

### Start or connect to INDI

1. Connect the Flip-Flat (or compatible light panel) to the computer hosting the INDI driver.
2. Set **Host** (`localhost` by default), **Port** (`7624`), and **Driver** (`indi_flipflat`).
3. Select **Start INDI server** only when you want this application to create a new local server. If Ekos already has a running INDI server, use that server instead; do not start a second one on the same port.
4. Select **Scan ports**. The application prefers persistent `/dev/serial/by-id/` paths and allows manual port selection.
5. Select **Connect panel**. The client connects via TCP, sets `DEVICE_PORT`, sends the device `CONNECTION/CONNECT` switch, and waits for the light-control properties.
6. Specify **Brightness (0–255)**, then optionally test **Set brightness / turn on** and **Turn off**.

Typical manual server command:

```bash
indiserver -p 7624 indi_flipflat
```

The application can stop only the INDI server process that it started. Serial-port auto-detection does **not** discover ports on remote INDI hosts; provide the remote host's valid device path manually if applicable.

## Calibration workflow

The acquisition phases run in this exact sequence:

1. **DARK** — camera/optics fully dark; DARK exposure; DARK count. The application commands the light panel off before acquisition.
2. **DARKFLAT** — still fully dark; same FLAT exposure, gain, offset, temperature and filter as the subsequent FLATS; count equals FLAT count. The application asks for confirmation before continuing.
3. **FLAT** — uncover and arrange uniform illumination, then confirm the prompt. The program turns on the panel at the configured brightness and waits briefly for stabilization.
4. **Finish** — the application sends the panel-off command, closes the session CSV and displays the final progress.

**Do not assume the Flip-Flat cover moves automatically.** The application controls the light source, not the mechanical cover. Follow the prompts and manually check that DARK/DARKFLAT frames are dark and FLATS are illuminated. If the light panel cannot be switched off, stop rather than continue with potentially contaminated DARK frames.

For cooled cameras, the application waits until the temperature is within 1 °C of the target for 30 seconds (up to 15 minutes). For noncooled cameras, the temperature cannot be actively regulated.

## Output folders and filenames

For an object label `M42`, the following directories are created under your selected output root:

```text
<output-root>/
└── M42/
    ├── DARK/
    │   └── M42_ZWO-ASI585MM-Pro_DARK_20261024-110000_m10_100_60s_0001.fits
    ├── DARKFLATS/
    │   └── M42_ZWO-ASI585MM-Pro_DARKFLAT_20261024-110500_m10_100_0p1s_0001.fits
    ├── FLATS/
    │   └── M42_ZWO-ASI585MM-Pro_FLAT_20261024-110700_m10_100_0p1s_0001.fits
    └── CALIBRATION_session_20261024_110000.csv
```

Names follow:

```text
OBJECT_CAMERA_DARK_DATE_TEMPERATURE_GAIN_EXPOSURE_SEQUENCE.fits
OBJECT_CAMERA_DARKFLAT_DATE_TEMPERATURE_GAIN_EXPOSURE_SEQUENCE.fits
OBJECT_CAMERA_FLAT_DATE_TEMPERATURE_GAIN_EXPOSURE_SEQUENCE.fits
```

The example filenames are illustrative; actual camera values and capture times determine the names. Negative temperatures are represented as tokens such as `m10`, and decimal exposure seconds as `0p1s`. The actual field name and camera name are sanitized for file safety. On an uncooled camera where no temperature reading is available, a placeholder is used.

The timestamp in the filename uses the computer's local time. `DATE-OBS` and the CSV time are recorded in UTC. If a filename already exists, the sequence number is advanced rather than overwriting an existing frame.

### FITS metadata

Each image is an uncalibrated RAW16 FITS frame. Its header includes, where supported: `IMAGETYP` (`DARK`, `DARKFLAT`, `FLAT`), `OBJECT`, `INSTRUME`, `DATE-OBS`, `EXPTIME`, `GAIN`, `OFFSET`, `FILTER`, `CCD-TEMP`, `SET-TEMP`, `SEQNUM`, `XBINNING` and `YBINNING`. Offset and measured temperature are omitted if unavailable. The application does not subtract DARKS from the raw frames.

The CSV log includes UTC date, relative file path, frame type, object, camera, target/measured temperature, gain, offset, filter, exposure seconds and sequence number. CSV column headings are in English in this edition.

## Troubleshooting

**ASI SDK cannot be loaded** — Find `libASICamera2.so`, set the SDK path via **Select SDK…**, and use `ldd /path/to/libASICamera2.so` to diagnose missing native dependencies.

**No ZWO camera found** — Check camera USB connectivity, permissions, power, and whether another application currently owns it.

**INDI server does not start** — Check `command -v indiserver`, `command -v indi_flipflat`, and whether TCP port 7624 is already in use. A server created by Ekos is not controlled by this program.

**INDI connects but has no FLAT_LIGHT_* properties** — Confirm the underlying serial device is actually connected (the INDI `CONNECTION` property must indicate connected). Those controls may only appear after the driver connects to the physical panel.

**DEVICE_PORT / no such file or directory** — Re-run **Scan ports**, choose a real `/dev/serial/by-id/` or `/dev/ttyUSB*` device, and check permissions. Avoid hard-coding a stale serial device path.

**Multiple serial devices** — Choose the Flip-Flat explicitly. Candidate detection is a heuristic, not a hardware identity guarantee.

**Brightness fails** — Confirm your INDI driver publishes `FLAT_LIGHT_CONTROL` and `FLAT_LIGHT_INTENSITY` and supports the requested numeric range.

**Too-dark or saturated FLATS** — Adjust exposure or panel brightness to obtain an appropriate signal level. The application does not automatically measure an ADU target or determine optimal exposure.

**Stopped acquisition** — Stopping is cooperative: an in-progress exposure may be completed before the application stops. Inspect the CSV and output folder to identify completed frames.

## Included files

```text
dark_flat_gui.py       Main GUI and acquisition workflow
legacy_core.py         ZWO ASI camera access and base interface
indi_panel.py          Native INDI TCP/XML protocol client
indi_server.py         Local INDI server process management
serial_detector.py     Linux serial device detection
requirements.txt       Python dependencies
README.md              This guide
LICENSE                CC BY 4.0 license notice and official legal-text link
```

## Known limitations

- This application has been syntax-checked, but complete end-to-end capture with a real ZWO camera and Optec panel must still be validated on the target machine.
- No physical filter-wheel control; filter value is informational and saved in FITS.
- No automatic Flip-Flat cover movement.
- Flat brightness is manually specified; no automatic exposure/ADU feedback loop.
- Local serial auto-discovery cannot inspect ports on remote computers.
- It is intended for a single acquisition run, not an exposure/gain/temperature matrix.

## License

Licensed under **Creative Commons Attribution 4.0 International (CC BY 4.0)**. See [`LICENSE`](LICENSE) and the [official license text](https://creativecommons.org/licenses/by/4.0/legalcode.en). You may share and adapt the work, including commercially, provided you give appropriate attribution, provide a link to the license, and indicate if changes were made. No warranties are provided.

For software, CC BY 4.0 is not the usual recommended code license, but this project uses it as requested. Note that ZWO and INDI and their dependencies retain their own respective licenses; this CC BY 4.0 notice covers only the original project materials.
