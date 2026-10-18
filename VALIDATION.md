# Verification record

## Automatic acknowledgement, chosen alarm volume and optional equipment — 18 October 2026

- **178 offline Python tests passed** on Windows with **Python 3.12.14** and
  **3.14.8**. New checks cover the default 60-second deadline and exact boundary,
  independent overlapping alarms, repeat chimes not extending deadlines,
  persistence across restarts, migration of older alert history, manual mode,
  early manual acknowledgement, and updated policy for already pending alarms.
  An actual local HTTP test saves the duration/volume and observes automatic
  acknowledgement through the dashboard API using an injected clock.
- **42 dashboard DOM checks passed**, including the new default options, saved
  duration and volume, disabling unused numeric inputs, notification-only mode,
  automatic acknowledgement in history, and closing browser notifications when
  the acknowledged state arrives. These use jsdom; no rendered-screen inspection
  is claimed.
- A fake muted playback endpoint verifies setting the requested percentage,
  unmuting, and confirmed readback before WAV playback. Volume-control failures
  still attempt playback and report the failure. Tests do not alter the real PC
  volume or require speakers. Real default-device volume was read only; Win32
  popup API exports and signatures loaded successfully without showing a popup.
- Native popup tests use fake Win32 APIs. They verify dismissal after automatic
  acknowledgement or app shutdown, while targeting only the popup's creating
  thread and matching dialog class/title. Closing a message alone does not
  acknowledge its saved alert. No other application window is enumerated.
- Both device enable flags default false for new installations. Existing saved
  enable choices and serial selections remain intact. Serial test fixtures now
  opt into their devices explicitly; disabled defaults reject starting live
  hardware control. No physical radio or rotator was used during verification.
- The preview was restarted successfully and served the updated Settings page.
  Its state reports automatic acknowledgement enabled, 60 seconds, and the
  configurable volume initially at the previous 100% level. Existing enabled
  device preferences were retained; live tracking and the radio test remained off.
- Distribution packaging excludes personal `settings.json` and `data/` and
  includes `settings.example.json`. A fresh launch creates notification-only
  settings; upgrades retain the existing runtime settings instead of replacing
  them with a packaged personal configuration.

## Satellite notification pauses and frequency lookup — 15 October 2026

- **156 offline Python tests passed** on Windows with both Python **3.12.14**
  and **3.14.8**. The suite covers saved per-satellite pauses, unchecked defaults,
  immediate suppression of existing and new alerts, other satellites continuing
  to alert, resuming within a pass, and notification-only changes preserving
  predictions and leaving equipment control running. Simulated serial fixtures
  continue to cover the existing radio and rotator behavior.
- **38 offline dashboard DOM checks passed**, including checkbox saves,
  rename/delete persistence, new-satellite defaults, all four lookup fields,
  source attribution, editable results, multiple operating profiles, no-match
  manual entry, retry after failure, and delayed replies preserving manual input
  and ignoring a closed/reopened editor. These checks use jsdom and fake HTTP;
  they are not a rendered-browser or physical hardware test.
- Real HTTPS requests successfully parsed AMSAT's current FM and linear tables.
  Live examples included AO-91, TO-108, both AO-7 operating profiles and TEVEL2-9.
  An unknown name returned an explicit manual-entry message. Offline tests also
  verify cache expiry, unavailable/changed pages, exact alias matching, and
  labelled bundled-reference fallback.
- The local server was restarted with live equipment control and radio testing
  off. Its actual authenticated HTTP lookup returned current profiles for AO-91,
  TO-108 and NORAD 25544 (ISS), and a no-match result for an unknown name.
  The dashboard and health endpoint responded successfully. Existing station,
  frequency and serial settings were preserved; all seven initial satellite
  notification pauses were unchecked.
- Browser inspection was attempted, including a helper reset, but the computer
  use runtime failed to start with `windows sandbox failed: helper_unknown_error:
  setup refresh had errors`. No rendered-screen verification is claimed.
  No physical radio or rotator was used for these checks.

## Initial application verification — 7–8 October 2026

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

## Requested serial defaults — 8 October 2026

- New or empty radio settings use COM6 and 57600 baud. The settings menu now
  offers 57600 alongside 9600, 19200 and 115200. Existing saved baud and port
  selections are retained. Missing rotator baud uses 9600.
- An empty rotator port selects COM3 only when detected and not already assigned
  to the enabled radio. A previously saved port is retained even when unplugged.
  Startup persists the filled settings without activating hardware control.
- **96 Python tests passed on both Python 3.12.14 and 3.14.8**, and all **13 DOM
  checks passed**. Added checks cover missing/empty settings, absent COM3,
  existing choices, port conflicts, persistence and the new numeric baud option.
  Parallel local runs encountered intermittent Windows connection aborts in an
  existing HTTP rejection test; complete sequential runs passed. HTTP permission
  checks were not changed.
- The restarted preview selected COM6 for the radio and COM3 for the rotator.
  Its previously saved radio baud of 9600 was preserved; rotator baud remains
  9600. Coordinates, height and satellite preferences were retained. Live
  tracking remains off and no physical serial port was opened.

## Audible alert repair — 8 October 2026

- Sound was enabled and the running monitor was not paused. The browser hid its
  sound control whenever Windows was detected and suppressed its chime whenever
  the native sound path reported success. That prevented browser audio from
  helping when the Windows beep was inaudible.
- Windows now plays an original bundled 1.8-second PCM WAV through `PlaySound`
  on the sound thread. A lock prevents overlapping native playback. Browser sound
  stays available on Windows, and Test alert resumes and verifies its audio
  context before claiming that it is enabled. Repeated, paused, acknowledged and
  disabled alerts retain their sound suppression rules; manually requested tests
  are allowed while paused. A reset sound counter after restart does not cause a
  spurious browser chime.
- **99 Python tests passed on Python 3.12.14 and 3.14.8**, and **17 offline DOM
  checks passed**. New checks cover the WAV format, nonzero unclipped samples,
  native filename/flags, failed playback and overlapping calls, plus browser
  activation on Windows, blocked audio, real alerts, repeats, pause and sound
  settings. The local HTTP server serves the WAV with `audio/wav` content type.
- These tests use a mocked Windows audio API and a simulated browser audio
  context. They establish software behaviour, not physical speaker audibility.
  The updated local preview was restarted with tracking off. After reloading the
  dashboard and pressing Test alert, the user confirmed the chime was audible on
  this machine. The native API also reported completed WAV playback. This does
  not establish speaker settings on a different computer. Saved radio/rotator
  settings were preserved; no serial port was opened.

## Radio testing, alert acknowledgement and audio volume — 8 October 2026

- **Test radio connection** alternates verified MAIN frequencies of 145.000 and
  145.555 MHz every 2 seconds, without a satellite target. The button becomes
  **Stop test radio connection** and shows a yellow progress label. It uses saved
  port/baud/address, stops live tracking, and never enables the rotator or sends
  PTT/memory writes. A new control session cancels the previous session's pending
  commands. Missing/incorrect replies stop the test. VHF on SUB is exchanged onto
  MAIN before test tuning, respecting the IC-9700's different-band constraint.
- Simulated device checks cover the exact frequency sequence and 2-second write
  intervals, early orbit wakeups, echo/broadcast filtering, correct port/baud and
  address, readback, stop/close, mid-command cancellation, rapid restart, failed
  open/ACK/write/readback and no automatic restart. HTTP and DOM checks cover
  start/stop, configuration changes, alert-pause independence, progress labels,
  verified frequency display, missing ports and errors.
- Rotator errors, including C2 timeouts, now stop/close only the rotator when
  radio control is enabled. Simulations establish that both downlink and uplink
  continue changing in both directions, without repeatedly reopening the failed
  rotator. Checks cover a moving-rotator stop, manual restart, rotator-only
  failure, stale observations during a timeout and a continuing radio status.
  Radio failures still stop live control.
- Closing the native Windows message no longer acknowledges the alert. Real
  alerts remain in the shared app banner until explicit acknowledgement, even
  when expired or paused. History has per-alert acknowledgement. Notification
  clicks show the dashboard. Simulated Windows dialog/notification checks cover
  dismissal, Settings visibility, expiry without further sound/notifications and
  acknowledgement through the actual HTTP endpoint.
- Windows alerts can set the default multimedia playback device to 100% and
  unmute before native playback, with readback confirmation. This setting defaults
  on, can be disabled without clearing pending alerts and adds no dependency.
  Tests establish volume-before-sound ordering, readback/error handling, continued
  playback if volume control fails, and no volume change with the option off or
  on non-Windows systems. The default endpoint's real volume/mute state was read
  successfully on this PC. The running monitor subsequently reported completed
  native playback with verified 100% unmuted volume for a real JO-97 alert.
  Automated suites mock changes and do not alter volume.
- The expanded Earth land fill is darker green (#548566) with darker coastlines
  (#31583f). A white outline/background preserves the ground-station marker and
  label. Geographic positions, scale and satellite marker prominence are retained.
- **121 Python tests passed on Python 3.12.14 and 3.14.8**, plus **25 offline DOM
  checks**. The pre-existing Windows connection-abort issue recurred in two local
  3.14 runs of an HTTP denial test; the subsequent complete run passed. Request
  permission checks were not changed. DOM checks establish behaviour, not rendered
  appearance. Radio/rotator validation uses simulated equipment rather than
  physical motors, CI-V interfaces or radio reception.

The updated preview is running with saved settings preserved. Equipment and the
radio connection test start off; use their main-page buttons to activate them.

## Rotator C2 diagnostic and pass-radio band selection — 8 October 2026

- The main-page **Test rotator connection** button sends exactly one `C2\r`
  using the saved rotator port and baud. Its persistent result shows test time,
  port, baud, raw response (including visible line endings), decoded position,
  and a warning/error for unconfigured/unavailable ports, timeouts or bad replies.
  The authenticated HTTP endpoint also makes the last result available to other
  dashboard tabs. The query works with live tracking off and the rotator checkbox
  disabled. Temporary ports close after the query; an existing live connection
  is reused under the same I/O lock as tracking. It does not send a pointing
  command, change the radio/control session, or restart a failed rotator.
- Simulated C2 checks cover saved port/baud, modem lines, exact command, raw
  replies and echoes, timestamps, legacy format, missing/unavailable ports,
  malformed/out-of-range/incomplete/oversized replies, timeout limits, open/write/
  read failures, partial response retention, cleanup, shutdown and active-radio
  port conflicts. A concurrent test establishes that live pointing waits for
  the C2 response rather than interleaving commands. Radio test timing and both
  pass Doppler frequencies continue afterwards. HTTP/DOM checks cover result
  persistence, missing-port warnings, safe rendering, local/UTC times and a
  disabled button during the query.
- The user reported that the connection test worked on their IC-9700 but an
  actual pass stopped with **command 05 (FA)**. The same failure was reproduced
  in the serial simulator: MAIN on VHF and SUB on UHF, followed by an ISS/UHF
  downlink tune. Pass initialization previously omitted the MAIN/SUB exchange
  already used by the connection test. It now reads the actual bands after
  entering the requested satellite/VFO mode, and exchanges MAIN/SUB when the
  downlink band is on SUB before setting either frequency. The commands were
  checked against the [Icom CI-V reference guide](https://www.icomfrance.com/uploads/files/produit/not-IC-9700_ENG_CI-V_3a-en.pdf);
  the basic manual's band selection section prohibits the same band on both
  receivers. The simulator now enforces that restriction by default.
- Regression checks cover VHF test-to-UHF pass transitions, VHF/UHF satellite
  handovers, approaching/receding Doppler updates, all 36 combinations of initial
  and requested distinct VHF/UHF/1.2 GHz band pairs, satellite-mode band restoration,
  receive-only operation, same-band rejection and command failures. Acknowledgement
  and RX/TX readback remain required; no PTT or memory writes are introduced.
- Calculated pass frequencies are explicitly labelled. The equipment panel
  reports last verified RX/TX, readback time and satellite, plus confirmed pass
  update count. A serial failure or a visible pass with live control off has a
  prominent explanation at the top of the main page. Simulated UI checks show
  that changing calculated values does not fabricate radio readback.
- **140 Python tests passed on Python 3.12.14 and 3.14.8**, and all **30 offline
  DOM checks passed**. The running preview was restarted and serves the new
  button and hardware state fields. Its settings file hash was unchanged:
  saved COM6/9600 radio and COM3/9600 rotator selections were preserved.
  Live tracking and the radio connection test are off after restart.

No physical serial port was opened during this validation. The user confirmed
the earlier radio connection test works, but the corrected actual-pass behaviour
has only been verified with simulated equipment so far. It needs confirmation
on the connected IC-9700. Codex's browser-control process again failed to start
because of its Windows sandbox helper; the DOM checks do not establish rendered
appearance. The updated local dashboard is running and can be reloaded normally.

## Compact C2 replies — 8 October 2026

The user supplied a valid physical-controller reply, `AZ=183EL=010\r\n`, that
the connection test incorrectly rejected. The shared parser required whitespace
between the two angles. That gap is now optional, so both the one-shot test and
live tracking accept compact and spaced replies while retaining the legacy
format and angle limits.

The exact supplied bytes were exercised through the parser, the connection-test
controller, a live tracking cycle and the authenticated HTTP endpoint. They now
produce success with azimuth 183° and elevation 10°, retaining the raw response
and test time. Live tracking continues rather than setting a rotator error.
Malformed and out-of-range compact replies remain rejected. **141 Python tests
passed locally on Python 3.12.14**, and the focused rotator suite passed on
Python 3.14.8. Two local 3.14 full runs encountered the pre-existing Windows
connection-abort issue in the HTTP denial test; request permission checks were
unchanged. The [Windows CI run for the parser fix](https://github.com/uham999/SatelliteAlert/actions/runs/37777371108)
passed the full suite on both Python 3.12 and 3.14, plus all 30 DOM checks and
the launcher checks. The serial regression checks use simulated devices; no
physical port was opened by the validation suite.
