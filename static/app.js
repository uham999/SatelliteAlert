'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
let state = null, selectedPass = null, selectedName = null, showAll = false, timeZone = localStorage.getItem('satellite-time-zone') || 'local';
let audioContext = null, browserSound = false, soundSerial = null, notificationIds = new Set(), disconnected = false;
let view = 'dashboard', toastTimer;
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
    if (field.type === 'checkbox') field.checked = value;
    else field.value = key === 'satellites' ? value.join('\n') : value;
  }
  $('notificationStatus').textContent = 'Notification' in window ? `Browser permission: ${Notification.permission}` : 'Notifications unavailable in this browser.';
}
function choosePass() {
  if (!state || !state.passes.length) return null;
  if (selectedPass) {
    const found = state.passes.find(pass=>pass.norad === selectedPass.norad && Math.abs(new Date(pass.threshold_rise)-new Date(selectedPass.threshold_rise))<1200000);
    if (found) return found;
  }
  if (selectedName) return state.passes.find(pass=>pass.name === selectedName) || null;
  return state.passes[0];
}
function renderNext() {
  const pass = choosePass(), settings = state.settings, minimum = settings.minimum_elevation;
  const horizon = settings.alert_mode === 'horizon', early = earlyAlertMinutes[settings.alert_mode] || 0;
  $('thresholdChip').textContent = `≥ ${minimum}° elevation`;
  $('alertRule').textContent = early ? `Alerts ${early} min before horizon rise · peak ≥ ${minimum}°` : horizon ? `Alerts at horizon rise · peak ≥ ${minimum}°` : `Alerts at ${minimum}° above your horizon`;
  $('alertTimeLabel').textContent = early ? 'Early alert time' : horizon ? 'Horizon rise / alert' : `Reaches ${minimum}°`;
  $('ringLabel').textContent = `${minimum}° alert boundary`;
  $('nextName').textContent = pass ? pass.name : (state.forecast_at ? (selectedName || 'No passes') : 'Calculating…');
  $('nextSubtitle').textContent = pass ? `${dayLabel(pass.alert_at)} · ${timeZone === 'UTC' ? 'UTC' : Intl.DateTimeFormat().resolvedOptions().timeZone} · NORAD ${pass.norad}` : state.forecast_at ? `No qualifying passes in the next ${settings.forecast_hours} hours.` : 'Downloading orbital elements and calculating passes.';
  $('nextStart').textContent = pass ? formatTime(pass.alert_at,true) : '—';
  $('nextPeak').textContent = pass ? `${pass.peak_elevation.toFixed(1)}°` : '—';
  $('nextDuration').textContent = pass ? duration((new Date(pass.threshold_set)-new Date(pass.threshold_rise))/1000) : '—';
  $('pathLabel').textContent = pass ? `${pass.name} path` : 'Predicted path';
  $('skyCaption').textContent = selectedName || selectedPass ? 'Selected pass path' : 'Next pass path';
  updateCountdown(); drawSky();
}
function updateCountdown() {
  const pass = choosePass();
  if (!pass) {$('countdown').textContent='-- : -- : --';return;}
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
    return `<button class="sat-card ${high?'active':''} ${selectedName===row.name?'focused':''}" data-satellite="${esc(row.name)}" title="${esc(row.detail + age)}"><div class="sat-heading"><strong>${esc(row.name)}</strong><span class="sat-status ${up?'up':!ready?'warn':''}">${esc(tag)}</span></div><div class="sat-metrics"><div><span>ELEVATION</span><strong>${ready && row.elevation != null ? row.elevation.toFixed(1)+'°':'—'}</strong></div><div><span>AZIMUTH</span><strong>${ready && row.azimuth != null ? row.azimuth.toFixed(0)+'°':'—'}</strong></div></div><div class="sat-bottom">${esc(detail)}${esc(age)}</div></button>`;
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
  const active=state.alerts.filter(alert=>alert.active);
  $('alertBanner').classList.toggle('hidden',!active.length);
  if (active.length) {
    const tests=active.every(alert=>alert.test);
    $('alertType').textContent=tests ? 'TEST ALERT · DEMONSTRATION' : 'SATELLITE PASS ALERT';
    const approaching=active.some(alert=>!alert.test && alert.horizon_rise && Date.now()<new Date(alert.horizon_rise));
    $('alertTitle').textContent=tests ? 'This is how your alert looks.' : active.filter(alert=>!alert.test).map(alert=>alert.name).join(' + ')+(approaching?' · pass approaching':' above your horizon');
    $('alertMessage').textContent=tests ? 'A chime has been requested. Real passes use this banner and your chosen popup setting.' : active.map(alert=>alert.message).join(' ');
  }
  for (const alert of active) {
    if (!notificationIds.has(alert.id)) {
      notificationIds.add(alert.id);
      if ('Notification' in window && Notification.permission==='granted') {
        try{new Notification(alert.test?'Satellite Alert · Test':`Satellite Alert · ${alert.name}`,{body:alert.message,tag:alert.id});}catch(error){console.warn(error);}
      }
    }
  }
  if(soundSerial!==null && state.sound_serial!==soundSerial && browserSound && state.settings.sound_enabled && (!state.native_sound || state.sound_status.includes('failed'))) chime();
  soundSerial=state.sound_serial;
  $('history').innerHTML=state.alerts.length ? state.alerts.map(alert=>`<div class="history-item"><div><strong>${esc(alert.test?'TEST ALERT':alert.name)}</strong> <span class="tag">${alert.acknowledged?'ACKNOWLEDGED':alert.active?'ACTIVE':'ENDED'}</span><p>${esc(alert.message)}</p></div><time>${formatTime(alert.created_at,true,true)}</time></div>`).join('') : '<p class="empty">No alerts yet. Use Test alert to check the banner and chime.</p>';
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
  $('soundStatus').textContent=s.sound_enabled ? (browserSound && !state.native_sound ? 'Browser chime enabled' : state.sound_status) : 'Sound disabled in Settings';
  $('enableSound').classList.toggle('hidden',state.native_sound && !state.sound_status.includes('failed') || browserSound);
}
function render() {renderStatus();renderNext();renderFleet();renderTable();renderAlerts();}
async function poll() {
  try {
    const response=await fetch('/api/state',{cache:'no-store',signal:AbortSignal.timeout(8000)});
    if(!response.ok) throw new Error('App unavailable');
    const initial=!state;state=await response.json();disconnected=false;render();if(initial) fillSettings();
  }catch(error){
    disconnected=true;$('connection').classList.add('offline');$('connection').innerHTML='<i></i> Monitor disconnected';
    $('notice').textContent='The dashboard cannot reach the Python monitor. Start the program again to resume live tracking and alerts.';$('notice').classList.remove('hidden');
  }finally{setTimeout(poll,2000);}
}

function drawSky() {
  const canvas=$('sky');if(!canvas.clientWidth || view!=='dashboard') return;
  const rect=canvas.getBoundingClientRect(),scale=window.devicePixelRatio||1;
  canvas.width=rect.width*scale;canvas.height=rect.height*scale;
  const ctx=canvas.getContext('2d');ctx.scale(scale,scale);
  const w=rect.width,h=rect.height,cx=w/2,cy=h/2,r=Math.min(w,h)/2-28;
  ctx.font='18px Segoe UI';ctx.textAlign='center';ctx.textBaseline='middle';
  ctx.fillStyle='#f9fbf9';ctx.beginPath();ctx.arc(cx,cy,r,0,Math.PI*2);ctx.fill();
  if (state && state.settings.earth_overlay && earthImage.complete && earthImage.naturalWidth) {
    ctx.save();ctx.beginPath();ctx.arc(cx,cy,r,0,Math.PI*2);ctx.clip();ctx.globalAlpha=.42;
    ctx.drawImage(earthImage,cx-r,cy-r,r*2,r*2);ctx.restore();
  }
  ctx.strokeStyle='#e0e8e3';ctx.lineWidth=1;
  for(const elevation of [0,30,60]){ctx.beginPath();ctx.arc(cx,cy,r*(1-elevation/90),0,Math.PI*2);ctx.stroke();}
  ctx.strokeStyle='#e9eeeb';ctx.beginPath();ctx.moveTo(cx-r,cy);ctx.lineTo(cx+r,cy);ctx.moveTo(cx,cy-r);ctx.lineTo(cx,cy+r);ctx.stroke();
  const threshold=state?state.settings.minimum_elevation:35;
  ctx.strokeStyle='#d6a246';ctx.setLineDash([4,5]);ctx.beginPath();ctx.arc(cx,cy,r*(1-threshold/90),0,Math.PI*2);ctx.stroke();ctx.setLineDash([]);
  ctx.fillStyle='#7f928c';for(const [label,x,y] of [['N',cx,cy-r-15],['E',cx+r+16,cy],['S',cx,cy+r+16],['W',cx-r-16,cy]])ctx.fillText(label,x,y);
  ctx.font='18px Segoe UI';ctx.fillStyle='#667b73';ctx.fillText('90°',cx+22,cy+15);ctx.fillText('0°',cx+r-23,cy+15);
  const point=(alt,az)=>{const distance=r*(1-Math.max(0,Math.min(90,alt))/90),angle=az*Math.PI/180;return [cx+Math.sin(angle)*distance,cy-Math.cos(angle)*distance];};
  const pass=choosePass();if(!pass) return;
  const path=pass.path;
  for(let i=1;i<path.length;i++){
    const from=point(path[i-1].elevation,path[i-1].azimuth),to=point(path[i].elevation,path[i].azimuth);
    ctx.strokeStyle=path[i].elevation>=threshold?'#007c7a':'#9ecac1';ctx.lineWidth=path[i].elevation>=threshold?2.8:1.8;
    ctx.beginPath();ctx.moveTo(...from);ctx.lineTo(...to);ctx.stroke();
  }
  const peak=path.reduce((best,item)=>item.elevation>best.elevation?item:best,path[0]),peakPoint=point(peak.elevation,peak.azimuth);
  ctx.fillStyle='#007c7a';ctx.beginPath();ctx.arc(...peakPoint,4,0,Math.PI*2);ctx.fill();ctx.font='18px Segoe UI';
  const direction=peakPoint[0]>cx?-1:1;ctx.textAlign=direction===1?'left':'right';ctx.fillText(`${pass.peak_elevation.toFixed(0)}° peak`,peakPoint[0]+direction*10,peakPoint[1]-10);ctx.textAlign='center';
  const rise=point(path[0].elevation,path[0].azimuth);ctx.fillStyle='#edf7f1';ctx.beginPath();ctx.arc(...rise,4,0,Math.PI*2);ctx.fill();ctx.strokeStyle='#007c7a';ctx.lineWidth=1.5;ctx.stroke();
  const row=state.satellites.find(item=>item.name===pass.name);
  if(row && row.elevation>=0 && Date.now()>=new Date(pass.horizon_rise) && Date.now()<=new Date(pass.horizon_set)){
    const position=point(row.elevation,row.azimuth);ctx.fillStyle='#f8b956';ctx.beginPath();ctx.arc(...position,7,0,Math.PI*2);ctx.fill();ctx.strokeStyle='#fff';ctx.lineWidth=2;ctx.stroke();
  }
}
function enableAudio() {
  try{audioContext ??= new (window.AudioContext || window.webkitAudioContext)();audioContext.resume();browserSound=true;return true;}catch(error){toast('Browser sound is unavailable. Check your audio settings.');return false;}
}
function chime() {
  if(!audioContext) return;
  audioContext.resume();const now=audioContext.currentTime;
  [660,880,1100,880].forEach((frequency,index)=>{
    const oscillator=audioContext.createOscillator(),gain=audioContext.createGain(),start=now+index*.29;
    oscillator.frequency.value=frequency;oscillator.type='sine';gain.gain.setValueAtTime(0,start);gain.gain.linearRampToValueAtTime(.15,start+.025);gain.gain.exponentialRampToValueAtTime(.001,start+.23);
    oscillator.connect(gain);gain.connect(audioContext.destination);oscillator.start(start);oscillator.stop(start+.25);
  });
}
$('enableSound').addEventListener('click',()=>{if(enableAudio()){chime();renderStatus();toast('Browser sound enabled for this tab.');}});
$('testAlert').addEventListener('click',async()=>{
  try{if(state && (!state.native_sound || state.sound_status.includes('failed')))enableAudio();await post('/api/test-alert');toast('Test alert triggered. It expires after 60 seconds.');}catch(error){toast(error.message);}
});
$('acknowledge').addEventListener('click',async()=>{try{await post('/api/acknowledge');toast('Acknowledged. This pass will stay silent.');}catch(error){toast(error.message);}});
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
  for(const [key] of Object.entries(state.settings)) {
    const field=form.elements.namedItem(key);if(!field)continue;
    data[key]=field.type==='checkbox'?field.checked:key==='satellites'?field.value.split(/[\n,]+/).map(name=>name.trim()).filter(Boolean):field.type==='number'?Number(field.value):field.value;
  }
  const recalculate=Object.keys(data).some(key=>key!=='earth_overlay' && JSON.stringify(data[key])!==JSON.stringify(state.settings[key]));
  try{await post('/api/settings',data);if(recalculate){selectedPass=null;selectedName=null;}$('saveStatus').textContent=recalculate?'Saved. Recalculating your passes…':'Settings saved.';toast('Settings saved.');}catch(error){$('saveStatus').textContent=error.message;toast(error.message);}
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
