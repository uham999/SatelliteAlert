# Verification record — 7–8 October 2026

The app was launched on Windows inside Codex using Python 3.12.14.
The supplied requirements pin the versions used for verification:
Skyfield 1.55, NumPy 2.5.3, sgp4 2.27, jplephem 2.24 and certifi 2026.7.22.

- **40 automated tests passed.** Tests include actual ISS elevation at the
  predicted 35° crossings, horizon crossings, sensitivity to observer location,
  underway passes, alerts once per pass, restart persistence, acknowledgement,
  pause/resume, old-element suppression and HTTP settings/actions.
- The 24-hour refresh schedule and 5-minute failure retry were checked using an
  injected clock and controlled downloads; we did not wait 24 hours in this run.
- A real HTTPS download from the requested AMSAT URL contained **97 valid TLE
  records**, with no rejected records, at the time of verification. All **seven**
  revised satellite entries resolved by their NORAD catalogue numbers.
- The live dashboard showed seven satellites, their changing elevation/azimuth
  and qualifying passes for the confirmed ground station (51.825°, -0.375°).
- The location, 35° threshold and revised list were checked in the Settings UI;
  saving successfully triggered recalculation. The browser reported no console
  warnings/errors in the checked dashboard, and it fit the Codex panel width.
- A clearly labelled TEST banner and Windows chime path were exercised through
  the dashboard. Physical sound volume/audibility needs checking on the target
  computer. Real Windows popups are implemented; no real pass popup was needed
  to demonstrate the program during verification.

Skyfield issues a NumPy 2.5 DeprecationWarning in the test run; all calculations
and checks passed. The warning concerns a future NumPy API change. Requirements
are pinned to the versions that passed, rather than silently taking a later
NumPy version.

This is software validation, not a hardware reception test. Current orbital
elements, satellite manoeuvres and your local obstructions affect real passes.
The supplied offline ISS fixture is solely for deterministic tests and is never
used as the program's live orbital data.

## Additional timing and Earth background verification

- The early timing is exactly 600 seconds before the calculated local horizon
  rise, including when that time falls on the previous day.
- A simulated monitor fires the early alert while ISS elevation is below zero,
  catches startup within the warning window, and avoids duplicate alerts after
  restarting. Qualifying passes still meet the minimum elevation.
- The Earth setting is optional, validated as a boolean, saved and restored.
  Changing it preserves predictions and unacknowledged active alerts.
- The generated transparent PNG is served locally with image/png content type
  and included in the distribution ZIP with its final ImageGen prompt.
- In the running Codex dashboard, the new timing appeared alongside the two
  existing choices. The Earth checkbox was switched off and back on; each state
  was visible and saved. The Earth-enabled sky plot retained the satellite path,
  elevation ring and local-sky labels. Existing timing and the user's saved
  observer height of 100 metres were retained.

## Python 3.14 and Windows launchers

- **40 offline tests also passed with Python 3.14.8 (Windows x64)** using the
  same pinned dependencies. `pip check` reported no broken requirements.
- The official Python 3.14.8 runtime archive was downloaded into an isolated
  workspace folder and its SHA-256 matched the Python release manifest:
  `4873947a8afc037846b180312b83c744a4146a851cfd316a75c3125a4d8299da`.
  This did not install or register Python system-wide.
- Install.cmd created a fresh Python 3.14 virtual environment and installed
  all pinned dependencies from a folder whose name contains spaces. An explicit
  interpreter path was used for this check because the computer's existing
  launcher does not register this isolated runtime.
- The README now explains Python 3.14 installation, File Explorer double-click
  and PowerShell launch instructions. Both .cmd and .bat launchers are supplied;
  the .bat files delegate to the .cmd files. Existing private environments are
  reused, and the installer reports their actual Python version.
- Install.bat successfully reused that Python 3.14 environment, and Start.cmd
  and Start.bat both launched the application's --help command successfully.
- The running Codex preview continues to use Python 3.12.14. The Python 3.14
  verification covers orbital calculations, alert logic and local HTTP handling.

## Readability, startup freshness and additional warning times

- **49 tests passed on both Python 3.12.14 and Python 3.14.8** after these changes.
  New checks cover 5- and 20-minute warning boundaries, midnight transitions,
  alarms while below the horizon, qualifying peaks and restart deduplication.
- Real maintenance threads were exercised with controlled data: startup downloads
  missing data, refreshes a 24-hour-old download despite a longer saved interval,
  refreshes a recent download whose watched elements are older than 1 day, and
  refreshes when a watched satellite is missing. Fresh cache avoids a download.
  Offline startup preserves the good cache and the 5-minute retry backoff.
- New installations default to the 5-minute warning and an enabled Earth image.
  The running preview was set to these requested choices while retaining the
  user's coordinates, observer height of 100 metres and satellite list.
- Typography now has an 18px minimum, including responsive labels, buttons,
  menus, links, table text and canvas labels. Larger headings and values were
  increased in proportion. The sky plot uses a square aspect ratio to enlarge
  the Earth circle across the available panel width.
- The duplicate Earth checkbox was removed from Settings. The live Earth
  toggle remains the sole control and saves its choice. Refresh orbital data
  was moved next to Test alert at the top of the live page.
- Browser checks found no rendered text below 18px in the live view, Settings,
  alert history or help. At the 592px preview width the sky canvas is 477px
  square, versus its former 268px height; the page has no horizontal overflow.
  The 20-minute warning was saved through Settings and its actual alert time
  was confirmed 20 minutes before rise. The 5-minute choice was restored.
  Switching Earth off, saving Settings, and switching it back on demonstrated
  that the sole live control persists correctly. The relocated Refresh button
  successfully downloaded AMSAT data and retained all seven watched satellites.

## Daily automatic pause and next-alert watch order

- **68 offline tests passed on Python 3.12.14 and Python 3.14.8**. New checks
  cover exact daily start/end boundaries, fractional durations, crossing midnight,
  24-hour pauses, local clock offsets, a modelled daylight-saving clock jump,
  default migration, invalid settings, restart persistence and corrupt state recovery.
- Manual Resume overrides the current automatic period and the next day's pause
  still applies. Manual Pause persists beyond the scheduled end until Resume.
  Changing schedule settings clears the override without rebuilding predictions
  or acknowledging active alerts. The local HTTP API was exercised end to end.
- Real Skyfield positions and downloads continue while paused. Controlled ticks
  confirm no new alerts, ledger entries, sound calls or popup requests during
  the pause, and an eligible pass alerts when manually resumed. Repeat sound
  stops on manual pause. These checks do not establish physical speaker playback
  or operating-system popup behavior on another computer.
- Watch-list checks cover earliest pending alert order, equal-time stability,
  missing/no-pass rows last, completed passes and skipping already alerted passes.
- In the running Codex dashboard, a temporary 24-hour setting triggered an
  automatic pause and displayed the requested message. Resume overrode it.
  The default enabled 00:00 / 7-hour schedule was restored through Settings;
  manual Pause displayed the same message and Resume removed it.
- The live watch list was confirmed in predicted alert order, with FO-29 first.
  New controls retain the 18px minimum and the preview has no horizontal overflow.
  The screenshot in docs/automatic-pause.jpg shows the restored defaults.
  Coordinates, 100-metre station height, satellite list, 5-minute timing and Earth
  overlay were retained. The monitor is running with manual pause disabled.

## Enlarged Earth, satellite editor and serial tracking — 8 October 2026

- **93 offline Python tests passed on Windows with Python 3.12.14 and 3.14.8**.
  Both environments passed `pip check`. pySerial 3.5 was added to the pinned
  runtime requirements. All earlier alert, quiet-hours, startup freshness,
  geometry and local HTTP tests remain in the suite.
- **13 offline interface checks passed** with Node.js 22.14.0 and jsdom 26.1.0.
  They exercise the actual dashboard HTML/JavaScript with simulated HTTP, dialog
  and canvas APIs: click/keyboard enlargement, all visible markers, moving
  positions, highest-elevation prominence, both Earth/sky modes, the sole optional
  image toggle, frequency CRUD, serial settings, master control and paused alerts.
  These are DOM and drawing-command checks, not browser screenshots or a
  rendered-layout validation. JavaScript syntax validation also passed.
- Geographic ground positions reconstruct the propagated orbit within 1 cm in
  the tested ISS cases. The globe uses WGS84 satellite latitude/longitude and an
  orthographic projection centred on the observer. An independent formula checks
  marker placement and confirms that changing longitude moves a marker even
  when azimuth/elevation are unchanged. Natural Earth land polygons and pinned
  D3 scripts are bundled locally with licences and source provenance.
- Line-of-sight velocity was compared with independent finite differences of
  range at several times. Doppler tests cover approach/recession, zero velocity,
  sign, magnitude, receive-only profiles and invalid values. Default frequency
  pairs were checked against AMSAT's current linear/FM tables. Linear defaults
  are transponder centres, not a single mandatory operating channel.
- Serial simulators verify GS-232B combined commands, the accumulating 1°
  deadband on both axes, north wrapping, configured 450° overlap, position
  replies, a single stop on loss of target and stale-observation suppression.
  A failed stop closes the connection, reports that the controller needs action
  and resets software state before another start.
- CI-V checks cover five-byte BCD, independently signed RX/TX corrections,
  MAIN/SUB selection, modes, tuning deadband, frequency readback, fragmented
  frames, echoes, unsolicited broadcasts, rejection/timeout handling and
  incorrect readback. Device errors stop both controls. Modem-control lines
  start inactive and no PTT command is sent. Receive-only profiles do not tune
  SUB. Unsupported frequencies and same-band duplex are rejected.
- Highest **current** elevation selects one target for both devices, from all
  watched satellites above the horizon by default. Predicted maximum elevation
  does not choose the target. Old/stale satellite data cannot drive equipment.
  Only the latest observation is retained; obsolete pointing commands are not
  queued. Equipment runs on a separate thread from alert/orbit monitoring.
- The actual HTTP server was exercised for detected ports, profile CRUD and
  persistence, equipment settings, the master endpoint, geographic state and
  locally served map assets. Frequency-only edits preserve orbital predictions.
  Equipment-affecting changes stop live control. Hardware remains off after
  restart, and alert-pause controls remain independent of equipment control.
- A fresh extraction of the delivery ZIP into a folder with spaces successfully
  ran Install.cmd using Python 3.14.8, installed all six pinned dependencies,
  passed `pip check` and all 93 Python tests, and ran both Start.cmd and Start.bat
  with `--help`. No runtime, cache, history or Git directory is included in the ZIP.
- GitHub's [Windows CI run for implementation commit 791c070](https://github.com/uham999/SatelliteAlert/actions/runs/37744559351)
  passed, including both Python 3.12/3.14 jobs, the 13 interface checks and the
  command-script checks.

The updated Python monitor was launched in Codex with fresh AMSAT elements.
Serial enumeration found the PC's Bluetooth virtual ports, but no hardware port
was selected or opened. **No physical GS-232B or IC-9700 was connected or tested**.
Acknowledgements/readback in these tests are simulated; motor movement, calibration,
actual radio state, USB-interface behaviour, reception and audibility need checking
on the target equipment. The app does not key the transmitter or set CTCSS tones.

Codex's browser-control process failed to start during this update, so a new
visual interaction/rendering check and screenshot could not be completed. Earlier
screenshots and layout checks above describe the earlier app version. The new
automated interface checks do not establish current rendered appearance. The
local app is available for viewing; refresh the page to load its new interface.
