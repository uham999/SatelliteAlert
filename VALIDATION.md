# Verification record — 7 October 2026

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
