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
  // Equipment fixtures opt in explicitly; manual-mode regression cases retain
  // their existing acknowledgement behavior. Fresh defaults are checked below.
  Object.assign(settings,{rotator_enabled:true,radio_enabled:true,auto_acknowledge_enabled:false});
  settings.satellite_notifications_paused=Object.fromEntries(settings.satellites.map(name=>[name,false]));
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
    hardware:{enabled:false,radio_test_enabled:false,radio_test_frequency_hz:null,rotator_test:null,error:null,rotator_status:'Off',radio_status:'Off',rotator_position:null,
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
      const pauses={...server.settings.satellite_notifications_paused,...body.satellite_notifications_paused};
      const equipmentKeys=['satellites','satellite_profiles','latitude','longitude','observer_height_m','minimum_elevation','rotator_enabled','rotator_port','rotator_baud','rotator_max_azimuth','radio_enabled','radio_port','radio_baud','radio_address','radio_step_hz','tracking_minimum_mode'];
      const equipmentChanged=Object.keys(body).some(key=>equipmentKeys.includes(key) && JSON.stringify(body[key])!==JSON.stringify(server.settings[key]));
      server.settings={...server.settings,...body};server.settings.satellite_notifications_paused=Object.fromEntries(server.settings.satellites.map(name=>[name,Boolean(pauses[name])]));
      if(equipmentChanged)server.hardware.enabled=false;
      server.satellites=server.satellites.filter(row=>server.settings.satellites.includes(row.name));updateTarget();result={ok:true};
    } else if(url==='/api/profile-lookup'){
      result=server.lookupReply?await server.lookupReply(body.name):{status:'not_found',matches:[],message:'No matching frequency profile was found. Enter the downlink, uplink, receive mode and transmit mode manually.'};
    } else if(url==='/api/tracking'){
      if(body.enabled && server.hardware.radio_test_enabled){ok=false;result={error:'Stop the radio connection test before starting live tracking.'};}
      else if(body.enabled && (!server.settings.rotator_port || !server.settings.radio_port)){ok=false;result={error:'Select an available serial port in Settings, then save.'};}
      else{server.hardware.enabled=body.enabled;result={ok:true};}
    } else if(url==='/api/radio-test'){
      if(body.enabled && !ports.some(port=>port.device===server.settings.radio_port)){ok=false;result={error:'Select an available radio serial port in Settings, then save.'};}
      else{server.hardware.enabled=false;server.hardware.radio_test_enabled=body.enabled;server.hardware.radio_test_frequency_hz=null;result={ok:true};}
    } else if(url==='/api/rotator-test'){
      const port=server.settings.rotator_port,available=ports.some(row=>row.device===port);
      const diagnostic=server.next_rotator_test_result || {tested_at:now,port:port || null,baud:server.settings.rotator_baud,
        command_sent:available,response:available?'AZ=350 EL=020\r\n':'',position:available?{azimuth:350,elevation:20}:null,
        status:available?'success':'warning',message:!port?'No rotator port is configured. Select a rotator serial port in Settings, then save.':
          available?'Reported azimuth 350° · elevation 20°.':`Configured rotator port ${port} is unavailable. Connect the interface or choose an available port in Settings.`};
      server.hardware.rotator_test=diagnostic;if(diagnostic.position)server.hardware.rotator_position=diagnostic.position;
      result={ok:true,result:diagnostic};
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

test('watch-list cards show the next pass maximum separately from current elevation and update with the forecast',async t=>{
  const {$,server,refresh}=await dashboard(t);
  const satellite=server.satellites.find(row=>row.name==='ISS');
  satellite.next_pass={alert_at:new Date(Date.now()+3600000).toISOString(),peak_elevation:78.6};
  const metrics=()=>Array.from($('fleet').querySelector('[data-satellite="ISS"]').querySelectorAll('.sat-metrics > div'),element=>[element.querySelector('span').textContent,element.querySelector('strong').textContent]);
  await refresh();
  assert.deepEqual(metrics(),[['ELEVATION','62.0°'],['AZIMUTH','175°'],['NEXT PASS MAX','78.6°']]);
  assert.equal($('fleet').querySelectorAll('.sat-metrics > div:last-child strong').length,7);
  satellite.elevation=25;
  satellite.next_pass.peak_elevation=45.2;
  await refresh();
  assert.deepEqual(metrics(),[['ELEVATION','25.0°'],['AZIMUTH','175°'],['NEXT PASS MAX','45.2°']]);
  satellite.next_pass.peak_elevation=null;
  await refresh();assert.equal(metrics()[2][1],'—');
  satellite.next_pass=null;
  await refresh();assert.equal(metrics()[2][1],'—');
  assert.match($('fleet').querySelector('[data-satellite="ISS"] .sat-bottom').textContent,/No qualifying pass in forecast/);
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

test('per-satellite checkbox saves immediately without stopping equipment or removing the satellite',async t=>{
  const {$,w,server,requests,refresh}=await dashboard(t);server.hardware.enabled=true;await refresh();
  const before=clone(server.settings.satellites),pause=$('profileList').querySelector('[data-pause-satellite="ISS"]');
  assert.equal(pause.checked,false);pause.checked=true;pause.dispatchEvent(new w.Event('change'));await flush();
  assert.equal(server.settings.satellite_notifications_paused.ISS,true);assert.equal(server.hardware.enabled,true);
  assert.deepEqual(server.settings.satellites,before);assert.match($('fleet').textContent,/Notifications paused/);
  assert.deepEqual(requests.find(row=>row.url==='/api/settings').body,{satellite_notifications_paused:{ISS:true}});
  const resume=$('profileList').querySelector('[data-pause-satellite="ISS"]');resume.checked=false;resume.dispatchEvent(new w.Event('change'));await flush();
  assert.equal(server.settings.satellite_notifications_paused.ISS,false);assert.equal(server.hardware.enabled,true);
});

test('new satellites default to notifications on and editing or renaming keeps their selected pause',async t=>{
  const {$,server}=await dashboard(t);$('addSatellite').click();assert.equal($('profilePause').checked,false);
  $('profileName').value='12345';$('saveSatellite').click();await flush();assert.equal(server.settings.satellite_notifications_paused['12345'],false);
  $('profileList').querySelector('[data-edit="12345"]').click();$('profilePause').checked=true;$('saveSatellite').click();await flush();
  $('profileList').querySelector('[data-edit="12345"]').click();assert.equal($('profilePause').checked,true);
  $('profileName').value='12346';$('saveSatellite').click();await flush();assert.equal(server.settings.satellite_notifications_paused['12346'],true);
  assert.equal(server.settings.satellite_notifications_paused['12345'],undefined);
  $('profileList').querySelector('[data-delete="12346"]').click();await flush();assert.equal(server.settings.satellite_notifications_paused['12346'],undefined);
});

function lookupMatch(label='AO-91',rx=145.960,tx=435.250,rxMode='FM',txMode='FM'){
  return {label,profile:{downlink_mhz:rx,uplink_mhz:tx,downlink_mode:rxMode,uplink_mode:txMode,notes:'Example lookup note.',source:'https://www.amsat.org/live-fm-satellites/'}};
}

test('new satellite name looks up all radio fields and saves editable values with source attribution',async t=>{
  const {$,w,server,requests}=await dashboard(t);server.lookupReply=async()=>({status:'found',matches:[lookupMatch()],message:'Found in AMSAT.'});
  $('addSatellite').click();$('profileName').value='AO-91';$('profileName').dispatchEvent(new w.Event('change'));await flush();
  assert.equal($('profileRx').value,'145.96');assert.equal($('profileTx').value,'435.25');
  assert.equal($('profileRxMode').value,'FM');assert.equal($('profileTxMode').value,'FM');
  assert.match($('profileLookupStatus').textContent,/Review/);assert.equal($('profileLookupSource').querySelector('a').href,'https://www.amsat.org/live-fm-satellites/');
  $('profileRx').value='145.961';$('profileRx').dispatchEvent(new w.Event('input'));$('saveSatellite').click();await flush();
  assert.equal(server.settings.satellite_profiles['AO-91'].downlink_mhz,145.961);
  assert.equal(server.settings.satellite_profiles['AO-91'].source,'https://www.amsat.org/live-fm-satellites/');
  assert.equal(server.settings.satellite_notifications_paused['AO-91'],false);
  assert.equal(requests.filter(row=>row.url==='/api/profile-lookup').length,1);
});

test('no match explains manual entry and changing the name clears earlier automatic values',async t=>{
  const {$,w,server}=await dashboard(t);server.lookupReply=async name=>name==='AO-91'?{status:'found',matches:[lookupMatch()],message:'Found.'}:
    {status:'not_found',matches:[],message:'No match was found. Enter the downlink, uplink, receive mode and transmit mode manually.'};
  $('addSatellite').click();$('profileName').value='AO-91';$('profileName').dispatchEvent(new w.Event('change'));await flush();
  $('profileName').value='UNKNOWN';$('profileName').dispatchEvent(new w.Event('input'));$('profileName').dispatchEvent(new w.Event('change'));await flush();
  assert.equal($('profileRx').value,'');assert.equal($('profileTx').value,'');assert.match($('profileLookupStatus').textContent,/No match.*manually/);
  $('profileRx').value='145.8';$('profileRx').dispatchEvent(new w.Event('input'));$('saveSatellite').click();await flush();
  assert.equal(server.settings.satellite_profiles.UNKNOWN.downlink_mhz,145.8);
});

test('multiple operating profiles require a choice before applying frequencies',async t=>{
  const {$,w,server}=await dashboard(t);server.lookupReply=async()=>({status:'found',matches:[lookupMatch('AO-7 Mode A',29.45,145.9,'USB','USB'),lookupMatch('AO-7 Mode B',145.95,432.15,'USB','LSB')],message:'Choose an operating profile.'});
  $('addSatellite').click();$('profileName').value='AO-7';$('profileName').dispatchEvent(new w.Event('change'));await flush();
  assert.ok(!$('profileLookupChoices').classList.contains('hidden'));assert.equal($('profileRx').value,'');
  $('profileLookupChoice').value='1';$('profileLookupChoice').dispatchEvent(new w.Event('change'));
  assert.equal($('profileRx').value,'145.95');assert.equal($('profileTx').value,'432.15');assert.equal($('profileTxMode').value,'LSB');
});

test('late lookups preserve manual input and cannot overwrite a different name or reopened editor',async t=>{
  const {$,w,server}=await dashboard(t);let finish;
  server.lookupReply=()=>new Promise(resolve=>{finish=resolve;});
  $('addSatellite').click();$('profileName').value='AO-91';$('profileName').dispatchEvent(new w.Event('change'));await flush();
  $('profileRx').value='145.123';$('profileRx').dispatchEvent(new w.Event('input'));
  finish({status:'found',matches:[lookupMatch()],message:'Found.'});await flush();
  assert.equal($('profileRx').value,'145.123');assert.equal($('profileTx').value,'435.25');assert.match($('profileLookupStatus').textContent,/manually.*kept/);
  $('lookupSatellite').click();await flush();const oldFinish=finish;
  $('cancelSatellite').click();$('addSatellite').click();$('profileName').value='OTHER';
  oldFinish({status:'found',matches:[lookupMatch()],message:'Old result.'});await flush();assert.equal($('profileRx').value,'');assert.equal($('profileTx').value,'');
});

test('a failed lookup explains manual entry and the retry button can recover',async t=>{
  const {$,w,server}=await dashboard(t);server.lookupReply=async()=>{throw new Error('Offline');};
  $('addSatellite').click();$('profileName').value='AO-91';$('profileName').dispatchEvent(new w.Event('change'));await flush();
  assert.match($('profileLookupStatus').textContent,/unavailable.*manually/);
  server.lookupReply=async()=>({status:'found',matches:[lookupMatch()],message:'Found.'});$('lookupSatellite').click();await flush();assert.equal($('profileRx').value,'145.96');
});

test('a paused satellite creates no browser notification or acknowledgement banner',async t=>{
  const {$,w,server,refresh}=await dashboard(t);const notifications=[];
  w.Notification=class {static permission='granted';constructor(title){notifications.push(title);}};
  server.settings.satellite_notifications_paused.ISS=true;
  server.alerts=[{id:'paused-pass',name:'ISS',test:false,active:true,acknowledged:false,message:'Example pass',created_at:server.now}];await refresh();
  assert.equal(notifications.length,0);assert.ok($('alertBanner').classList.contains('hidden'));
  server.settings.satellite_notifications_paused.ISS=false;await refresh();assert.equal(notifications.length,1);assert.ok(!$('alertBanner').classList.contains('hidden'));
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

test('rotator test displays raw C2 response, port, baud and test time without changing live tracking',async t=>{
  const {$,w,server,refresh,requests}=await dashboard(t);server.settings.rotator_port='COM10';server.settings.rotator_baud=4800;server.hardware.enabled=true;await refresh();
  assert.equal($('rotatorTest').textContent,'Test rotator connection');assert.ok($('rotatorTestResult').classList.contains('hidden'));
  $('rotatorTest').click();await flush();
  assert.equal(requests.filter(row=>row.url==='/api/rotator-test').length,1);
  assert.ok(!$('rotatorTestResult').classList.contains('hidden'));assert.match($('rotatorTestTitle').textContent,/succeeded/);
  assert.match($('rotatorTestDetails').textContent,/COM10.*4800 baud.*C2/);
  assert.match($('rotatorTestResponse').textContent,/AZ=350 EL=020\\r\\n/);
  assert.equal($('rotatorTestTime').dateTime,server.hardware.rotator_test.tested_at);
  assert.match($('rotatorTestTime').textContent,/Test time:.*\d\d:\d\d:\d\d.*local time/);
  assert.equal($('rotatorTestResult').getAttribute('role'),'status');assert.equal(server.hardware.enabled,true);
  assert.match($('equipmentStatus').textContent,/Reported: az 350° · el 20°/);
  await refresh();assert.match($('rotatorTestResponse').textContent,/AZ=350 EL=020/);
  $('timeZone').value='UTC';$('timeZone').dispatchEvent(new w.Event('change'));await flush();
  assert.match($('rotatorTestTime').textContent,/UTC/);
});

test('missing and unavailable rotator ports leave a visible warning with no command sent',async t=>{
  const {$,server,refresh}=await dashboard(t);
  for(const port of ['', 'COM99']){
    server.settings.rotator_port=port;await refresh();$('rotatorTest').click();await flush();
    assert.ok(!$('rotatorTestResult').classList.contains('hidden'));assert.ok($('rotatorTestResult').classList.contains('test-warning'));
    assert.match($('rotatorTestMessage').textContent,port?/COM99 is unavailable/:/No rotator port is configured/);
    assert.match($('rotatorTestResponse').textContent,/No command sent/);
    assert.equal($('rotatorTest').disabled,false);assert.equal($('rotatorTest').textContent,'Test rotator connection');
  }
});

test('failed C2 test preserves malformed text safely and distinguishes no response',async t=>{
  const {$,server,refresh}=await dashboard(t);server.hardware.radio_test_enabled=true;await refresh();
  for(const response of ['<img src=x onerror="bad()">\r\n','']){
    server.next_rotator_test_result={tested_at:server.now,port:'COM10',baud:9600,status:'error',command_sent:true,response,position:null,
      message:response?'Rotator did not return an azimuth/elevation reply to C2.':'No response to C2 within 0.8 seconds.'};
    $('rotatorTest').click();await flush();
    assert.ok($('rotatorTestResult').classList.contains('test-error'));
    assert.equal($('rotatorTestResponse').querySelector('img'),null);
    assert.ok($('rotatorTestResponse').textContent.includes(response?'<img src=x onerror="bad()">':'No response received.'));
    assert.equal(server.hardware.radio_test_enabled,true);assert.ok(!$('radioTestBanner').classList.contains('hidden'));
  }
});

test('rotator test button stays disabled while waiting and accepts another test after completion',async t=>{
  const {$,w,server,refresh,requests}=await dashboard(t);server.settings.rotator_port='COM10';await refresh();
  const fetch=w.fetch;let release;
  w.fetch=async(url,options)=>{if(url==='/api/rotator-test')await new Promise(resolve=>{release=resolve;});return fetch(url,options);};
  $('rotatorTest').click();await flush();assert.equal($('rotatorTest').disabled,true);assert.equal($('rotatorTest').textContent,'Testing rotator connection…');
  $('rotatorTest').click();await refresh();assert.equal($('rotatorTest').disabled,true);release();await flush();
  assert.equal(requests.filter(row=>row.url==='/api/rotator-test').length,1);assert.equal($('rotatorTest').disabled,false);
  w.fetch=fetch;$('rotatorTest').click();await flush();assert.equal(requests.filter(row=>row.url==='/api/rotator-test').length,2);
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

test('calculated pass frequencies are labelled and a CI-V failure is prominent without claiming radio readback',async t=>{
  const {$,server,refresh}=await dashboard(t);
  assert.match($('liveTarget').textContent,/Calculated downlink \/ RX/);assert.match($('liveTarget').textContent,/Calculated uplink \/ TX/);
  assert.match($('trackingNotice').textContent,/Live tracking is off.*calculated only.*Start live tracking/);
  assert.match($('equipmentStatus').textContent,/No radio readback yet/);
  server.hardware.error='IC-9700 rejected command 05 (FA). Check satellite/VFO mode and supported frequency.';
  server.satellites.find(row=>row.name==='ISS').doppler.rx_hz+=300;await refresh();
  assert.ok(!$('trackingNotice').classList.contains('hidden'));assert.match($('trackingNotice').textContent,/Live control stopped.*command 05 \(FA\)/);
  assert.match($('liveTarget').textContent,/437.801300 MHz/);assert.match($('equipmentStatus').textContent,/No radio readback yet/);
  Object.assign(server.hardware,{error:null,enabled:true,radio_status:'Tracking · frequencies verified',radio_rx_hz:437801300,radio_tx_hz:145989000,
    radio_verified_at:server.now,radio_verified_target:'ISS',frequency_updates:4});await refresh();
  assert.ok($('trackingNotice').classList.contains('hidden'));assert.match($('equipmentStatus').textContent,/Last radio readback:.*ISS/);
  assert.match($('equipmentStatus').textContent,/Pass frequency updates: 4/);
  server.settings.radio_enabled=false;await refresh();assert.match($('trackingNotice').textContent,/Radio adjustments are disabled/);
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

test('fresh defaults select one-minute automatic acknowledgement and notification-only equipment',async t=>{
  const defaults=JSON.parse(fs.readFileSync(path.join(root,'settings.example.json'),'utf8'));
  const {$}=await dashboard(t,{settings:defaults}),fields=$('settingsForm').elements;
  assert.equal(fields.auto_acknowledge_enabled.checked,true);assert.equal(fields.auto_acknowledge_seconds.value,'60');
  assert.equal(fields.windows_volume_percent.value,'100');assert.equal(fields.windows_maximum_volume.checked,true);
  assert.equal(fields.rotator_enabled.checked,false);assert.equal(fields.radio_enabled.checked,false);
  assert.equal($('trackingToggle').disabled,true);assert.match($('trackingNotice').textContent,/Notification-only mode/);
  assert.ok($('radioTestToggle').classList.contains('hidden'));assert.ok($('rotatorTest').classList.contains('hidden'));
  assert.match($('fleet').textContent,/ISS/);assert.match($('liveTarget').textContent,/ISS/);
});

test('home connection-test buttons independently follow saved radio and rotator enable choices',async t=>{
  const {$,w,server,refresh}=await dashboard(t),fields=$('settingsForm').elements;
  for(const [rotator,radio] of [[false,false],[true,false],[false,true],[true,true]]){
    fields.rotator_enabled.checked=rotator;fields.radio_enabled.checked=radio;
    $('settingsForm').dispatchEvent(new w.Event('submit',{cancelable:true}));await flush();
    assert.equal(server.settings.rotator_enabled,rotator);assert.equal(server.settings.radio_enabled,radio);
    assert.equal($('rotatorTest').classList.contains('hidden'),!rotator);
    assert.equal($('radioTestToggle').classList.contains('hidden'),!radio);
    assert.ok(!$('testAlert').classList.contains('hidden'));
  }
  server.volume_status='Previous PC volume 20% and muted state restored.';await refresh();
  assert.match($('soundStatus').textContent,/Previous PC volume 20% and muted state restored/);
});

test('both satellite pause checkboxes use the requested wording',async t=>{
  const {$}=await dashboard(t);
  const wording='Pause notifications for this satellite until you uncheck this';
  const checkbox=$('profileList').querySelector('[data-pause-satellite="ISS"]');
  assert.equal(checkbox.parentElement.textContent.trim(),wording);
  $('addSatellite').click();assert.equal($('profilePause').parentElement.textContent.trim(),wording);
});

test('alarm duration and chosen volume save without acknowledging alerts or stopping live equipment',async t=>{
  const {$,w,server,refresh,requests}=await dashboard(t),fields=$('settingsForm').elements;
  server.alerts=[{id:'pending-alarm',name:'ISS',test:false,active:true,acknowledged:false,created_at:server.now,message:'Example pass'}];
  server.hardware.enabled=true;await refresh();
  fields.auto_acknowledge_enabled.checked=true;fields.auto_acknowledge_enabled.dispatchEvent(new w.Event('change'));
  assert.equal(fields.auto_acknowledge_seconds.disabled,false);
  fields.auto_acknowledge_seconds.value='120';fields.windows_volume_percent.value='45';
  $('settingsForm').dispatchEvent(new w.Event('submit',{cancelable:true}));await flush();
  const saved=requests.filter(row=>row.url==='/api/settings').at(-1).body;
  assert.equal(saved.auto_acknowledge_enabled,true);assert.equal(saved.auto_acknowledge_seconds,120);assert.equal(saved.windows_volume_percent,45);
  assert.equal(server.alerts[0].acknowledged,false);assert.equal(server.hardware.enabled,true);assert.equal($('saveStatus').textContent,'Settings saved.');
});

test('manual acknowledgement and current-volume options disable their numeric controls',async t=>{
  const {$,w,server}=await dashboard(t),fields=$('settingsForm').elements;
  fields.auto_acknowledge_enabled.checked=false;fields.auto_acknowledge_enabled.dispatchEvent(new w.Event('change'));
  fields.windows_maximum_volume.checked=false;fields.windows_maximum_volume.dispatchEvent(new w.Event('change'));
  assert.equal(fields.auto_acknowledge_seconds.disabled,true);assert.equal(fields.windows_volume_percent.disabled,true);
  $('settingsForm').dispatchEvent(new w.Event('submit',{cancelable:true}));await flush();
  assert.equal(server.settings.auto_acknowledge_enabled,false);assert.equal(server.settings.windows_maximum_volume,false);
  assert.equal(server.settings.auto_acknowledge_seconds,60);assert.equal(server.settings.windows_volume_percent,100);
});

test('automatic acknowledgement removes the alarm, closes browser notification and stays in history',async t=>{
  const {$,w,server,refresh}=await dashboard(t);let closed=0;
  w.Notification=class {static permission='granted';constructor(){}close(){closed++;}};
  server.settings.auto_acknowledge_enabled=true;
  server.alerts=[{id:'auto-alarm',name:'ISS',test:false,active:true,acknowledged:false,created_at:server.now,
    auto_acknowledge_at:new Date(Date.now()+60000).toISOString(),message:'Example pass'}];await refresh();
  assert.match($('autoAcknowledgeStatus').textContent,/Automatically acknowledges at/);
  server.alerts[0].acknowledged=true;server.alerts[0].active=false;server.alerts[0].acknowledgement_source='automatic';await refresh();
  assert.equal(closed,1);assert.ok($('alertBanner').classList.contains('hidden'));assert.match($('history').textContent,/AUTO ACKNOWLEDGED/);
});
