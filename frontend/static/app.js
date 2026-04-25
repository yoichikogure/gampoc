const api = async (url, options = {}) => {
  const res = await fetch(url, options);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
};

function fmt(x) {
  if (x === null || x === undefined) return "—";
  return String(x);
}

function renderTable(id, rows, columns) {
  const el = document.getElementById(id);
  if (!rows.length) {
    el.innerHTML = "<tr><td>No records yet</td></tr>";
    return;
  }
  el.innerHTML = `<thead><tr>${columns.map(c => `<th>${c.label}</th>`).join("")}</tr></thead>` +
    `<tbody>${rows.map(r => `<tr>${columns.map(c => `<td>${fmt(r[c.key])}</td>`).join("")}</tr>`).join("")}</tbody>`;
}

async function loadSummary() {
  const s = await api('/api/summary');
  const cards = [
    ['Detector count records', s.detector_count_records],
    ['Signal event records', s.signal_event_records],
    ['Video sources', s.video_sources],
    ['Incident events', s.incident_events],
    ['Latest detector interval', s.latest_detector_interval || '—'],
    ['Latest signal event', s.latest_signal_event || '—'],
  ];
  document.getElementById('summaryCards').innerHTML = cards.map(([label, value]) =>
    `<div class="card"><div class="label">${label}</div><div class="value">${value}</div></div>`
  ).join('');
}

async function upload(formId, endpoint, resultId) {
  const form = document.getElementById(formId);
  const out = document.getElementById(resultId);
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    out.textContent = 'Importing...';
    try {
      const body = new FormData(form);
      const data = await api(endpoint, { method: 'POST', body });
      out.textContent = JSON.stringify(data, null, 2);
      await refreshAll();
    } catch (err) {
      out.textContent = err.message;
    }
  });
}

async function loadDetectorRows() {
  const rows = await api('/api/detector-counts?limit=100');
  renderTable('detectorTable', rows, [
    {key:'interval_start', label:'Interval'}, {key:'approach_no', label:'Approach'},
    {key:'detector_no', label:'Detector'}, {key:'vehicle_count', label:'Count'}, {key:'quality_flag', label:'Quality'}
  ]);
}

async function loadSignalRows() {
  const rows = await api('/api/signal-events?limit=100');
  renderTable('signalTable', rows, [
    {key:'event_time', label:'Time'}, {key:'intersection_code', label:'INT'},
    {key:'phase_no', label:'Phase'}, {key:'signal_state', label:'State'}, {key:'raw_line', label:'Raw'}
  ]);
}

async function loadSignalSummary() {
  const rows = await api('/api/signal-phase-summary');
  renderTable('signalSummaryTable', rows, [
    {key:'phase_no', label:'Phase'}, {key:'signal_state', label:'State'}, {key:'event_count', label:'Events'}
  ]);
}

async function loadFiles() {
  const rows = await api('/api/ingestion-files');
  renderTable('filesTable', rows, [
    {key:'id', label:'ID'}, {key:'file_type', label:'Type'}, {key:'original_filename', label:'Filename'},
    {key:'status', label:'Status'}, {key:'records_imported', label:'Records'}, {key:'error_message', label:'Error'}, {key:'uploaded_at', label:'Uploaded'}
  ]);
}

function drawLineChart(canvas, points) {
  const ctx = canvas.getContext('2d');
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0,0,w,h);
  ctx.fillStyle = '#ffffff'; ctx.fillRect(0,0,w,h);
  ctx.strokeStyle = '#e5e7eb'; ctx.lineWidth = 1;
  for (let i=0; i<6; i++) { const y = 30 + i*(h-60)/5; ctx.beginPath(); ctx.moveTo(50,y); ctx.lineTo(w-20,y); ctx.stroke(); }
  if (!points.length) { ctx.fillStyle = '#667085'; ctx.fillText('No detector data yet', 60, h/2); return; }
  const values = points.map(p => p.vehicle_count);
  const min = Math.min(...values), max = Math.max(...values, 1);
  const xFor = i => 50 + i * (w-80) / Math.max(points.length-1, 1);
  const yFor = v => h-35 - ((v-min) / Math.max(max-min, 1)) * (h-70);
  ctx.strokeStyle = '#274472'; ctx.lineWidth = 2;
  ctx.beginPath();
  points.forEach((p,i) => { const x=xFor(i), y=yFor(p.vehicle_count); if(i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y); });
  ctx.stroke();
  ctx.fillStyle = '#344054'; ctx.fillText(`min ${min} / max ${max}`, 56, 20);
}

async function loadDetectorChart() {
  const points = await api('/api/detector-chart');
  drawLineChart(document.getElementById('countCanvas'), points);
}

async function refreshAll() {
  await Promise.all([loadSummary(), loadDetectorRows(), loadSignalRows(), loadSignalSummary(), loadFiles(), loadDetectorChart()]);
}

upload('detectorForm', '/api/import/detector-log', 'detectorResult');
upload('signalForm', '/api/import/signal-log', 'signalResult');
upload('videoForm', '/api/import/video', 'videoResult');
refreshAll().catch(console.error);
