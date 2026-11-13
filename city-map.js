import { a as maplibre } from './assets/maplibre/maplibre-gl-CnVrYLhK.js';

const slugs = ['delhi','gurugram','mumbai','pune'];
const city = location.pathname.split('/').find((part) => slugs.includes(part));
const esc = (v) => String(v ?? '').replace(/[&<>"']/g,(c)=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const layer = 'city-ward-fills';
function pointLabelData(geojson) {
  return { type:'FeatureCollection', features:geojson.features.map((feature) => {
    const points=[];
    const walk=(item)=>{ if(!Array.isArray(item)) return; if(typeof item[0]==='number') points.push(item); else item.forEach(walk); };
    walk(feature.geometry.coordinates);
    const lng=points.reduce((sum,p)=>sum+p[0],0)/Math.max(1,points.length),lat=points.reduce((sum,p)=>sum+p[1],0)/Math.max(1,points.length);
    return {type:'Feature',properties:{number:feature.properties.ward_number},geometry:{type:'Point',coordinates:[lng,lat]}};
  })};
}
function extent(coords, box=[[Infinity,Infinity],[-Infinity,-Infinity]]) {
  if(!Array.isArray(coords)) return box;
  if(typeof coords[0]==='number'){box[0][0]=Math.min(box[0][0],coords[0]);box[0][1]=Math.min(box[0][1],coords[1]);box[1][0]=Math.max(box[1][0],coords[0]);box[1][1]=Math.max(box[1][1],coords[1]);}
  else coords.forEach((item)=>extent(item,box));
  return box;
}
function pointInRing(point, ring) {
  let inside=false;
  for(let i=0,j=ring.length-1;i<ring.length;j=i++) {
    const [xi,yi]=ring[i], [xj,yj]=ring[j];
    const crosses=((yi>point[1])!==(yj>point[1])) && point[0]<(xj-xi)*(point[1]-yi)/(yj-yi||Number.EPSILON)+xi;
    if(crosses)inside=!inside;
  }
  return inside;
}
function containsPoint(geometry, point) {
  const polygons=geometry.type==='Polygon'?[geometry.coordinates]:geometry.type==='MultiPolygon'?geometry.coordinates:[];
  return polygons.some((polygon)=>polygon.length && pointInRing(point,polygon[0]) && !polygon.slice(1).some((hole)=>pointInRing(point,hole)));
}
async function init() {
  const mapElement=document.querySelector('.ward-map-frame > div');
  if(!mapElement||!city) return;
  mapElement.id='ward-map'; mapElement.setAttribute('role','application'); mapElement.setAttribute('aria-label',`Interactive ward map of ${city}`);
  const [cityRows,geojson,wardPayload]=await Promise.all([
    fetch('/api/v1/cities').then((r)=>r.json()),
    fetch(`/api/v1/${city}/wards/geojson`).then((r)=>{if(!r.ok)throw Error(`Ward geometry HTTP ${r.status}`);return r.json();}),
    fetch(`/api/v1/${city}/wards`).then((r)=>r.json())
  ]);
  const cityMeta=(Array.isArray(cityRows)?cityRows:cityRows.items).find((item)=>item.slug===city);
  const wards=Array.isArray(wardPayload)?wardPayload:wardPayload.items;
  if(!cityMeta||!geojson.features?.length) throw Error('This city has no published ward boundaries.');
  const dark=document.documentElement.classList.contains('dark');
  const map=new maplibre.Map({container:mapElement,style:`https://tiles.openfreemap.org/styles/${dark?'dark':'positron'}`,center:[cityMeta.centre.lng,cityMeta.centre.lat],zoom:cityMeta.default_zoom,attributionControl:false,minZoom:7,maxZoom:18});
  map.addControl(new maplibre.NavigationControl({visualizePitch:false}),'bottom-right');
  map.addControl(new maplibre.ScaleControl({maxWidth:110,unit:'metric'}),'bottom-left');
  map.addControl(new maplibre.AttributionControl({compact:true}),'bottom-right');
  let active=null;
  let detailLoad=0;
  let directoryCache=null;
  const placeBesideSearch=(node)=>{
    const column=document.querySelector('.ward-controls-column');
    if(!column)return;
    const search=column.firstElementChild;
    if(search)search.insertAdjacentElement('afterend',node);else column.append(node);
  };
  if(!document.querySelector('link[data-ward-details-style]')){
    const stylesheet=document.createElement('link');
    stylesheet.rel='stylesheet';
    stylesheet.href='../assets/ward-details.css?v=6';
    stylesheet.dataset.wardDetailsStyle='true';
    document.head.append(stylesheet);
  }
  const money=(value)=>{
    if(value===null||value===undefined||value==='')return 'Not published';
    const amount=Number(value);if(!Number.isFinite(amount))return String(value);
    return amount>=10000000?`₹ ${new Intl.NumberFormat('en-IN',{maximumFractionDigits:1}).format(amount/10000000)} Cr`:amount>=100000?`₹ ${new Intl.NumberFormat('en-IN',{maximumFractionDigits:1}).format(amount/100000)} L`:`₹ ${new Intl.NumberFormat('en-IN').format(amount)}`;
  };
  const ensureWardPanel=()=>{
    let panel=document.querySelector('#selected-ward-details');
    if(!panel){panel=document.createElement('section');panel.id='selected-ward-details';panel.className='ward-selection-details';panel.setAttribute('aria-live','polite');panel.setAttribute('aria-busy','false');panel.hidden=true;placeBesideSearch(panel);if(!panel.isConnected)mapElement.parentElement.parentElement.append(panel);}
    return panel;
  };
  const showMapStatus=(message)=>{
    const column=document.querySelector('.ward-controls-column');if(!column)return;
    let status=column.querySelector('#city-map-status');
    if(!status){status=document.createElement('p');status.id='city-map-status';status.className='rounded-lg border border-border bg-card p-3 text-sm text-muted-foreground';status.setAttribute('role','status');placeBesideSearch(status);}
    status.textContent=message;
  };
  async function loadWardDetails(ward, wardId){
    const requestId=++detailLoad;
    const panel=ensureWardPanel();panel.hidden=false;panel.setAttribute('aria-busy','true');
    panel.innerHTML=`<header class="ward-selection-heading"><div><p class="home-kicker">${esc(ward?.corporation||ward?.municipal_body||ward?.zone||city.toUpperCase())} · WARD ${esc(ward?.ward_number??'')}</p><h2>${esc(ward?.name||`Ward ${ward?.ward_number||''}`).toLocaleUpperCase()}</h2>${ward?.name_local?`<p class="local-name">${esc(ward.name_local)}</p>`:''}</div><button class="ward-clear" type="button" data-clear-ward>Clear this area</button></header><p class="ward-loading">Loading published ward records…</p>`;
    const get=async(path)=>{const response=await fetch(`/api/v1/${city}/${path}`,{headers:{Accept:'application/json'},cache:'no-store'});if(!response.ok)throw Error(`HTTP ${response.status}`);return response.json();};
    const rows=(payload,keys)=>Array.isArray(payload)?payload:keys.map((key)=>payload?.[key]).find(Array.isArray)||[];
    const loadDirectory=async()=>{
      if(!directoryCache)directoryCache=(async()=>{
        const payload=await get('people?page=1&limit=100');
        const all=rows(payload,['officials','items','people','data','results']);
        const total=Number(payload?.total??payload?.pagination?.total??all.length);
        const pageSize=Number(payload?.per_page??payload?.limit??100)||100;
        const pageCount=Math.min(20,Number(payload?.pages??payload?.pagination?.pages??Math.ceil(total/pageSize))||1);
        if(pageCount>1){
          const next=await Promise.all(Array.from({length:pageCount-1},(_,i)=>get(`people?page=${i+2}&limit=${pageSize}`)));
          for(const page of next)all.push(...rows(page,['officials','items','people','data','results']));
        }
        return {payload,officials:all};
      })().catch((error)=>{directoryCache=null;throw error;});
      return directoryCache;
    };
    const [directoryResult,worksResult]=await Promise.allSettled([loadDirectory(),get(`works?ward_id=${encodeURIComponent(wardId)}&limit=5`)]);
    const directory=directoryResult.status==='fulfilled'?directoryResult.value:null;
    const directoryPayload=directory?.payload;
    const officials=directory?.officials||[];
    const worksPayload=worksResult.status==='fulfilled'?worksResult.value:null;
    const workRows=rows(worksPayload,['items','projects','works','data','results']);
    if(requestId!==detailLoad)return;
    const matchesWard=(person)=>{
      const body=String(ward?.corporation||ward?.municipal_body||'');
      if(!body||person.agency!==body)return false;
      const number=Number(ward?.ward_number);
      return Number(person.ward_number)===number||(Array.isArray(person.jurisdiction?.wards)&&person.jurisdiction.wards.some((value)=>Number(value)===number));
    };
    const linkedOfficials=officials.filter(matchesWard);
    const bodyCode=String(ward?.corporation||ward?.municipal_body||'').toLocaleLowerCase();
    const bodyOfficials=officials.filter((person)=>!bodyCode||String(person.agency||person.corporation||'').toLocaleLowerCase()===bodyCode);
    const commissioners=bodyOfficials.filter((person)=>/commissioner/i.test(`${person.department||''} ${person.title||''} ${person.category||''}`));
    const commissioner=commissioners[0];
    const contacts=rows(directoryPayload,['helplines','contacts']).filter((line)=>!line.corporation||String(line.corporation).toLocaleLowerCase()===bodyCode);
    const councillors=rows(directoryPayload,['councillors','elected_representatives']).filter((person)=>(!person.agency||String(person.agency).toLocaleLowerCase()===bodyCode)&&matchesWard(person));
    const councillorRecords=officials.filter((person)=>/councillor|council member|elected representative/i.test(`${person.department||''} ${person.title||''} ${person.category||''} ${person.level||''}`)&&matchesWard(person));
    const elected=[...councillors,...councillorRecords].filter((person,index,all)=>all.findIndex((item)=>String(item.id??item.name)===String(person.id??person.name))===index);
    const humanize=(key)=>key.replaceAll('_',' ').replace(/\b\w/g,(letter)=>letter.toUpperCase());
    const details=Object.entries(ward||{}).filter(([key,value])=>!['city','ward_id'].includes(key)&&value!==null&&value!==undefined&&value!==''&&!(Array.isArray(value)&&!value.length)).map(([key,value])=>{
      const shown=typeof value==='object'?JSON.stringify(value,null,2):String(value);
      const content=key==='source_url'&&/^https?:\/\//i.test(shown)?`<a href="${esc(shown)}" target="_blank" rel="noopener noreferrer">${esc(shown)} ↗</a>`:esc(shown);
      return `<div class="detail-row"><dt>${esc(humanize(key))}</dt><dd>${content}</dd></div>`;
    }).join('');
    const officialContact=(person)=>`${person.phone?`<a href="tel:${esc(person.phone)}">Call ${esc(person.name||'official')}</a>`:''}${person.phone&&person.email?' · ':''}${person.email?`<a href="mailto:${esc(person.email)}">Email</a>`:''}`;
    const commissionerSection=commissioner?`<p><strong>${esc(commissioner.name||'Name not published')}</strong><br>${esc(commissioner.department||commissioner.title||'Commissioner')} · ${esc(commissioner.agency||ward?.corporation||'')}</p>${commissioner.phone||commissioner.email?`<p>${officialContact(commissioner)}</p>`:''}`:directory?'<p>Commissioner contact details are not listed in the published directory.</p>':'<p>Commissioner information is unavailable from the public directory.</p>';
    const workSection=worksPayload?(workRows.length?workRows.slice(0,5).map((work)=>`<article class="ward-project"><h4>${esc(work.title||work.name||'Work title not published')}</h4><p>${esc(work.executing_agency||work.agency||'Agency not published')} · ${esc(work.division||work.location_text||'Location not published')} · ${esc(work.status||'Status not published')}</p><dl class="detail-list"><div class="detail-row"><dt>Tender reference</dt><dd>${esc(work.tender_ref||'Not published')}</dd></div><div class="detail-row"><dt>Awarded value</dt><dd>${esc(money(work.awarded_budget??work.budget))}</dd></div>${work.contractor_name?`<div class="detail-row"><dt>Contractor</dt><dd>${esc(work.contractor_name)}</dd></div>`:''}<div class="detail-row"><dt>Location</dt><dd>${esc(work.location_text||work.division||'Not published')}</dd></div></dl><details><summary>View more details</summary><p>${esc(work.scope_of_work||work.title||'Scope not published')}</p><p>Inviting authority: ${esc(work.inviting_authority||'Not published')}</p><p>Tender date: ${esc(work.tender_date||'Not published')} · Bid close: ${esc(work.bid_close_date||'Not published')}</p><p>${work.source_url?`<a href="${esc(work.source_url)}" target="_blank" rel="noopener noreferrer">Open official tender source</a>`:'Official source link not published.'}</p></details></article>`).join(''):'<p>No works are explicitly linked to this ward in the public records.</p>'):'<p>Ward-specific works could not be loaded from the public API.</p>';
    const helpSection=contacts.length?`<ul class="dialog-list">${contacts.map((line)=>`<li><strong>${esc(line.name||'Public helpline')}</strong><span>${esc(line.description||'')}${line.phone?` · <a href="tel:${esc(line.phone)}">${esc(line.phone)}</a>`:''}</span></li>`).join('')}</ul>`:directory?'<p>No public helpline is listed for this governing body in the available directory.</p>':'<p>Helpline information is unavailable from the public directory.</p>';
    const officerSection=linkedOfficials.length?`<ul class="dialog-list">${linkedOfficials.slice(0,12).map((person)=>`<li><strong>${esc(person.name||'Name not published')}</strong><span>${esc(person.department||person.title||'Official')}${person.agency?` · ${esc(person.agency)}`:''}${person.phone?` · <a href="tel:${esc(person.phone)}">${esc(person.phone)}</a>`:''}${person.email?` · <a href="mailto:${esc(person.email)}">Email</a>`:''}${person.address?` · ${esc(person.address)}`:''}</span></li>`).join('')}</ul>`:directory?'<p>No ward-assigned officer is listed in the published directory.</p>':'<p>Ward officer information is unavailable from the public directory.</p>';
    const electedSection=elected.length?`<ul class="dialog-list">${elected.map((person)=>`<li><strong>${esc(person.name||'Name not published')}</strong><span>${esc(person.department||person.title||'Elected representative')}${person.agency?` · ${esc(person.agency)}`:''}${officialContact(person)?` · ${officialContact(person)}`:''}</span></li>`).join('')}</ul>`:'<p>No elected councillor is listed for this ward in the published records.</p>';
    panel.innerHTML=`<header class="ward-selection-heading"><div><p class="home-kicker">${esc(ward?.corporation||ward?.municipal_body||ward?.zone||city.toUpperCase())} · WARD ${esc(ward?.ward_number??'')}</p><h2>${esc(ward?.name||`Ward ${ward?.ward_number||''}`).toLocaleUpperCase()}</h2>${ward?.name_local?`<p class="local-name">${esc(ward.name_local)}</p>`:''}</div><button class="ward-clear" type="button" data-clear-ward>Clear this area</button></header><dl class="detail-list">${details}</dl><section class="ward-detail-section"><h3>Commissioner's office</h3><p>Contact the head of ${esc(ward?.corporation||ward?.municipal_body||'this governing body')} for help with this area.</p>${commissionerSection}</section><section class="ward-detail-section"><h3>Work near this governing area</h3><p>Projects explicitly linked to this ward in the public records.</p>${workSection}</section><section class="ward-detail-section"><h3>Get help now</h3><p>Numbers published by bodies covering your area.</p>${helpSection}</section><section class="ward-detail-section"><h3>Officers for your ward</h3><p>Published ward-level contacts responsible for this area.</p>${officerSection}</section><section class="ward-detail-section"><h3>Elected councillor</h3>${electedSection}</section>`;
    panel.querySelector('[data-clear-ward]')?.addEventListener('click',()=>{detailLoad++;panel.hidden=true;if(active!==null){map.removeFeatureState({source:'city-wards',id:active},'active');active=null;}});
    panel.setAttribute('aria-busy','false');
  }
  const selectWard=(feature, fly=false)=>{
    if(!feature)return;
    if(active!==null&&map.getSource('city-wards'))map.removeFeatureState({source:'city-wards',id:active},'active');
    active=Number(feature.properties.ward_id);
    const ward=wards.find((item)=>Number(item.ward_id)===active)||{...feature.properties,ward_id:active};
    if(map.getSource('city-wards'))map.setFeatureState({source:'city-wards',id:active},{active:true});
    document.querySelector('#city-map-status')?.remove();
    loadWardDetails(ward,active).catch((error)=>{const panel=ensureWardPanel();panel.hidden=false;panel.setAttribute('aria-busy','false');panel.innerHTML=`<header class="ward-selection-heading"><div><p class="home-kicker">${esc(ward?.corporation||ward?.zone||city.toUpperCase())}</p><h2>${esc(ward?.name||`Ward ${ward?.ward_number||''}`)}</h2></div><button class="ward-clear" type="button" data-clear-ward>Clear this area</button></header><p>Ward details could not be loaded from the public API. ${esc(error.message)}</p>`;panel.querySelector('[data-clear-ward]')?.addEventListener('click',()=>{panel.hidden=true;});});
    if(fly){const bounds=extent(feature.geometry.coordinates);if(bounds[0][0]!==Infinity)map.fitBounds(bounds,{padding:80,duration:500,maxZoom:14});}
  };
  const apply=()=>{
    const ids=geojson.features.map((f)=>f.properties.ward_id);
    const colors=['#3fc0e5','#40c6a3','#7777ef','#e29a65','#db6597','#dfa733','#61a9e0','#75b958'];
    const fill=['match',['get','ward_id']]; ids.forEach((id,i)=>fill.push(id,colors[i%colors.length]));fill.push('#607080');
    if(!map.getSource('city-wards'))map.addSource('city-wards',{type:'geojson',data:geojson,promoteId:'ward_id'});
    if(!map.getLayer(layer))map.addLayer({id:layer,type:'fill',source:'city-wards',paint:{'fill-color':fill,'fill-opacity':dark?.31:.30,'fill-outline-color':dark?'#d9dee8':'#ffffff'}});
    if(!map.getSource('ward-labels'))map.addSource('ward-labels',{type:'geojson',data:pointLabelData(geojson)});
    if(!map.getLayer('city-ward-labels'))map.addLayer({id:'city-ward-labels',type:'symbol',source:'ward-labels',layout:{'text-field':['to-string',['get','number']],'text-font':['Noto Sans Bold'],'text-size':12,'text-allow-overlap':false,'text-padding':1},paint:{'text-color':dark?'#f4efe3':'#242830','text-halo-color':dark?'#15181d':'#fff','text-halo-width':1.5}});
    if(active!==null)map.setFeatureState({source:'city-wards',id:active},{active:true});
  };
  map.on('load',()=>{apply();const b=cityMeta.bounds;map.fitBounds([[b.min_lng,b.min_lat],[b.max_lng,b.max_lat]],{padding:36,duration:0,maxZoom:12});});
  map.on('click',(event)=>{
    const feature=map.queryRenderedFeatures(event.point,{layers:[layer]})[0];if(!feature)return;
    selectWard(feature);
  });
  window.nagrikWardMap={map,focusWardOpensDetails:true,focusWard(id){const feature=geojson.features.find((item)=>Number(item.properties.ward_id)===Number(id));if(feature)selectWard(feature,true);},openWardById(id){const feature=geojson.features.find((item)=>Number(item.properties.ward_id)===Number(id));if(feature)selectWard(feature);},showCoordinates(){const center=map.getCenter();showMapStatus(`Map center · ${center.lat.toFixed(5)}, ${center.lng.toFixed(5)}`);},locate(button){if(!navigator.geolocation){showMapStatus('Location is not available in this browser.');return;}if(button)button.disabled=true;navigator.geolocation.getCurrentPosition(({coords})=>{const point=[coords.longitude,coords.latitude];const feature=geojson.features.find((item)=>containsPoint(item.geometry,point));map.flyTo({center:point,zoom:feature?14:12});if(feature)selectWard(feature,true);else showMapStatus(`Your location · ${coords.latitude.toFixed(5)}, ${coords.longitude.toFixed(5)}. No mapped ward covers this point.`);if(button)button.disabled=false;},()=>{showMapStatus('Location could not be read. Allow location access in the browser and try again.');if(button)button.disabled=false;},{enableHighAccuracy:true,timeout:12000,maximumAge:60000});}};
  window.addEventListener('nagrik:themechange',(event)=>{const next=event.detail?.dark??document.documentElement.classList.contains('dark');const camera={center:map.getCenter(),zoom:map.getZoom()};map.setStyle(`https://tiles.openfreemap.org/styles/${next?'dark':'positron'}`);map.once('style.load',()=>{document.documentElement.classList.toggle('dark',next);apply();map.jumpTo(camera);});});
}
init().catch((error)=>{console.error('Could not load city ward map:',error);const frame=document.querySelector('.ward-map-frame');if(frame)frame.innerHTML='<div class="h-full rounded-lg border border-rule bg-panel p-4">Ward map could not be loaded. The published ward list is still available.</div>';});
