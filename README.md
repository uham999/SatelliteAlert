# Satellite Alert

A Python program with a local browser dashboard. It downloads
[AMSAT nasabare.txt](https://www.amsat.org/tle/current/nasabare.txt) every **24 hours**
and continuously checks the satellites' elevation from your ground station.

![Satellite Alert dashboard with the optional Earth image](docs/dashboard.jpg)

Satellite Alert shows live elevation, azimuth, distance and upcoming qualifying
passes. A local sky plot displays the selected satellite's path. Visual alerts,
Windows sound and optional popups warn when a pass meets your minimum elevation.
Choose an alert at the minimum angle, at horizon rise, or **5, 10 or 20 minutes
before rise**. The default is **5 minutes before horizon rise**. The Earth
background is shown by default and can be switched off in the live view.
All application text is at least **18px**, with larger headings and values.

The default ground station and satellite list are:

- Latitude **51.825° N** (`51.825`).
- Longitude **0.375° W** (`-0.375`).
- Minimum elevation **35° above your local horizon**.
- Alert **5 minutes before horizon rise**, only for passes reaching that angle.
- RS-44, AO-73, SO-50, ISS, FO-29, JO-97 and AO-123.
- Observer height defaults to **0 metres above sea level**; set your station's
  actual height in Settings. Existing saved preferences are retained.

## Install on another Windows computer

1. On [the repository page](https://github.com/uham999/SatelliteAlert), choose
   **Code → Download ZIP**. Alternatively, use the standalone `SatelliteAlert.zip`
   supplied with this project. **Extract all files** into a normal writable
   folder, such as a SatelliteAlert folder inside your Documents folder.
   Open the extracted folder containing `Install.cmd` and `README.md`.
2. Install the **Python Install Manager** from
   [python.org](https://www.python.org/downloads/). Open a new terminal and run
   `pymanager install 3.14` to install Python 3.14. With the new manager,
   `py install 3.14` also works unless an older Python launcher takes precedence.
   If Python 3.14 is already installed, skip this step. The installer accepts
   Python 3.12 or later; this project has been tested on 3.12 and 3.14.
3. In the extracted folder, double-click **Install.cmd**. Internet access is needed. It creates a local
   `.venv` and installs **Skyfield 1.55** plus its dependencies: NumPy, sgp4,
   jplephem and certifi. No separate sound library is needed on Windows.
4. Once installation says it is complete, press a key to close its window.
   Double-click **Start.cmd**. Your browser opens at
   `http://127.0.0.1:8765/`. Keep the program's terminal running.
5. Click **Settings** to check your location, height, satellite list and alert
   preferences. Click **Test alert** to check your speakers and the banner.

Python's [Windows installation documentation](https://docs.python.org/3/using/windows.html)
explains the install manager and virtual environments.

Both `.bat` and `.cmd` are Windows command scripts and run through `cmd.exe` on
Windows 11. `.cmd` is provided as the main launcher; the original `Install.bat`
and `Start.bat` call the same scripts and still work. No administrator access is
needed when you extract to a folder you can write to. Do not run from inside
the ZIP or open the scripts in Python/IDLE.

Alternatively, open the extracted folder in File Explorer, right-click its
background and choose **Open in Terminal**. In PowerShell, run `.\Install.cmd`,
wait for completion, then run `.\Start.cmd`.
The `.\` prefix tells PowerShell to use the file in the current folder.

The installer prefers Python 3.14, falls back to an installed Python 3.12 or
later, and keeps an existing `.venv`. It prints the environment's actual Python
version. To move an existing installation to Python 3.14, extract the ZIP into
a fresh folder and run Install.cmd there; copy your settings.json if desired.
Advanced users can select an interpreter with `.\Install.cmd "C:\path\to\python.exe"`.

The equivalent manual PowerShell commands, from the extracted folder, are:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe satellite_alert.py
```

If port 8765 is already in use, launch with `--port 8766` and use the new address.
The dashboard only listens on this computer; it needs no cloud hosting/account.
Your coordinates stay in local settings and are not sent to AMSAT.

## macOS or Linux

Install Python 3.12 or later. On Linux, install your distribution's Python venv
package if `python3 -m venv` requests it. From the extracted folder:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python satellite_alert.py
```

On these systems, keep the browser dashboard open and click **Enable browser
sound**. Browser audio requires that click after each tab is opened. Background
tab throttling can delay browser-only sound; Windows uses Python's native audio
and popup independently of the browser.

## Alerts and the 35° angle

**Elevation is measured above the horizontal plane at your saved ground
position.** The geometric horizon is 0° and directly overhead is 90°.
Predictions use a WGS84 observer and SGP4 through
[Skyfield](https://rhodesmill.org/skyfield/earth-satellites.html). Atmospheric
refraction, local buildings, trees and terrain are not modelled.

By default the app alerts **5 minutes before horizon rise**, if the pass reaches
**35°**. Settings also offers **10 or 20 minutes before horizon rise**, **at
horizon rise**, and **when the satellite reaches the minimum elevation**. The early
options alert while the satellite is still below your local horizon. All five
timings retain the minimum predicted peak elevation requirement. Every pass in the table reaches the minimum.
It shows the selected alert time, horizon rise, the 35° crossing, peak time/elevation
and descent below 35°. The early alert date can be the previous day for a pass
rising shortly after midnight.
Time display can be switched between the computer's local time zone and UTC.

The **Earth image** checkbox in **Your Sky** switches the optional background on
or off immediately and saves your choice. This is the only Earth control; there
is no duplicate in Settings. The enlarged circle fills the available plot width.
The background is enabled for a new installation. The
image is decorative: the path still represents azimuth and elevation in your
local sky, rather than an orbit drawn over a geographical Earth map. Switching
the background does not silence an active alert or recalculate orbital data.
The transparent PNG is bundled locally, so it does not require a network
connection. It was created with the built-in ImageGen tool; its final prompt is
in `static/earth-overlay.prompt.txt`.

A real alert gives you:

- A large pulsing amber banner, with a reduced-motion alternative.
- A Windows chime, repeated every 30 seconds until acknowledged or the useful
  window ends. You can change/disable the sound in Settings.
- A Windows popup, enabled by default, that can appear over other applications.
  Pressing OK acknowledges that alert. You can disable the popup in Settings.
- Optional browser desktop notifications, enabled by your button click and your
  browser's permission decision. They are additional to the Windows popup.

**Test alert** is clearly labelled TEST. It plays the chime and shows the dashboard
banner for up to 60 seconds, without opening a Windows popup. Acknowledging silences
it immediately. A paused app still permits an explicitly requested test.

The Python monitor checks every **2 seconds**. It alerts once per satellite pass,
including a pass already underway at startup/resume. In early mode, starting
within the selected 5-, 10- or 20-minute warning window also catches the approaching pass. Alert history and pass
deduplication survive restart. Small time changes after a TLE refresh do not
produce a second alert for the same pass. Pausing stops real alerts and repeat
sound; resuming catches a currently qualifying pass.

## Automatic downloads and continuous operation

On **every program start**, the app immediately checks both the saved download
timestamp and the element epochs of your watched satellites. It refreshes if
data is missing, a watched satellite is missing, the download is at least
**1 day old**, or a watched satellite's elements are **older than 1 day**.
This startup check also applies if you set a longer routine download interval.

After the startup check, routine downloads follow the saved interval: by default,
exactly 24 hours after the last successful download. A manual refresh restarts
that 24-hour interval. Downloading
and recalculating run separately from the continuous position monitor.

The startup age check runs once per program start. If AMSAT returns the same old
element epochs, the app continues to show their actual age and follows the normal
download/retry schedule. It avoids repeatedly downloading unchanged data.
The **Refresh orbital data** button is at the top of the live view, next to
**Test alert**.

Failed downloads retain the last valid cache and retry after **5 minutes**. Bad
checksums, mismatched identities and malformed records are rejected. Missing
selected satellites and prediction failures are shown in the dashboard. A download
with no selected satellites is rejected instead of replacing the good cache.

Each satellite's **element epoch age** is checked separately from the download
age. Alerts are suppressed for elements more than **7 days** from their epoch
by default. The age limit is editable. Freshly downloading an old element set
does not make it fresh. TLEs are predictions with limited validity; they cannot
reliably predict any arbitrary date. Radio operation and naked-eye visibility
are separate from geometric elevation.

The computer must be **awake and the Python program running** to give timely
alerts. After sleep/restart, it catches a qualifying pass still in progress;
it does not play missed alerts for completed passes. Recalculate forecasts every
30 minutes, immediately after settings/data changes, and on wake as needed.

To start automatically on Windows, optionally add a shortcut to `Start.cmd` to
your user Startup folder (`shell:startup`, opened by you through Windows). This
runs at login. The installer does not change your startup settings. Do not schedule
the Python program to run only once daily: it must remain running to monitor passes.

Stop with **How it works → Stop app**, or Ctrl+C in the program's terminal.

## Files, validation and troubleshooting

- `satellite_alert.py`: local web server, daily downloader and continuous alerts.
- `orbits.py`: TLE validation, satellite identities and orbital calculations.
- `static/`: self-contained dashboard; no internet fonts/scripts are loaded.
- `settings.json`: editable settings, also editable through the dashboard.
- `settings.example.json`: starting settings; the app also creates defaults
  automatically if settings.json is absent. Your local settings.json is excluded
  from Git, as are caches, alert history and virtual environments.
- `data/elements.json`: downloaded TLE text and successful download timestamp.
- `data/alerts.json`: recent alert history and deduplication records.
- `data/satellite_alert.log`: rotating diagnostic log; retained locally.
- `tests/`: repeatable offline checks, including actual 35° crossing calculations.

You may copy `settings.json` to retain your preferences on another machine.
`data/` is created automatically. The delivery ZIP omits the virtual environment
and live cache/history; the other machine downloads its own current data.
**Settings → View your filtered orbital elements** shows just the selected
satellites' three-line TLE records. Known aliases resolve by NORAD number.

Run the verification suite with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

No sound: check mute/volume and Test alert. If the Windows audio API fails, the
dashboard offers browser sound as a fallback. If a popup is already open, close
it with OK. No data: check the internet connection and the diagnostic log; use
Refresh orbital data. A disconnected dashboard means the Python process is
stopped or unreachable. Check the terminal before restarting. An invalid manually
edited `settings.json` is reported at startup; correct it or rename it to retain
it and let the app create fresh defaults.

Test results verify software behaviour and orbital geometry, not physical
satellite reception or speaker audibility at your other computer. Use Test alert
on that computer before relying on it.

See [VALIDATION.md](VALIDATION.md) for the full verification record and limits.
The GitHub Actions workflow runs the offline suite on Windows with Python 3.12
and 3.14 for pushes and pull requests. It does not run the live monitor or
download current orbital data.
