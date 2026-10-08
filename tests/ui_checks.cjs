'use strict';
// Pure DOM tests with fake HTTP and canvas APIs. No browser, network or serial device.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM, VirtualConsole} = require('jsdom');
const root = path.join(__dirname, '..');
const clone = value => JSON.parse(JSON.stringify(value));
const flush = async () => {for (let i=0;i<8;i++) await new Promise(resolve=>setImmediate(resolve));};

async function dashboard(t, changes={}) {
  const settings=JSON.parse(fs.readFileSync(path.join(root,'settings.example.json'),'utf8'));
  const now=new Date().toISOString(),errors=[],requests=[],paint=[];
  const elevations={'RS-44':12,'ISS':62,'FO-29':23,'AO-73':-5,'JO-97':-12,'SO-50':-20,'AO-123':-40};
  let server={token:'offline-test-token',settings,now,last_tick:now,forecast_at:now,
    fetched_at:now,next_refresh_at:now,fetch_error:null,forecast_error:null,warnings:[],
    updating:false,paused:false,pause_reason:null,automatic_pause_overridden:false,
    sound_status:'Off in test',native_sound:false,sound_serial:0,alerts:[],passes:[],
    satellites:settings.satellites.map((name,index)=>({name,norad:100+index,status:'tracking',
      azimuth:70+index*35,elevation:elevations[name],range_km:1200,range_rate_km_s:-1,
      ground_latitude:48+index*2,ground_longitude:-8+index*2,satellite_height_km:800,
      detail:'Fresh test position',tle_age_days:.1,next_pass:null,
      doppler:{rx_hz:437801000,tx_hz:145989000,rx_shift_hz:1000,tx_shift_hz:-1000}})),
    hardware:{enabled:false,radio_test_enabled:false,radio_test_frequency_hz:null,error:null,rotator_status:'Off',radio_status:'Off',rotator_position:null,
      commanded_position:null,radio_rx_hz:null,radio_tx_hz:null},...changes};
  const updateTarget=()=>{server.tracking_target=clone(server.satellites.filter(row=>row.status==='tracking' && row.elevation>=0).sort((a,b)=>b.elevation-a.elevation)[0] || null);};
  updateTarget();
  const vc=new VirtualConsole();vc.on('jsdomError',error=>errors.push(error.message));
  const dom=new JSDOM(fs.readFileSync(path.join(root,'static/index.html'),'utf8'),
    {url:'http://127.0.0.1:8765/',runScripts:'outside-only',virtualConsole:vc});
  t.after(async()=>{await flush();dom.window.close();assert.deepEqual(errors,[]);});
  const w=dom.window,$=id=>w.document.getElementById(id);
  w.setTimeout=()=>0;w.clearTimeout=()=>{};w.setInterval=()=>0;
  w.AbortSignal.timeout=()=>new w.AbortController().signal;
  w.requestAnimationFrame=fn=>{fn();return 0;};
  w.Image=class {constructor(){this.complete=true;this.naturalWidth=1024;}addEventListener(){}};
  w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
  w.HTMLDialogElement.prototype.close=function(){this.open=false;};
  w.HTMLCanvasElement.prototype.getBoundingClientRect=function(){const size=this.id==='expandedSky'?950:420;return {width:size,height:size};};
  Object.defineProperty(w.HTMLCanvasElement.prototype,'clientWidth',{get(){return this.getBoundingClientRect().width;}});
  w.HTMLCanvasElement.prototype.getContext=function(){
    const canvas=this.id,ctx={};
    return new Proxy(ctx,{get:(obj,key)=>key in obj?obj[key]:key==='measureText'?text=>({width:text.length*11}):(...args)=>paint.push({canvas,op:key,args,fillStyle:ctx.fillStyle,font:ctx.font}),set:(obj,key,value)=>{obj[key]=value;return true;}});
  };
  const ports=[{device:'COM10',description:'Test rotator'},{device:'COM11',description:'Test radio'}];
  w.fetch=async (url,options={})=>{
    const body=options.body?JSON.parse(options.body):null;requests.push({url,body,options});
    let result,ok=true;
    if(url==='/api/state')result=server;
    else if(url==='/world-land.json')result=JSON.parse(fs.readFileSync(path.join(root,'static/world-land.json'),'utf8'));
    else if(url==='/api/ports')result={ports,default_profiles:settings.satellite_profiles};
    else if(url==='/api/settings'){
      server.settings={...server.settings,...body};server.hardware.enabled=false;
      server.satellites=server.satellites.filter(row=>server.settings.satellites.includes(row.name));updateTarget();result={ok:true};
    } else if(url==='/api/tracking'){
      if(body.enabled && server.hardware.radio_test_enabled){ok=false;result={error:'Stop the radio connection test before starting live tracking.'};}
      else if(body.enabled && (!server.settings.rotator_port || !server.settings.radio_port)){ok=false;result={error:'Select an available serial port in Settings, then save.'};}
      else{server.hardware.enabled=body.enabled;result={ok:true};}
    } else if(url==='/api/radio-test'){
      if(body.enabled && !ports.some(port=>port.device===server.settings.radio_port)){ok=false;result={error:'Select an available radio serial port in Settings, then save.'};}
      else{server.hardware.enabled=false;server.hardware.radio_test_enabled=body.enabled;server.hardware.radio_test_frequency_hz=null;result={ok:true};}
    } else if(url==='/api/test-alert'){
      server.sound_serial++;server.alerts=[{id:'test-audio',name:'TEST',test:true,active:true,acknowledged:false,created_at:now,message:'TEST ALERT'}];result={ok:true};
    } else if(url==='/api/acknowledge'){
      for(const alert of server.alerts)if(!body.id || alert.id===body.id){alert.acknowledged=true;alert.active=false;}result={ok:true};
    } else if(url==='/api/pause'){server.paused=body.paused;server.pause_reason=body.paused?'manual':null;result={ok:true};}
    else throw new Error(`Unexpected test request: ${url}`);
    return {ok,json:async()=>clone(result)};
  };
  for(const name of ['d3-array.min.js','d3-geo.min.js'])w.eval(fs.readFileSync(path.join(root,'static',name),'utf8'));
  w.eval(fs.readFileSync(path.join(root,'static/app.js'),'utf8')+'\nwindow.__test={refreshState,fillSettings};');await flush();
  assert.deepEqual(errors,[]);assert.doesNotMatch($('connection').textContent,/disconnected/);
  return {w,$,paint,requests,errors,server,updateTarget,refresh:async()=>{updateTarget();await w.__test.refreshState();await flush();}};
}

test('initial dashboard renders equipment preview and seven editable profiles with hardware off',async t=>{
  const { $,errors }=await dashboard(t);
  assert.equal($('trackingToggle').textContent,'Start live tracking');
  assert.match($('liveTarget').textContent,/ISS/);assert.match($('liveTarget').textContent,/62.0°/);
  assert.match($('liveTarget').textContent,/437.801000 MHz/);
  assert.equal($('profileList').querySelectorAll('[data-edit]').length,7);
  assert.equal($('earthToggle').checked,true);assert.deepEqual(errors,[]);
});

test('Earth click, expand button and keyboard open the live dialog and close button dismisses it',async t=>{
  const { $,w }=await dashboard(t);
  $('sky').click();assert.equal($('skyDialog').open,true);
  $('closeSky').click();assert.equal($('skyDialog').open,false);
  $('expandSky').click();assert.equal($('skyDialog').open,true);$('closeSky').click();
  $('sky').dispatchEvent(new w.KeyboardEvent('keydown',{key:'Enter',bubbles:true}));assert.equal($('skyDialog').open,true);
  assert.equal($('expandedSatellites').querySelectorAll('article').length,3);
  assert.equal($('expandedSatellites').querySelector('.highest strong').textContent,'ISS');
});

test('sky draws every visible satellite without a qualifying forecast, with a larger gold highest marker',async t=>{
  const {paint,$}=await dashboard(t);$('expandedMapMode').value='sky';paint.length=0;$('expandSky').click();
  const labels=paint.filter(p=>p.canvas==='expandedSky' && p.op==='fillText').map(p=>p.args[0]);
  for(const name of ['RS-44','FO-29','ISS'])assert.ok(labels.includes(name));
  assert.ok(!labels.includes('AO-73'));
  assert.ok(paint.some(p=>p.op==='arc' && p.args[2]===11 && p.fillStyle==='#e5a32c'));
  assert.ok(paint.some(p=>p.op==='arc' && p.args[2]===7 && p.fillStyle==='#007c7a'));
});

test('fresh positions move markers and hand prominence to the new highest elevation',async t=>{
  const {server,paint,$,refresh}=await dashboard(t);$('expandedMapMode').value='sky';$('expandSky').click();await flush();
  const old=paint.find(p=>p.canvas==='expandedSky' && p.op==='arc' && p.args[2]===11).args.slice(0,2);
  server.satellites.find(s=>s.name==='ISS').elevation=5;
  const next=server.satellites.find(s=>s.name==='FO-29');next.elevation=70;next.azimuth=310;
  paint.length=0;await refresh();
  assert.equal($('expandedSatellites').querySelector('.highest strong').textContent,'FO-29');
  const moved=paint.find(p=>p.canvas==='expandedSky' && p.op==='arc' && p.args[2]===11).args.slice(0,2);
  assert.notDeepEqual(moved,old);assert.match($('liveTarget').textContent,/70.0°/);
});

test('one Earth toggle persists and controls the background in both sky canvases',async t=>{
  const { $,w,paint,requests }=await dashboard(t);$('expandedMapMode').value='sky';$('expandSky').click();await flush();paint.length=0;
  $('earthToggle').checked=false;$('earthToggle').dispatchEvent(new w.Event('change'));await flush();
  assert.equal(requests.at(-1).body.earth_overlay,false);assert.equal(paint.filter(p=>p.op==='drawImage').length,0);
  $('earthToggle').checked=true;$('earthToggle').dispatchEvent(new w.Event('change'));await flush();
  assert.deepEqual(new Set(paint.filter(p=>p.op==='drawImage').map(p=>p.canvas)),new Set(['sky','expandedSky']));
  assert.equal(w.document.querySelectorAll('input[name="earth_overlay"]').length,0);
});

test('enlarged Earth uses geographic positions and a ground-station-centred projection',async t=>{
  const { $,server,paint,refresh }=await dashboard(t);$('expandSky').click();await flush();
  assert.equal($('expandedMapMode').value,'world');assert.match($('expandedMapNote').textContent,/point on Earth beneath/);
  const satellite=server.satellites.find(s=>s.name==='ISS'),s=server.settings,r=950/2-24,rad=Math.PI/180;
  const lat=satellite.ground_latitude*rad,lon=(satellite.ground_longitude-s.longitude)*rad,lat0=s.latitude*rad;
  const expected=[475+r*Math.cos(lat)*Math.sin(lon),475-r*(Math.cos(lat0)*Math.sin(lat)-Math.sin(lat0)*Math.cos(lat)*Math.cos(lon))];
  const old=paint.find(p=>p.canvas==='expandedSky' && p.op==='arc' && p.args[2]===11).args.slice(0,2);
  for(let i=0;i<2;i++)assert.ok(Math.abs(old[i]-expected[i])<.0001);
  satellite.ground_longitude+=15;paint.length=0;await refresh();
  const moved=paint.find(p=>p.canvas==='expandedSky' && p.op==='arc' && p.args[2]===11).args.slice(0,2);
  assert.notDeepEqual(moved,old);assert.equal(satellite.elevation,62);
});

test('reported rotator angles and verified radio frequencies are distinct from the requested direction',async t=>{
  const {server,refresh,$}=await dashboard(t);
  Object.assign(server.hardware,{enabled:true,rotator_position:{azimuth:123,elevation:44},
    commanded_position:{azimuth:125,elevation:46},radio_rx_hz:437801002,radio_tx_hz:145989003});await refresh();
  assert.match($('equipmentStatus').textContent,/Reported: az 123° · el 44°/);
  assert.match($('equipmentStatus').textContent,/Last command: az 125° · el 46°/);
  assert.match($('equipmentStatus').textContent,/437.801002 MHz/);
  assert.equal($('trackingToggle').textContent,'Stop live tracking');
});

test('satellite editor creates, reads, updates, renames and deletes a custom profile',async t=>{
  const { $,server }=await dashboard(t);
  $('addSatellite').click();$('profileName').value='12345';$('profileRx').value='437.9';$('profileTx').value='145.8';
  $('profileRxMode').value='FM';$('profileTxMode').value='FM';$('profileNotes').value='Custom channel';
  $('saveSatellite').click();await flush();assert.ok(server.settings.satellites.includes('12345'));
  assert.match($('profileList').textContent,/437.900000 MHz/);
  $('profileList').querySelector('[data-edit="12345"]').click();assert.equal($('profileRx').value,'437.9');
  $('profileName').value='12346';$('profileRx').value='437.91';$('profileTx').value='';$('saveSatellite').click();await flush();
  assert.equal(server.settings.satellite_profiles['12346'].uplink_mhz,null);
  assert.ok(!server.settings.satellites.includes('12345'));assert.equal(server.settings.satellite_profiles['12346'].downlink_mhz,437.91);
  $('profileList').querySelector('[data-delete="12346"]').click();await flush();
  assert.ok(!server.settings.satellites.includes('12346'));assert.equal($('profileList').querySelectorAll('[data-edit]').length,7);
});

test('duplicate names and an unfinished profile edit are blocked without saving global settings',async t=>{
  const { $,w,requests }=await dashboard(t);$('addSatellite').click();$('profileName').value='ISS';$('saveSatellite').click();await flush();
  assert.match($('profileError').textContent,/already/);
  $('settingsForm').dispatchEvent(new w.Event('submit',{cancelable:true}));await flush();
  assert.match($('toast').textContent,/Save or cancel/);assert.ok(!requests.some(r=>r.url==='/api/settings'));
  $('cancelSatellite').click();assert.ok($('profileEditor').classList.contains('hidden'));
});

test('detected COM options and numeric serial settings save correctly; missing configured ports remain visible',async t=>{
  const { $,w,server,requests }=await dashboard(t);
  const form=$('settingsForm');assert.match(form.elements.rotator_port.textContent,/COM10 · Test rotator/);
  form.elements.rotator_port.value='COM10';form.elements.radio_port.value='COM11';
  assert.ok([...form.elements.radio_baud.options].some(option=>option.value==='57600'));
  assert.equal(form.elements.radio_baud.value,'57600');
  form.elements.radio_baud.value='57600';form.elements.rotator_max_azimuth.value='450';
  form.dispatchEvent(new w.Event('submit',{cancelable:true}));await flush();
  const saved=requests.find(r=>r.url==='/api/settings').body;
  assert.equal(saved.radio_baud,57600);assert.equal(saved.rotator_max_azimuth,450);
  server.settings.radio_port='COM99';await w.__test.refreshState();w.__test.fillSettings();await flush();
  assert.equal(form.elements.radio_port.value,'COM99');assert.match(form.elements.radio_port.textContent,/COM99 · unavailable/);
});

test('master button reports missing ports, then starts and stops configured equipment with authenticated requests',async t=>{
  const { $,server,requests,refresh }=await dashboard(t);
  $('trackingToggle').click();await flush();assert.match($('toast').textContent,/Select an available/);
  server.settings.rotator_port='COM10';server.settings.radio_port='COM11';await refresh();
  $('trackingToggle').click();await flush();assert.equal(server.hardware.enabled,true);
  assert.equal($('trackingToggle').textContent,'Stop live tracking');$('trackingToggle').click();await flush();
  assert.equal(server.hardware.enabled,false);
  assert.equal(requests.filter(r=>r.url==='/api/tracking').at(-1).options.headers['X-Satellite-Token'],'offline-test-token');
});

test('paused alerts retain the live target and independent tracking controls',async t=>{
  const { $,server,refresh }=await dashboard(t);server.paused=true;server.pause_reason='automatic';server.hardware.enabled=true;await refresh();
  assert.match($('notice').textContent,/Alerts are paused. Positions and orbital data continue to update/);
  assert.match($('liveTarget').textContent,/ISS/);assert.equal($('trackingToggle').textContent,'Stop live tracking');
});

test('empty sky shows a useful status without fabricating a satellite marker',async t=>{
  const { $,server,paint,refresh }=await dashboard(t);for(const s of server.satellites)s.elevation=-10;paint.length=0;await refresh();
  assert.match($('liveTarget').textContent,/No watched satellite/);assert.match($('expandedSummary').textContent,/0 watched satellites/);
  assert.ok(!paint.some(p=>p.op==='arc' && p.args[2]===11));
});

function fakeAudio(w,blocked=false) {
  const recordings={resumes:0,tones:[]};
  w.AudioContext=class {
    constructor(){this.state='suspended';this.currentTime=0;this.destination={};}
    async resume(){recordings.resumes++;if(blocked)throw new Error('Autoplay blocked');this.state='running';}
    createGain(){return {gain:{setValueAtTime(){},linearRampToValueAtTime(){},exponentialRampToValueAtTime(){}},connect(){}};}
    createOscillator(){const oscillator={frequency:{value:0},connect(){},start(){recordings.tones.push(oscillator.frequency.value);},stop(){}};return oscillator;}
  };
  return recordings;
}

test('browser audio stays available on Windows and Test alert activates and plays it',async t=>{
  const { $,w,server,refresh }=await dashboard(t);server.native_sound=true;server.sound_status='Windows WAV playback completed';await refresh();
  assert.ok(!$('enableSound').classList.contains('hidden'));
  const sounds=fakeAudio(w);$('testAlert').click();await flush();
  assert.equal(sounds.resumes,1);assert.deepEqual(sounds.tones,[660,880,1100,880]);
  assert.match($('soundStatus').textContent,/Browser sound enabled/);assert.ok($('enableSound').classList.contains('hidden'));
});

test('real alerts sound through the enabled browser even when Windows reports success; pause and acknowledgement suppress them',async t=>{
  const { $,w,server,refresh }=await dashboard(t);server.native_sound=true;await refresh();
  const sounds=fakeAudio(w);$('enableSound').click();await flush();sounds.tones.length=0;
  server.alerts=[{id:'real',name:'ISS',test:false,active:true,acknowledged:false,created_at:server.now,message:'ISS approaching'}];
  server.sound_serial++;await refresh();assert.equal(sounds.tones.length,4);
  await refresh();assert.equal(sounds.tones.length,4);
  server.paused=true;server.sound_serial++;await refresh();assert.equal(sounds.tones.length,4);
  server.paused=false;server.alerts[0].active=false;server.alerts[0].acknowledged=true;server.sound_serial++;await refresh();assert.equal(sounds.tones.length,4);
  server.alerts[0].active=true;server.alerts[0].acknowledged=false;server.sound_serial=0;await refresh();assert.equal(sounds.tones.length,4);
});

test('browser audio denial is reported and its enable button remains available',async t=>{
  const { $,w }=await dashboard(t);const sounds=fakeAudio(w,true);
  $('testAlert').click();await flush();assert.equal(sounds.tones.length,0);
  assert.match($('toast').textContent,/Browser audio is blocked/);
  assert.ok(!$('enableSound').classList.contains('hidden'));assert.doesNotMatch($('soundStatus').textContent,/Browser sound enabled/);
});

test('test alerts can check browser sound while paused, and disabled sound settings suppress the alert chime',async t=>{
  const { $,w,server,refresh }=await dashboard(t);const sounds=fakeAudio(w);
  server.paused=true;await refresh();$('testAlert').click();await flush();assert.equal(sounds.tones.length,4);
  server.settings.sound_enabled=false;await refresh();$('testAlert').click();await flush();assert.equal(sounds.tones.length,4);
  assert.match($('toast').textContent,/Sound is disabled/);
});

test('radio test button starts and stops the server test, shows a yellow status and prevents competing tracking',async t=>{
  const { $,server,refresh,requests }=await dashboard(t);server.settings.radio_port='COM11';server.hardware.enabled=true;server.paused=true;await refresh();
  assert.equal($('radioTestToggle').textContent,'Test radio connection');assert.ok($('radioTestBanner').classList.contains('hidden'));
  $('radioTestToggle').click();await flush();
  assert.equal($('radioTestToggle').textContent,'Stop test radio connection');assert.equal($('radioTestToggle').getAttribute('aria-pressed'),'true');
  assert.ok($('radioTestToggle').classList.contains('radio-testing'));assert.ok($('radioTestBanner').classList.contains('radio-test-banner'));
  assert.ok(!$('radioTestBanner').classList.contains('hidden'));assert.match($('radioTestBanner').textContent,/Radio connection test in progress.*Connecting/);
  assert.equal($('radioTestBanner').getAttribute('role'),'status');assert.equal($('trackingToggle').disabled,true);
  assert.equal(server.hardware.enabled,false);assert.equal(server.paused,true);
  server.hardware.radio_test_frequency_hz=145555000;server.hardware.radio_rx_hz=145555000;await refresh();
  assert.match($('radioTestBanner').textContent,/145\.555000 MHz verified/);assert.match($('radioTestBanner').textContent,/every 2 seconds/);
  $('radioTestToggle').click();await flush();
  assert.equal($('radioTestToggle').textContent,'Test radio connection');assert.equal($('radioTestToggle').getAttribute('aria-pressed'),'false');
  assert.ok($('radioTestBanner').classList.contains('hidden'));assert.equal($('trackingToggle').disabled,false);assert.equal(server.hardware.enabled,false);
  assert.deepEqual(requests.filter(row=>row.url==='/api/radio-test').map(row=>row.body),[{enabled:true},{enabled:false}]);
});

test('missing radio port reports the error without displaying false test progress',async t=>{
  const { $,server,refresh }=await dashboard(t);server.settings.radio_port='';await refresh();
  $('radioTestToggle').click();await flush();assert.match($('toast').textContent,/available radio serial port/);
  assert.equal($('radioTestToggle').textContent,'Test radio connection');assert.equal($('radioTestToggle').disabled,false);
  assert.ok($('radioTestBanner').classList.contains('hidden'));
});

test('radio test failures clear the progress label and restore controls while displaying the serial error',async t=>{
  const { $,server,refresh }=await dashboard(t);server.hardware.radio_test_enabled=true;await refresh();
  assert.ok(!$('radioTestBanner').classList.contains('hidden'));
  Object.assign(server.hardware,{radio_test_enabled:false,radio_test_frequency_hz:null,error:'IC-9700 did not acknowledge/respond.',radio_status:'Stopped after error'});await refresh();
  assert.ok($('radioTestBanner').classList.contains('hidden'));assert.equal($('radioTestToggle').textContent,'Test radio connection');
  assert.equal($('trackingToggle').disabled,false);assert.match($('equipmentStatus').textContent,/did not acknowledge/);
});

test('Windows notifications accompany the in-app banner; closing them leaves an acknowledgement available on Settings',async t=>{
  const { $,w,server,refresh }=await dashboard(t),notifications=[];
  w.Notification=class {static permission='granted';constructor(title,options){this.title=title;this.options=options;notifications.push(this);}close(){this.closed=true;}};
  w.document.querySelector('.nav-item[data-view="settings"]').click();
  server.alerts=[{id:'real-notification',name:'SO-50',test:false,active:true,acknowledged:false,created_at:server.now,message:'SO-50 pass approaching'}];await refresh();
  assert.equal(notifications.length,1);assert.ok(!$('alertBanner').classList.contains('hidden'));
  assert.equal($('alertBanner').closest('.view'),null);assert.ok(!$('view-settings').classList.contains('hidden'));
  notifications[0].close();await refresh();assert.ok(!$('alertBanner').classList.contains('hidden'));
  $('acknowledge').click();await flush();assert.equal(server.alerts[0].acknowledged,true);assert.ok($('alertBanner').classList.contains('hidden'));
});

test('expired real alerts stay visible for acknowledgement without playing sound or repeating Windows notifications',async t=>{
  const { $,w,server,refresh }=await dashboard(t),notifications=[];
  w.Notification=class {static permission='granted';constructor(){notifications.push(this);}close(){}};
  const sounds=fakeAudio(w);$('enableSound').click();await flush();sounds.tones.length=0;
  server.alerts=[{id:'expired',name:'ISS',test:false,active:false,acknowledged:false,created_at:server.now,message:'ISS pass'}];server.sound_serial++;await refresh();
  assert.ok(!$('alertBanner').classList.contains('hidden'));assert.match($('alertTitle').textContent,/awaiting acknowledgement/);
  assert.equal(sounds.tones.length,0);assert.equal(notifications.length,0);
  server.paused=true;await refresh();assert.ok(!$('alertBanner').classList.contains('hidden'));
  $('acknowledge').click();await flush();assert.ok($('alertBanner').classList.contains('hidden'));
});

test('history acknowledges a specific alert and Windows notification click opens its in-app controls',async t=>{
  const { $,w,server,refresh,requests }=await dashboard(t),notifications=[];let focused=false;
  w.focus=()=>{focused=true;};w.Notification=class {static permission='granted';constructor(){notifications.push(this);}close(){}};
  server.alerts=[{id:'current',name:'ISS',test:false,active:true,acknowledged:false,created_at:server.now,message:'ISS pass'},
    {id:'ended-test',name:'TEST',test:true,active:false,acknowledged:false,created_at:server.now,message:'TEST ALERT'}];await refresh();
  w.document.querySelector('.nav-item[data-view="history"]').click();notifications[0].onclick();
  assert.equal(focused,true);assert.ok(!$('view-dashboard').classList.contains('hidden'));
  w.document.querySelector('.nav-item[data-view="history"]').click();$('history').querySelector('[data-acknowledge="ended-test"]').click();await flush();
  assert.equal(server.alerts[1].acknowledged,true);assert.equal(server.alerts[0].acknowledged,false);
  assert.deepEqual(requests.find(row=>row.url==='/api/acknowledge').body,{id:'ended-test'});
  assert.equal($('history').querySelectorAll('[data-acknowledge]').length,1);
});

test('rotator C2 errors remain visible while radio tracking and both verified frequencies continue',async t=>{
  const { $,server,refresh }=await dashboard(t);
  Object.assign(server.hardware,{enabled:true,rotator_error:'No valid GS-232B C2 position reply.',rotator_status:'Stopped after error',
    radio_status:'Tracking · frequencies verified',radio_rx_hz:437801000,radio_tx_hz:145989000});await refresh();
  assert.equal($('trackingToggle').textContent,'Stop live tracking');assert.match($('equipmentStatus').textContent,/C2 position reply/);
  assert.match($('equipmentStatus').textContent,/Radio control remains enabled/);assert.match($('equipmentStatus').textContent,/437\.801000 MHz/);
  assert.match($('equipmentStatus').textContent,/145\.989000 MHz/);
});

test('maximum Windows volume is enabled by default and can be disabled without acknowledging an alert',async t=>{
  const { $,w,server,refresh,requests }=await dashboard(t);
  server.alerts=[{id:'pending-volume',name:'ISS',test:false,active:true,acknowledged:false,created_at:server.now,message:'ISS pass'}];await refresh();
  w.document.querySelector('.nav-item[data-view="settings"]').click();
  const option=$('settingsForm').elements.windows_maximum_volume;assert.equal(option.checked,true);
  option.checked=false;$('settingsForm').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await flush();
  assert.equal(requests.filter(row=>row.url==='/api/settings').at(-1).body.windows_maximum_volume,false);
  assert.equal(server.alerts[0].acknowledged,false);assert.ok(!$('alertBanner').classList.contains('hidden'));
  assert.equal($('saveStatus').textContent,'Settings saved.');
});
