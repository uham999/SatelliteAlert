'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
let state = null, selectedPass = null, selectedName = null, showAll = false, timeZone = localStorage.getItem('satellite-time-zone') || 'local';
let audioContext = null, browserSound = false, soundSerial = null, notificationIds = new Set(), disconnected = false;
const notificationHandles = new Map();
let view = 'dashboard', toastTimer;
let serialPorts=[], editingSatellite=null, profileEditorOpen=false;
let profileLookupTimer, profileLookupGeneration=0, lastLookupName='', profileMatches=[], profileSource='';
let automaticProfileValues={}, manualProfileFields=new Set();
const profileFields={profileRx:'downlink_mhz',profileTx:'uplink_mhz',profileRxMode:'downlink_mode',profileTxMode:'uplink_mode',profileNotes:'notes'};
const profileEmpty={profileRx:'',profileTx:'',profileRxMode:'USB',profileTxMode:'LSB',profileNotes:''};
let worldLand=null, worldLandRequest=null;
const earthImage = new Image();
const earlyAlertMinutes = {before_horizon_5:5, before_horizon:10, before_horizon_20:20};
earthImage.addEventListener('load',drawSky);
earthImage.src = '/earth-overlay.png';
$('timeZone').value = timeZone;

function formatTime(value, seconds = false, date = false) {
  if (!value) return '—';
  const options = {hour:'2-digit',minute:'2-digit',hour12:false};
  if (seconds) options.second = '2-digit';
  if (date) {options.day='2-digit'; options.month='short';}
  if (timeZone === 'UTC') options.timeZone='UTC';
  return new Intl.DateTimeFormat('en-GB',options).format(new Date(value));
}
function duration(seconds) {
  seconds = Math.max(0, Math.round(seconds));
  return `${Math.floor(seconds/60)}m ${String(seconds%60).padStart(2,'0')}s`;
}
function dayLabel(value) {
  if (!value) return '';
  const options = {day:'2-digit',month:'short'};
  if (timeZone === 'UTC') options.timeZone='UTC';
  return new Intl.DateTimeFormat('en-GB',options).format(new Date(value));
}
function toast(message) {
  $('toast').textContent = message; $('toast').classList.remove('hidden');
  clearTimeout(toastTimer); toastTimer = setTimeout(()=>$('toast').classList.add('hidden'),5000);
}
async function post(path,data={}) {
  if (!state) throw new Error('Please wait for the app to connect.');
  const response = await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Satellite-Token':state.token},body:JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Request failed.');
  return result;
}
function showView(name) {
  view = name;
  document.querySelectorAll('.view').forEach(element=>element.classList.toggle('hidden',element.id !== `view-${name}`));
  document.querySelectorAll('.nav-item').forEach(element=>element.classList.toggle('selected',element.dataset.view === name));
  if (name === 'settings') fillSettings();
  if (name === 'dashboard') requestAnimationFrame(drawSky);
}
document.querySelectorAll('[data-view]').forEach(element=>element.addEventListener('click',()=>showView(element.dataset.view)));
document.querySelector('.brand').addEventListener('click',event=>{event.preventDefault();showView('dashboard');});

function fillSettings() {
  if (!state) return;
  for (const [key,value] of Object.entries(state.settings)) {
    const field = $('settingsForm').elements.namedItem(key);
    if (!field) continue;
    if (key==='rotator_port' || key==='radio_port') {
      field.replaceChildren(new Option(value || 'Select a port',value));
      field.value=value;
    } else if (field.type === 'checkbox') field.checked = value;
    else field.value = key === 'satellites' ? value.join('\n') : value;
  }
  $('notificationStatus').textContent = 'Notification' in window ? `Browser permission: ${Notification.permission}` : 'Notifications unavailable in this browser.';
  updateAlarmSettings();
  renderProfiles();refreshPorts(true);
}
function updateAlarmSettings() {
  const fields=$('settingsForm').elements;
  fields.auto_acknowledge_seconds.disabled=!fields.auto_acknowledge_enabled.checked;
  fields.windows_volume_percent.disabled=!fields.windows_maximum_volume.checked;
}
for(const name of ['auto_acknowledge_enabled','windows_maximum_volume'])$('settingsForm').elements.namedItem(name).addEventListener('change',updateAlarmSettings);
function choosePass() {
  if (!state || !state.passes.length) return null;
  if (selectedPass) {
    const found = state.passes.find(pass=>pass.norad === selectedPass.norad && Math.abs(new Date(pass.threshold_rise)-new Date(selectedPass.threshold_rise))<1200000);
    if (found) return found;
  }
  if (selectedName) return state.passes.find(pass=>pass.name === selectedName) || null;
  return state.passes.find(pass=>!satelliteNotificationsPaused(pass.name)) || null;
}
function satelliteNotificationsPaused(name) {return Boolean(state && state.settings.satellite_notifications_paused?.[name]);}
function renderNext() {
  const pass = choosePass(), settings = state.settings, minimum = settings.minimum_elevation;
  const horizon = settings.alert_mode === 'horizon', early = earlyAlertMinutes[settings.alert_mode] || 0;
  $('thresholdChip').textContent = `≥ ${minimum}° elevation`;
  $('alertRule').textContent = early ? `Alerts ${early} min before horizon rise · peak ≥ ${minimum}°` : horizon ? `Alerts at horizon rise · peak ≥ ${minimum}°` : `Alerts at ${minimum}° above your horizon`;
  $('alertTimeLabel').textContent = early ? 'Early alert time' : horizon ? 'Horizon rise / alert' : `Reaches ${minimum}°`;
  $('ringLabel').textContent = `${minimum}° alert boundary`;
  $('nextName').textContent = pass ? pass.name : (state.forecast_at ? (selectedName || (settings.satellites.every(satelliteNotificationsPaused)?'Notifications paused':'No passes')) : 'Calculating…');
  $('nextSubtitle').textContent = pass ? `${dayLabel(pass.alert_at)} · ${timeZone === 'UTC' ? 'UTC' : Intl.DateTimeFormat().resolvedOptions().timeZone} · NORAD ${pass.norad}` : state.forecast_at ? (state.passes.length && !selectedName ? 'Notifications are paused for satellites with upcoming passes. Uncheck Pause notifications in Settings to resume.' : `No qualifying passes in the next ${settings.forecast_hours} hours.`) : 'Downloading orbital elements and calculating passes.';
  $('nextStart').textContent = pass ? formatTime(pass.alert_at,true) : '—';
  $('nextPeak').textContent = pass ? `${pass.peak_elevation.toFixed(1)}°` : '—';
  $('nextDuration').textContent = pass ? duration((new Date(pass.threshold_set)-new Date(pass.threshold_rise))/1000) : '—';
  const position=state.satellites.find(row=>row.name===(selectedName || (pass && pass.name)) && row.status==='tracking' && row.azimuth!=null && row.elevation!=null);
  $('nextAzimuth').textContent=position ? `${position.azimuth.toFixed(1)}°` : '—';
  $('nextElevation').textContent=position ? `${position.elevation.toFixed(1)}°` : '—';
  $('pathLabel').textContent = pass ? `${pass.name} path` : 'Predicted path';
  $('skyCaption').textContent = selectedName || selectedPass ? 'Selected pass path' : 'Next pass path';
  updateCountdown(); drawSky();
}
function updateCountdown() {
  const pass = choosePass();
  if (!pass) {$('countdown').textContent='-- : -- : --';$('countdownLabel').textContent='NO ALERT SCHEDULED';$('nextStatus').textContent='LIVE FORECAST';return;}
  if(satelliteNotificationsPaused(pass.name)){$('countdown').textContent='PAUSED';$('countdownLabel').textContent='NOTIFICATIONS PAUSED FOR THIS SATELLITE';$('nextStatus').textContent='NOTIFICATIONS PAUSED';return;}
  const now = Date.now(), until = (new Date(pass.alert_at)-now)/1000;
  let remaining = until, label = 'TIME UNTIL ALERT', status = 'LIVE FORECAST';
  if (until <= 0 && now < new Date(pass.horizon_rise)) {
    remaining = (new Date(pass.horizon_rise)-now)/1000;label='EARLY ALERT WINDOW · TIME UNTIL HORIZON RISE';status='PASS APPROACHING';
  } else if (until <= 0 && now < new Date(pass.threshold_rise)) {
    remaining = (new Date(pass.threshold_rise)-now)/1000;label=`TIME UNTIL ${state.settings.minimum_elevation}° ELEVATION`;status='ABOVE HORIZON';
  } else if (until <= 0) {
    remaining = (new Date(pass.threshold_set)-now)/1000;label='TIME ABOVE MINIMUM REMAINING';status='PASS UNDERWAY';
  }
  $('countdownLabel').textContent=label;$('nextStatus').textContent=status;
  const seconds = Math.max(0,Math.ceil(remaining));
  $('countdown').textContent = [Math.floor(seconds/3600),Math.floor(seconds/60)%60,seconds%60].map(number=>String(number).padStart(2,'0')).join(' : ');
}

function renderFleet() {
  const minimum = state.settings.minimum_elevation;
  const rows = state.satellites.length ? state.satellites : state.settings.satellites.map(name=>({name,status:'loading',detail:'Calculating…'}));
  $('fleetCount').textContent = rows.length;
  $('fleet').innerHTML = rows.map(row=>{
    const ready = row.status==='tracking', up=ready && row.elevation>=0, high=ready && row.elevation>=minimum;
    const tag = ready ? (high ? 'In range' : up ? 'Above horizon' : 'Below horizon') : row.status;
    const detail = ready ? (row.next_pass ? `Next ${formatTime(row.next_pass.alert_at)} · ${dayLabel(row.next_pass.alert_at)}` : 'No qualifying pass in forecast') : row.detail;
    const age = row.tle_age_days == null ? '' : ` · TLE ${Math.abs(row.tle_age_days).toFixed(1)}d`;
    const peakElevation = row.next_pass?.peak_elevation;
    return `<button class="sat-card ${high?'active':''} ${selectedName===row.name?'focused':''}" data-satellite="${esc(row.name)}" title="${esc(row.detail + age)}"><div class="sat-heading"><strong>${esc(row.name)}</strong><span class="sat-status ${up?'up':!ready?'warn':''}">${esc(tag)}</span></div>${satelliteNotificationsPaused(row.name)?'<span class="tag">Notifications paused</span>':''}<div class="sat-metrics"><div><span>ELEVATION</span><strong>${ready && row.elevation != null ? row.elevation.toFixed(1)+'°':'—'}</strong></div><div><span>AZIMUTH</span><strong>${ready && row.azimuth != null ? row.azimuth.toFixed(0)+'°':'—'}</strong></div><div title="Maximum elevation of the next qualifying pass"><span>NEXT PASS MAX</span><strong>${Number.isFinite(peakElevation) ? peakElevation.toFixed(1)+'°':'—'}</strong></div></div><div class="sat-bottom">${esc(detail)}${esc(age)}</div></button>`;
  }).join('');
  $('fleet').querySelectorAll('button').forEach(button=>button.addEventListener('click',()=>{selectedName=button.dataset.satellite;selectedPass=null;renderNext();renderFleet();}));
}
function renderTable() {
  const minimum = state.settings.minimum_elevation;
  $('tableThreshold').textContent = `Reaches ${minimum}°`;
  $('tableSet').textContent = `Below ${minimum}°`;
  $('forecastLabel').textContent = `Next ${state.settings.forecast_hours} hours · only passes reaching ${minimum}° elevation · ${timeZone === 'UTC' ? 'UTC' : Intl.DateTimeFormat().resolvedOptions().timeZone}`;
  const passes=showAll ? state.passes : state.passes.slice(0,8);
  $('passTable').innerHTML = passes.length ? passes.map((pass,index)=>`<tr data-pass="${index}" tabindex="0" aria-label="Show ${esc(pass.name)} pass at ${esc(formatTime(pass.alert_at))}"><td>${esc(pass.name)}</td><td>${formatTime(pass.alert_at,true)}<span class="day-label">${dayLabel(pass.alert_at)}</span></td><td>${formatTime(pass.horizon_rise)}<span class="day-label">${dayLabel(pass.horizon_rise)}</span></td><td>${formatTime(pass.threshold_rise,true)}</td><td>${formatTime(pass.peak_time,true)}</td><td>${formatTime(pass.threshold_set,true)}</td><td>${pass.peak_elevation.toFixed(1)}°<span class="elevation-bar"><i></i></span></td></tr>`).join('') : '<tr><td colspan="7" class="empty">No qualifying passes in this forecast window.</td></tr>';
  // Set widths without inline styles, to respect the local content policy.
  $('passTable').querySelectorAll('.elevation-bar i').forEach((element,index)=>element.style.width=`${Math.round(passes[index].peak_elevation/90*100)}%`);
  $('passTable').querySelectorAll('tr[data-pass]').forEach(row=>{
    const select=()=>{selectedPass=passes[Number(row.dataset.pass)];selectedName=null;renderNext();renderFleet();document.querySelector('.overview').scrollIntoView({behavior:'smooth',block:'start'});};
    row.addEventListener('click',select);row.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();select();}});
  });
  $('morePasses').classList.toggle('hidden',state.passes.length<=8);
  $('morePasses').textContent=showAll ? 'Show fewer passes' : `Show all ${state.passes.length} passes`;
}

function renderAlerts() {
  const active=state.alerts.filter(alert=>!alert.acknowledged && alert.active && (alert.test || !satelliteNotificationsPaused(alert.name)) && (!state.paused || alert.test));
  const pending=state.alerts.filter(alert=>!alert.acknowledged && (alert.test || !satelliteNotificationsPaused(alert.name)) && (!alert.test || alert.active));
  $('alertBanner').classList.toggle('hidden',!pending.length);
  const deadlines=pending.filter(alert=>alert.auto_acknowledge_at).map(alert=>alert.auto_acknowledge_at).sort();
  $('autoAcknowledgeStatus').textContent=deadlines.length?`Automatically acknowledges at ${formatTime(deadlines[0],true)}. You can acknowledge sooner.`:'';
  $('autoAcknowledgeStatus').classList.toggle('hidden',!deadlines.length);
  for(const [id,notification] of notificationHandles)if(!active.some(alert=>alert.id===id)){
    if(typeof notification.close==='function')notification.close();notificationHandles.delete(id);
  }
  $('alertBanner').classList.toggle('awaiting-acknowledgement',!active.length);
  if (pending.length) {
    const tests=pending.every(alert=>alert.test),real=pending.filter(alert=>!alert.test);
    $('alertType').textContent=tests ? 'TEST ALERT · DEMONSTRATION' : 'SATELLITE PASS ALERT';
    const approaching=real.some(alert=>alert.active && alert.horizon_rise && Date.now()<new Date(alert.horizon_rise));
    const awaiting=real.some(alert=>!alert.active) || state.paused;
    $('alertTitle').textContent=tests ? 'This is how your alert looks.' : [...new Set(real.map(alert=>alert.name))].join(' + ')+(awaiting?' · awaiting acknowledgement':approaching?' · pass approaching':' above your horizon');
    $('alertMessage').textContent=tests ? 'A chime has been requested. Real passes stay here until you acknowledge them, alongside any Windows notification.' : pending.map(alert=>`${alert.active?'':`${formatTime(alert.created_at,true,true)} · `}${alert.message}`).join(' ');
  }
  for (const alert of active) {
    if (!notificationIds.has(alert.id)) {
      notificationIds.add(alert.id);
      if ('Notification' in window && Notification.permission==='granted') {
        try{const notification=new Notification(alert.test?'Satellite Alert · Test':`Satellite Alert · ${alert.name}`,{body:alert.message,tag:alert.id});notificationHandles.set(alert.id,notification);notification.onclick=()=>{window.focus();showView('dashboard');notification.close();};}catch(error){console.warn(error);}
      }
    }
  }
  if(soundSerial!==null && state.sound_serial>soundSerial && active.length && (!state.paused || active.some(alert=>alert.test)) && browserSound && state.settings.sound_enabled) chime();
  soundSerial=state.sound_serial;
  $('history').innerHTML=state.alerts.length ? state.alerts.map(alert=>`<div class="history-item"><div><strong>${esc(alert.test?'TEST ALERT':alert.name)}</strong> <span class="tag">${alert.acknowledged?(alert.acknowledgement_source==='automatic'?'AUTO ACKNOWLEDGED':'ACKNOWLEDGED'):alert.active?'ACTIVE':'AWAITING ACKNOWLEDGEMENT'}</span><p>${esc(alert.message)}</p></div><div class="history-actions"><time>${formatTime(alert.created_at,true,true)}</time>${!alert.acknowledged?`<button class="button" data-acknowledge="${esc(alert.id)}">Acknowledge</button>`:''}</div></div>`).join('') : '<p class="empty">No alerts yet. Use Test alert to check the banner and chime.</p>';
}
function renderStatus() {
  const warnings=[];
  if(state.fetch_error) warnings.push(state.fetch_error);
  if(state.forecast_error) warnings.push(state.forecast_error);
  if(state.settings.location_note) warnings.push(state.settings.location_note);
  if(state.paused) warnings.push('Alerts are paused. Positions and orbital data continue to update.');
  const issues=state.satellites.filter(row=>['missing','stale','error','duplicate'].includes(row.status));
  if(issues.length) warnings.push(issues.map(row=>`${row.name}: ${row.detail}`).join(' '));
  if(state.warnings.length) warnings.push(`${state.warnings.length} malformed or invalid feed record(s) were skipped. See the orbital data log for details.`);
  $('notice').textContent=warnings.join(' ');$('notice').classList.toggle('hidden',!warnings.length);
  $('pause').textContent=state.paused?'Resume alerts':'Pause alerts';
  $('pause').title=state.pause_reason==='automatic'?'Resume overrides this automatic pause period':state.paused?'Resume alerts':'Pause until you press Resume';
  $('quietStatus').textContent=state.pause_reason==='manual'?'Currently paused manually. Press Resume alerts to continue.':state.pause_reason==='automatic'?'Currently in the automatic pause period. Resume alerts overrides this period.':state.automatic_pause_overridden?'Manual Resume is overriding the current automatic pause period.':state.settings.automatic_pause_enabled?'The daily pause follows this computer’s local clock.':'Automatic pause is disabled.';
  $('refresh').disabled=state.updating;$('refresh').textContent=state.updating?'↻ Downloading…':'↻ Refresh orbital data';
  const ticking=state.last_tick && Date.now()-new Date(state.last_tick)<10000;
  $('connection').classList.toggle('offline',state.paused || !ticking);
  $('connection').innerHTML=`<i></i> ${state.paused?'Alerts paused':ticking?'Monitor running':'Starting monitor'}`;
  const s=state.settings;
  $('earthToggle').checked=s.earth_overlay;
  $('earthNote').classList.toggle('hidden',!s.earth_overlay);
  $('stationLat').textContent=`${Math.abs(s.latitude).toFixed(3)}° ${s.latitude>=0?'N':'S'}`;
  $('stationLon').textContent=`${Math.abs(s.longitude).toFixed(3)}° ${s.longitude>=0?'E':'W'}`;
  $('dataStatus').textContent=state.fetched_at ? `AMSAT · downloaded ${formatTime(state.fetched_at,false,true)}` : 'Waiting for first successful download.';
  $('refreshStatus').textContent=`${formatTime(state.next_refresh_at,false,true)} · every ${s.refresh_hours} hours`;
  const soundStatus=s.sound_enabled ? (browserSound ? `Browser sound enabled${state.native_sound?' · Windows WAV backup':''}` : `${state.sound_status}${state.native_sound?' · browser sound available below':''}`) : 'Sound disabled in Settings';
  $('soundStatus').textContent=soundStatus+(state.volume_status?` · ${state.volume_status}`:'');
  $('enableSound').classList.toggle('hidden',browserSound);
}
function render() {renderStatus();renderNext();renderFleet();renderTable();renderAlerts();renderTracking();}
async function refreshState() {
  const response=await fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(8000)});
  if(!response.ok) throw new Error('App unavailable');
  const initial=!state;state=await response.json();disconnected=false;render();if(initial) fillSettings();
}
async function poll() {
  try {
    await refreshState();
  }catch(error){
    disconnected=true;$('connection').classList.add('offline');$('connection').innerHTML='<i></i> Monitor disconnected';
    $('notice').textContent='The dashboard cannot reach the Python monitor. Start the program again to resume live tracking and alerts.';$('notice').classList.remove('hidden');
  }finally{setTimeout(poll,2000);}
}

function visibleSatellites() {
  return state ? state.satellites.filter(row=>row.status==='tracking' && row.elevation>=0 && row.azimuth!=null).sort((a,b)=>b.elevation-a.elevation) : [];
}
function drawSky() {
  if(view==='dashboard')drawSkyCanvas($('sky'));
  if($('skyDialog').open) {
    const world=$('expandedMapMode').value==='world';
    if(world)drawWorldCanvas($('expandedSky'));else drawSkyCanvas($('expandedSky'));
    $('expandedMapNote').textContent=world?'Gold marks the highest satellite. Purple marks your ground station at the centre. Markers show the point on Earth beneath each satellite. Positions update every 2 seconds.':'Gold marks the highest satellite. North is up; the circle edge is your local horizon. Positions update every 2 seconds.';
  }
}
async function loadWorldLand() {
  if(worldLand || worldLandRequest)return worldLandRequest;
  worldLandRequest=(async()=>{
    try{const response=await fetch('/world-land.json');if(!response.ok)throw new Error('Earth map unavailable.');worldLand=await response.json();drawSky();}
    catch(error){toast(error.message);}finally{worldLandRequest=null;}
  })();return worldLandRequest;
}
function drawWorldCanvas(canvas) {
  if(!state || !canvas.clientWidth)return;
  const rect=canvas.getBoundingClientRect(),scale=window.devicePixelRatio||1;
  canvas.width=rect.width*scale;canvas.height=rect.height*scale;
  const ctx=canvas.getContext('2d');ctx.scale(scale,scale);
  const w=rect.width,h=rect.height,r=Math.min(w,h)/2-24,cx=w/2,cy=h/2,s=state.settings;
  const projection=d3.geoOrthographic().rotate([-s.longitude,-s.latitude]).translate([cx,cy]).scale(r).precision(.5);
  const path=d3.geoPath(projection,ctx);
  ctx.beginPath();path({type:'Sphere'});ctx.fillStyle='#dceef3';ctx.fill();ctx.strokeStyle='#94bac3';ctx.lineWidth=1.5;ctx.stroke();
  if(worldLand){ctx.beginPath();path(worldLand);ctx.fillStyle='#548566';ctx.fill();ctx.strokeStyle='#31583f';ctx.lineWidth=1;ctx.stroke();}
  ctx.beginPath();path(d3.geoGraticule10());ctx.strokeStyle='#adc8cf';ctx.lineWidth=.7;ctx.stroke();
  ctx.beginPath();ctx.moveTo(cx-9,cy);ctx.lineTo(cx+9,cy);ctx.moveTo(cx,cy-9);ctx.lineTo(cx,cy+9);
  ctx.strokeStyle='#fff';ctx.lineWidth=6;ctx.stroke();ctx.strokeStyle='#8557b2';ctx.lineWidth=3;ctx.stroke();
  ctx.font='18px Segoe UI';ctx.textAlign='center';
  const stationLabel='Your ground station',stationLabelWidth=ctx.measureText(stationLabel).width+14;
  ctx.fillStyle='#ffffffdd';ctx.fillRect(cx-stationLabelWidth/2,cy+9,stationLabelWidth,25);
  ctx.fillStyle='#634181';ctx.fillText(stationLabel,cx,cy+26);
  const visible=visibleSatellites().filter(row=>Number.isFinite(row.ground_latitude) && Number.isFinite(row.ground_longitude));
  drawSatelliteMarkers(ctx,visible,row=>projection([row.ground_longitude,row.ground_latitude]),w,h);
}
function drawSkyCanvas(canvas) {
  if(!canvas.clientWidth)return;
  const rect=canvas.getBoundingClientRect(),scale=window.devicePixelRatio||1;
  canvas.width=rect.width*scale;canvas.height=rect.height*scale;
  const ctx=canvas.getContext('2d');ctx.scale(scale,scale);
  const w=rect.width,h=rect.height,cx=w/2,cy=h/2,r=Math.min(w,h)/2-30;
  ctx.font='18px Segoe UI';ctx.textAlign='center';ctx.textBaseline='middle';
  ctx.fillStyle='#f9fbf9';ctx.beginPath();ctx.arc(cx,cy,r,0,Math.PI*2);ctx.fill();
  if(state && state.settings.earth_overlay && earthImage.complete && earthImage.naturalWidth) {
    ctx.save();ctx.beginPath();ctx.arc(cx,cy,r,0,Math.PI*2);ctx.clip();ctx.globalAlpha=.42;
    ctx.drawImage(earthImage,cx-r,cy-r,r*2,r*2);ctx.restore();
  }
  ctx.strokeStyle='#dde5e2';ctx.lineWidth=1;
  for(const elevation of [0,30,60]){ctx.beginPath();ctx.arc(cx,cy,r*(1-elevation/90),0,Math.PI*2);ctx.stroke();}
  ctx.strokeStyle='#e2e9e6';ctx.beginPath();ctx.moveTo(cx-r,cy);ctx.lineTo(cx+r,cy);ctx.moveTo(cx,cy-r);ctx.lineTo(cx,cy+r);ctx.stroke();
  const threshold=state?state.settings.minimum_elevation:35;
  ctx.strokeStyle='#b78630';ctx.setLineDash([4,5]);ctx.beginPath();ctx.arc(cx,cy,r*(1-threshold/90),0,Math.PI*2);ctx.stroke();ctx.setLineDash([]);
  ctx.fillStyle='#667b73';for(const [label,x,y] of [['N',cx,cy-r-15],['E',cx+r+16,cy],['S',cx,cy+r+16],['W',cx-r-16,cy]])ctx.fillText(label,x,y);
  ctx.fillText('90°',cx+22,cy+15);ctx.fillText('0°',cx+r-23,cy+15);
  const point=(alt,az)=>{const distance=r*(1-Math.max(0,Math.min(90,alt))/90),angle=az*Math.PI/180;return [cx+Math.sin(angle)*distance,cy-Math.cos(angle)*distance];};
  const visible=visibleSatellites(),target=state && state.tracking_target;
  const pass=target ? state.passes.find(p=>p.name===target.name && new Date(p.horizon_rise)<=new Date(state.now) && new Date(p.horizon_set)>=new Date(state.now)) : choosePass();
  if(pass && pass.path.length) {
    const path=pass.path;
    for(let i=1;i<path.length;i++){
      const from=point(path[i-1].elevation,path[i-1].azimuth),to=point(path[i].elevation,path[i].azimuth);
      ctx.strokeStyle=path[i].elevation>=threshold?'#007c7a':'#88b9ae';ctx.lineWidth=path[i].elevation>=threshold?3:2;
      ctx.beginPath();ctx.moveTo(...from);ctx.lineTo(...to);ctx.stroke();
    }
  }
  drawSatelliteMarkers(ctx,visible,row=>point(row.elevation,row.azimuth),w,h);
}
function drawSatelliteMarkers(ctx,visible,point,w,h) {
  const labels=[];
  // Draw the highest last so its larger gold marker remains visible at overlaps.
  for(const row of [...visible].reverse()) {
    const highest=row.name===visible[0].name,[x,y]=point(row);
    ctx.fillStyle=highest?'#e5a32c':'#007c7a';ctx.beginPath();ctx.arc(x,y,highest?11:7,0,Math.PI*2);ctx.fill();
    ctx.strokeStyle='#fff';ctx.lineWidth=2;ctx.stroke();
    if(highest){ctx.strokeStyle='#995d05';ctx.beginPath();ctx.arc(x,y,15,0,Math.PI*2);ctx.stroke();}
    ctx.font=highest?'bold 20px Segoe UI':'18px Segoe UI';ctx.textAlign='left';
    const text=row.name,width=ctx.measureText(text).width+12;
    const candidates=[[x+19,y-20],[x-width-19,y-20],[x+19,y+20],[x-width-19,y+20],[x+19,y-45],[x-width-19,y+45]];
    let box;
    for(const [bx,by] of candidates){box={x:Math.max(5,Math.min(w-width-5,bx)),y:Math.max(15,Math.min(h-15,by)),width};if(!labels.some(b=>Math.abs(b.y-box.y)<25 && b.x<box.x+box.width && box.x<b.x+b.width))break;}
    labels.push(box);ctx.fillStyle='#ffffffdd';ctx.fillRect(box.x-3,box.y-13,width,26);ctx.fillStyle=highest?'#704600':'#005957';ctx.fillText(text,box.x+3,box.y);
  }
}

function mhz(value){return value==null?'—':`${(value/1000000).toFixed(6)} MHz`;}
function liveDetails(row) {
  const f=row.doppler || {};
  const ground=Number.isFinite(row.ground_latitude)?`<p class="ground-position">Above ${Math.abs(row.ground_latitude).toFixed(2)}° ${row.ground_latitude>=0?'N':'S'}, ${Math.abs(row.ground_longitude).toFixed(2)}° ${row.ground_longitude>=0?'E':'W'} · altitude ${Math.round(row.satellite_height_km)} km</p>`:'';
  return `<div class="live-name"><strong>${esc(row.name)}</strong><span class="tag teal">${row.elevation.toFixed(1)}° elevation</span></div>${ground}<div class="live-metrics"><div><span>Satellite azimuth</span><strong>${row.azimuth.toFixed(1)}°</strong></div><div><span>Satellite elevation</span><strong>${row.elevation.toFixed(1)}°</strong></div><div><span>Calculated downlink / RX · Doppler corrected</span><strong>${mhz(f.rx_hz)}</strong><span>${f.rx_shift_hz==null?'Set a frequency':`${f.rx_shift_hz>=0?'+':''}${f.rx_shift_hz} Hz shift`}</span></div><div><span>Calculated uplink / TX · Doppler corrected</span><strong>${mhz(f.tx_hz)}</strong><span>${f.tx_shift_hz==null?'Receive only':`${f.tx_shift_hz>=0?'+':''}${f.tx_shift_hz} Hz shift`}</span></div></div>`;
}
function renderTracking() {
  const h=state.hardware,target=state.tracking_target,visible=visibleSatellites();
  $('trackingToggle').textContent=h.enabled?'Stop live tracking':'Start live tracking';
  $('trackingToggle').classList.toggle('tracking-on',h.enabled);
  const notificationOnly=!state.settings.rotator_enabled && !state.settings.radio_enabled;
  $('trackingToggle').disabled=Boolean(h.radio_test_enabled || (notificationOnly && !h.enabled));
  $('trackingToggle').title=notificationOnly?'Enable radio or rotator control in Settings to use live tracking.':'';
  $('radioTestToggle').classList.toggle('hidden',!state.settings.radio_enabled);
  $('rotatorTest').classList.toggle('hidden',!state.settings.rotator_enabled);
  $('radioTestToggle').textContent=h.radio_test_enabled?'Stop test radio connection':'Test radio connection';
  $('radioTestToggle').classList.toggle('radio-testing',Boolean(h.radio_test_enabled));
  $('radioTestToggle').setAttribute('aria-pressed',String(Boolean(h.radio_test_enabled)));
  const radioTesting=state.settings.radio_enabled && h.radio_test_enabled;
  $('radioTestBanner').classList.toggle('hidden',!radioTesting);
  $('radioTestBanner').textContent=radioTesting?`Radio connection test in progress · ${h.radio_test_frequency_hz?`MAIN ${mhz(h.radio_test_frequency_hz)} verified`:'Connecting…'} · 145.000 ↔ 145.555 MHz every 2 seconds`:'';
  renderRotatorTest(h.rotator_test);
  renderRadioTest(h.radio_test_result);
  const trackingNotice=h.error?`Live control stopped: ${h.error} RX/TX frequencies below are calculated only. Correct the issue, then press Start live tracking.`:
    notificationOnly?'Notification-only mode. Radio and rotator control are disabled in Settings. Alerts, positions and pass predictions continue.':
    !h.enabled && !h.radio_test_enabled && target?'Live tracking is off. RX/TX frequencies below are calculated only. Press Start live tracking to send adjustments to your configured equipment.':
    h.enabled && !state.settings.radio_enabled && target?'Radio adjustments are disabled in Settings. RX/TX frequencies below are calculated only. Enable the radio, save Settings, then start live tracking.':'';
  $('trackingNotice').textContent=trackingNotice;$('trackingNotice').classList.toggle('hidden',!trackingNotice);
  $('trackingSummary').textContent=h.radio_test_enabled?'Radio connection test active. Live satellite tracking is stopped.':h.enabled?(target?`Tracking ${target.name} · highest current elevation · ${visible.length} above the horizon`:'Tracking is enabled. Waiting for a satellite above the tracking limit.'):notificationOnly?'Notification-only mode. Enable devices in Settings if you want this app to control equipment.':'Live adjustments are off. Start live tracking to control your configured equipment.';
  $('liveTarget').innerHTML=target?liveDetails(target):'<p class="subtle">No watched satellite is currently above the tracking limit.</p>';
  const pos=h.rotator_position,command=h.commanded_position;
  $('equipmentStatus').classList.toggle('hidden',notificationOnly);
  $('equipmentStatus').innerHTML=`<div class="equipment-grid">${state.settings.rotator_enabled?`<div><strong>Rotator · ${esc(h.rotator_status)}</strong><p>Reported: ${pos?`az ${pos.azimuth}° · el ${pos.elevation}°`:'—'}</p><p>Last command: ${command?`az ${command.azimuth}° · el ${command.elevation}°`:'—'}</p></div>`:''}${state.settings.radio_enabled?`<div><strong>Radio · ${esc(h.radio_status)}</strong><p>Last verified RX: ${mhz(h.radio_rx_hz)}</p><p>Last verified TX: ${mhz(h.radio_tx_hz)}</p><p>${h.radio_verified_at?`Last radio readback: ${formatTime(h.radio_verified_at,true,true)} · ${esc(h.radio_verified_target)}`:'No radio readback yet.'}</p><p>Pass frequency updates: ${h.frequency_updates || 0}</p></div>`:''}</div>${state.settings.rotator_enabled && h.rotator_error?`<p class="equipment-error">Rotator: ${esc(h.rotator_error)}${h.enabled && state.settings.radio_enabled?' Radio control remains enabled.':''}</p>`:''}${!notificationOnly && h.error?`<p class="equipment-error">${esc(h.error)}</p>`:''}`;
  $('expandedSummary').textContent=`${visible.length} watched satellite${visible.length===1?'':'s'} above the horizon${visible.length?` · highest: ${visible[0].name} at ${visible[0].elevation.toFixed(1)}°`:''}`;
  $('expandedSatellites').innerHTML=visible.length?visible.map((row,i)=>`<article class="expanded-satellite ${i===0?'highest':''}">${liveDetails(row)}</article>`).join(''):'<p>No watched satellite is currently above the horizon. The sky continues to update.</p>';
}
function renderRotatorTest(result) {
  const visible=Boolean(state.settings.rotator_enabled && result);
  $('rotatorTestResult').classList.toggle('hidden',!visible);
  if(!visible)return;
  $('rotatorTestResult').classList.toggle('test-warning',result.status==='warning');
  $('rotatorTestResult').classList.toggle('test-error',result.status==='error');
  $('rotatorTestTitle').textContent=`Rotator connection test ${result.status==='success'?'succeeded':result.status==='warning'?'warning':'failed'}`;
  $('rotatorTestTime').dateTime=result.tested_at;
  $('rotatorTestTime').textContent=`Test time: ${formatTime(result.tested_at,true,true)} · ${timeZone==='UTC'?'UTC':'local time'}`;
  $('rotatorTestDetails').textContent=`Port: ${result.port || 'not configured'} · ${result.baud} baud · Command: C2`;
  $('rotatorTestMessage').textContent=result.message;
  const response=result.response?result.response.replace(/\r/g,'\\r').replace(/\n/g,'\\n\n'):(result.command_sent || result.command_attempted?'No response received.':'No command sent.');
  $('rotatorTestResponse').textContent=`Response to C2:\n${response}`;
}
function renderRadioTest(result) {
  const visible=Boolean(state.settings.radio_enabled && !state.hardware.radio_test_enabled && result && result.status!=='running');
  $('radioTestResult').classList.toggle('hidden',!visible);
  if(!visible)return;
  $('radioTestResult').classList.toggle('test-warning',result.status==='warning');
  $('radioTestResult').classList.toggle('test-error',result.status==='error');
  $('radioTestTitle').textContent=`Radio connection test ${result.status==='success'?'succeeded':result.status==='warning'?'stopped':'failed'}`;
  $('radioTestTime').dateTime=result.finished_at || result.tested_at;
  $('radioTestTime').textContent=`Test time: ${formatTime(result.finished_at || result.tested_at,true,true)} · ${timeZone==='UTC'?'UTC':'local time'}`;
  $('radioTestDetails').textContent=`Port: ${result.port} · ${result.baud} baud · Last verified MAIN: ${mhz(result.frequency_hz)}`;
  $('radioTestMessage').textContent=result.message;
}
async function refreshPorts(useSaved=false) {
  try {
    const response=await fetch('/api/ports',{cache:'no-store'});if(!response.ok)throw new Error('Cannot list serial ports.');
    const result=await response.json();serialPorts=result.ports;
    for(const key of ['rotator_port','radio_port']) {
      const select=$('settingsForm').elements.namedItem(key),chosen=useSaved?state.settings[key]:select.value;
      select.replaceChildren(new Option('Select a port',''));
      for(const port of serialPorts)select.add(new Option(`${port.device} · ${port.description}`,port.device));
      if(chosen && !serialPorts.some(p=>p.device===chosen))select.add(new Option(`${chosen} · unavailable`,chosen));
      select.value=chosen;
    }
    $('portsStatus').textContent=serialPorts.length?`${serialPorts.length} serial port(s) available.`:'No serial ports found. Connect your USB interfaces and refresh.';
  } catch(error){$('portsStatus').textContent=error.message;}
}
function renderProfiles() {
  if(!state)return;
  $('profileList').innerHTML=state.settings.satellites.map(name=>{const p=state.settings.satellite_profiles[name];return `<article class="profile-row"><strong>${esc(name)}</strong><label class="checkbox"><input type="checkbox" data-pause-satellite="${esc(name)}" aria-label="Pause notifications for ${esc(name)}" ${satelliteNotificationsPaused(name)?'checked':''}> Pause notifications for this satellite until you uncheck this</label><p>RX ${p.downlink_mhz==null?'unset':p.downlink_mhz.toFixed(6)+' MHz'} · ${esc(p.downlink_mode)}<br>TX ${p.uplink_mhz==null?'receive only':p.uplink_mhz.toFixed(6)+' MHz'} · ${esc(p.uplink_mode)}</p><p>${esc(p.notes)}</p><div class="profile-actions"><button type="button" class="button" data-edit="${esc(name)}">Edit ${esc(name)}</button><button type="button" class="text-button" data-delete="${esc(name)}">Remove ${esc(name)}</button></div></article>`;}).join('');
  $('profileList').querySelectorAll('[data-pause-satellite]').forEach(checkbox=>checkbox.addEventListener('change',async()=>{
    const name=checkbox.dataset.pauseSatellite,paused=checkbox.checked;checkbox.disabled=true;
    try{await post('/api/settings',{satellite_notifications_paused:{[name]:paused}});await refreshState();renderProfiles();if(editingSatellite===name)$('profilePause').checked=paused;toast(`${name} notifications ${paused?'paused until you uncheck it':'resumed'}.`);}
    catch(error){checkbox.checked=!paused;toast(error.message);}finally{checkbox.disabled=false;}
  }));
  $('profileList').querySelectorAll('[data-edit]').forEach(button=>button.addEventListener('click',()=>editProfile(button.dataset.edit)));
  $('profileList').querySelectorAll('[data-delete]').forEach(button=>button.addEventListener('click',async()=>{
    const name=button.dataset.delete,names=state.settings.satellites.filter(n=>n!==name),profiles={...state.settings.satellite_profiles};delete profiles[name];
    try{await post('/api/settings',{satellites:names,satellite_profiles:profiles});await refreshState();renderProfiles();toast(`${name} removed.`);}catch(error){toast(error.message);}
  }));
}
function fillProfile(p) {
  $('profileRx').value=p.downlink_mhz??'';$('profileTx').value=p.uplink_mhz??'';
  $('profileRxMode').value=p.downlink_mode||'USB';$('profileTxMode').value=p.uplink_mode||'LSB';$('profileNotes').value=p.notes||'';
}
function editProfile(name=null) {
  resetProfileLookup();manualProfileFields=new Set();automaticProfileValues={};
  editingSatellite=name;profileEditorOpen=true;$('profileEditor').classList.remove('hidden');
  $('profileEditorTitle').textContent=name?`Edit ${name}`:'Add satellite';$('profileName').value=name||'';$('profileError').textContent='';
  $('lookupSatellite').classList.toggle('hidden',Boolean(name));$('profileLookupStatus').classList.toggle('hidden',Boolean(name));
  $('profilePause').checked=name?satelliteNotificationsPaused(name):false;
  profileSource=name?state.settings.satellite_profiles[name].source||'':'';
  fillProfile(name?state.settings.satellite_profiles[name]:{});$('profileName').focus();
}
function resetProfileLookup(clearFilled=false) {
  clearTimeout(profileLookupTimer);profileLookupGeneration++;lastLookupName='';profileMatches=[];
  $('profileLookupChoices').classList.add('hidden');$('profileLookupSource').classList.add('hidden');
  $('profileLookupStatus').textContent='Enter a name to look up frequencies and modes automatically.';
  if(clearFilled){
    for(const [id,value] of Object.entries(automaticProfileValues))if(!manualProfileFields.has(id) && $(id).value===value)$(id).value=profileEmpty[id];
    automaticProfileValues={};profileSource='';
  }
}
function showProfileSource(source) {
  const box=$('profileLookupSource');box.replaceChildren();box.classList.toggle('hidden',!source);
  if(!source)return;
  box.append('Source: ');
  if(/^https:\/\/www\.amsat\.org\/live-(fm|linear)-satellites\/$/.test(source)){
    const link=document.createElement('a');link.href=source;link.target='_blank';link.rel='noopener';link.textContent='AMSAT frequency table ↗';box.append(link);
  }else box.append(source);
}
function applyLookupProfile(match) {
  let kept=false;
  for(const [id,key] of Object.entries(profileFields)){
    if(manualProfileFields.has(id)){kept=true;continue;}
    $(id).value=match.profile[key]??profileEmpty[id];automaticProfileValues[id]=$(id).value;
  }
  profileSource=match.profile.source||'';showProfileSource(profileSource);
  $('profileLookupStatus').textContent=`${match.label}: frequencies and modes filled in. Review and edit them before saving.${kept?' Values you entered manually have been kept.':''}`;
}
async function lookupSatelliteProfile(force=false) {
  clearTimeout(profileLookupTimer);
  if(!profileEditorOpen || editingSatellite)return;
  const name=$('profileName').value.trim().toUpperCase();
  if(!name || (!force && lastLookupName===name))return;
  lastLookupName=name;const generation=++profileLookupGeneration;
  $('profileLookupStatus').textContent='Looking up frequencies and modes…';$('profileLookupChoices').classList.add('hidden');
  const current=()=>profileEditorOpen && !editingSatellite && generation===profileLookupGeneration && $('profileName').value.trim().toUpperCase()===name;
  try{
    const result=await post('/api/profile-lookup',{name});if(!current())return;
    profileMatches=result.matches||[];$('profileLookupStatus').textContent=result.message;
    if(profileMatches.length===1){applyLookupProfile(profileMatches[0]);if(result.bundled)$('profileLookupStatus').textContent=result.message+' '+$('profileLookupStatus').textContent;}
    else if(profileMatches.length>1){
      $('profileLookupChoice').replaceChildren(new Option('Choose an operating profile',''));
      profileMatches.forEach((match,index)=>$('profileLookupChoice').add(new Option(`${match.label} · RX ${match.profile.downlink_mhz} MHz / TX ${match.profile.uplink_mhz} MHz`,String(index))));
      $('profileLookupChoices').classList.remove('hidden');
    }
  }catch(error){if(current())$('profileLookupStatus').textContent='Automatic lookup is unavailable. Enter the downlink, uplink, receive mode and transmit mode manually, or press Look up frequencies & modes to retry.';}
}
$('addSatellite').addEventListener('click',()=>editProfile());
$('cancelSatellite').addEventListener('click',()=>{resetProfileLookup();profileEditorOpen=false;$('profileEditor').classList.add('hidden');});
$('profileName').addEventListener('input',()=>{if(!editingSatellite){resetProfileLookup(true);if($('profileName').value.trim())profileLookupTimer=setTimeout(()=>lookupSatelliteProfile(),650);}});
$('profileName').addEventListener('change',()=>lookupSatelliteProfile());
$('lookupSatellite').addEventListener('click',()=>lookupSatelliteProfile(true));
$('profileLookupChoice').addEventListener('change',()=>{const index=$('profileLookupChoice').value;if(index!=='' && profileMatches[Number(index)])applyLookupProfile(profileMatches[Number(index)]);});
for(const id of Object.keys(profileFields))for(const event of ['input','change'])$(id).addEventListener(event,()=>manualProfileFields.add(id));
$('saveSatellite').addEventListener('click',async()=>{
  const name=$('profileName').value.trim().toUpperCase();
  if(!name){$('profileError').textContent='Enter a satellite name or NORAD number.';return;}
  if(name!==editingSatellite && state.settings.satellites.includes(name)){$('profileError').textContent='That satellite is already in your watch list.';return;}
  const names=editingSatellite?state.settings.satellites.map(n=>n===editingSatellite?name:n):[...state.settings.satellites,name];
  const profiles={...state.settings.satellite_profiles};if(editingSatellite)delete profiles[editingSatellite];
  const p={downlink_mhz:$('profileRx').value===''?null:Number($('profileRx').value),uplink_mhz:$('profileTx').value===''?null:Number($('profileTx').value),downlink_mode:$('profileRxMode').value,uplink_mode:$('profileTxMode').value,notes:$('profileNotes').value,source:profileSource};profiles[name]=p;
  const notifications={...state.settings.satellite_notifications_paused};if(editingSatellite)delete notifications[editingSatellite];notifications[name]=$('profilePause').checked;
  profileLookupGeneration++;clearTimeout(profileLookupTimer);
  try{await post('/api/settings',{satellites:names,satellite_profiles:profiles,satellite_notifications_paused:notifications});await refreshState();renderProfiles();profileEditorOpen=false;$('profileEditor').classList.add('hidden');toast(`${name} saved. Live tracking is off.`);}catch(error){$('profileError').textContent=error.message;}
});
$('scanPorts').addEventListener('click',()=>refreshPorts());
$('trackingToggle').addEventListener('click',async()=>{
  try{await post('/api/tracking',{enabled:!state.hardware.enabled});await refreshState();toast(state.hardware.enabled?'Live tracking enabled.':'Live tracking stopped.');}catch(error){toast(error.message);}
});
$('radioTestToggle').addEventListener('click',async()=>{
  $('radioTestToggle').disabled=true;
  try{await post('/api/radio-test',{enabled:!state.hardware.radio_test_enabled});await refreshState();toast(state.hardware.radio_test_enabled?'Radio connection test started.':'Radio connection test stopped.');}catch(error){toast(error.message);}finally{$('radioTestToggle').disabled=false;}
});
$('rotatorTest').addEventListener('click',async()=>{
  $('rotatorTest').disabled=true;$('rotatorTest').textContent='Testing rotator connection…';
  try{
    await post('/api/rotator-test');
    await refreshState();
  }catch(error){toast(error.message);}finally{$('rotatorTest').disabled=false;$('rotatorTest').textContent='Test rotator connection';}
});
for(const [device,buttonId,resultKey] of [['rotator','acknowledgeRotatorTest','rotator_test'],['radio','acknowledgeRadioTest','radio_test_result']]){
  $(buttonId).addEventListener('click',async()=>{
    const result=state.hardware[resultKey];if(!result)return;
    $(buttonId).disabled=true;
    try{await post('/api/acknowledge-test',{device,id:result.id});await refreshState();}
    catch(error){toast(error.message);}finally{$(buttonId).disabled=false;}
  });
}
function expandSky(){if(!$('skyDialog').open)$('skyDialog').showModal();renderTracking();requestAnimationFrame(drawSky);loadWorldLand();}
$('sky').addEventListener('click',expandSky);$('expandSky').addEventListener('click',expandSky);
$('sky').addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();expandSky();}});
$('closeSky').addEventListener('click',()=>$('skyDialog').close());
$('expandedMapMode').addEventListener('change',()=>{drawSky();if($('expandedMapMode').value==='world')loadWorldLand();});

async function enableAudio() {
  try {
    if(!audioContext || audioContext.state==='closed')audioContext=new (window.AudioContext || window.webkitAudioContext)();
    await audioContext.resume();
    if(audioContext.state!=='running')throw new Error('Audio is suspended.');
    browserSound=true;return true;
  } catch(error){browserSound=false;toast('Browser sound is blocked or unavailable. Check tab mute and your audio output, then click Enable browser sound.');return false;}
}
function chime() {
  if(!audioContext || audioContext.state!=='running') {browserSound=false;if(state)renderStatus();return;}
  const now=audioContext.currentTime;
  [660,880,1100,880].forEach((frequency,index)=>{
    const oscillator=audioContext.createOscillator(),gain=audioContext.createGain(),start=now+index*.29;
    oscillator.frequency.value=frequency;oscillator.type='sine';gain.gain.setValueAtTime(0,start);gain.gain.linearRampToValueAtTime(.25,start+.025);gain.gain.exponentialRampToValueAtTime(.001,start+.23);
    oscillator.connect(gain);gain.connect(audioContext.destination);oscillator.start(start);oscillator.stop(start+.25);
  });
}
$('enableSound').addEventListener('click',async()=>{if(await enableAudio()){chime();renderStatus();toast('Browser sound enabled for this tab.');}});
$('testAlert').addEventListener('click',async()=>{
  try{const ready=await enableAudio();await post('/api/test-alert');await refreshState();toast(!state.settings.sound_enabled?'Test banner triggered. Sound is disabled in Settings.':ready?'Test alert triggered. Browser sound enabled; it expires after 60 seconds.':'Test alert requested. Browser audio is blocked; check tab mute/output and click Enable browser sound.');}catch(error){toast(error.message);}
});
$('acknowledge').addEventListener('click',async()=>{try{await post('/api/acknowledge');await refreshState();toast('Acknowledged. This pass will stay silent.');}catch(error){toast(error.message);}});
$('history').addEventListener('click',async event=>{
  const button=event.target.closest('[data-acknowledge]');if(!button)return;
  try{await post('/api/acknowledge',{id:button.dataset.acknowledge});await refreshState();toast('Alert acknowledged.');}catch(error){toast(error.message);}
});
$('refresh').addEventListener('click',async()=>{try{await post('/api/refresh');toast('Downloading the latest AMSAT orbital elements…');}catch(error){toast(error.message);}});
$('pause').addEventListener('click',async()=>{try{await post('/api/pause',{paused:!state.paused});}catch(error){toast(error.message);}});
$('earthToggle').addEventListener('change',async event=>{
  const enabled=event.currentTarget.checked;
  try{await post('/api/settings',{earth_overlay:enabled});state.settings.earth_overlay=enabled;drawSky();renderStatus();toast(enabled?'Earth image shown.':'Earth image hidden.');}
  catch(error){$('earthToggle').checked=Boolean(state && state.settings.earth_overlay);toast(error.message);}
});
$('morePasses').addEventListener('click',()=>{showAll=!showAll;renderTable();});
$('timeZone').addEventListener('change',()=>{timeZone=$('timeZone').value;localStorage.setItem('satellite-time-zone',timeZone);render();});
$('settingsForm').addEventListener('submit',async event=>{
  event.preventDefault();const form=event.currentTarget,data={};
  if(profileEditorOpen){toast('Save or cancel the satellite edit before saving other settings.');return;}
  for(const [key] of Object.entries(state.settings)) {
    const field=form.elements.namedItem(key);if(!field)continue;
    data[key]=field.type==='checkbox'?field.checked:field.type==='number'||['rotator_baud','radio_baud','rotator_max_azimuth'].includes(key)?Number(field.value):field.value;
  }
  const recalculate=Object.keys(data).some(key=>!['earth_overlay','windows_maximum_volume','windows_volume_percent','auto_acknowledge_enabled','auto_acknowledge_seconds','automatic_pause_enabled','automatic_pause_start','automatic_pause_hours','rotator_enabled','rotator_port','rotator_baud','rotator_max_azimuth','radio_enabled','radio_port','radio_baud','radio_address','radio_step_hz','tracking_minimum_mode'].includes(key) && JSON.stringify(data[key])!==JSON.stringify(state.settings[key]));
  try{await post('/api/settings',data);if(recalculate){selectedPass=null;selectedName=null;}await refreshState();$('saveStatus').textContent=recalculate?'Saved. Recalculating your passes…':'Settings saved.';toast('Settings saved.');}catch(error){$('saveStatus').textContent=error.message;toast(error.message);}
});
$('enableNotifications').addEventListener('click',async()=>{
  if(!('Notification' in window)){toast('This browser does not support notifications.');return;}
  const permission=await Notification.requestPermission();$('notificationStatus').textContent=`Browser permission: ${permission}`;
});
$('stopApp').addEventListener('click',async()=>{
  try{await post('/api/stop');toast('App stopped. Start the program again to resume alerts.');}catch(error){toast(error.message);}
});
window.addEventListener('resize',drawSky);
setInterval(()=>{
  $('clock').textContent=new Intl.DateTimeFormat('en-GB',{timeZone:'UTC',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date())+' UTC';
  if(state && !disconnected)updateCountdown();
},1000);
poll();
