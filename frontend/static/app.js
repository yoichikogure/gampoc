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
  if (!el) return;
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

async function loadDetectorMappings() {
  const rows = await api('/api/mappings/detectors');
  renderTable('mappingTable', rows, [
    {key:'intersection_code', label:'INT'}, {key:'approach_no', label:'Approach'},
    {key:'approach_name', label:'Approach name'}, {key:'detector_no', label:'Detector'},
    {key:'lane_label', label:'Lane label'}, {key:'description', label:'Description'}
  ]);
}

async function loadDailySummary() {
  const rows = await api('/api/analytics/daily-summary');
  renderTable('dailySummaryTable', rows, [
    {key:'day', label:'Day'}, {key:'approach_no', label:'Approach'}, {key:'approach_name', label:'Approach name'},
    {key:'total_count', label:'Total count'}, {key:'avg_15min_count', label:'Avg / 15 min'},
    {key:'max_15min_count', label:'Max / 15 min'}, {key:'interval_records', label:'Records'}
  ]);
}

async function loadHourlySummary() {
  const rows = await api('/api/analytics/hourly-summary');
  renderTable('hourlySummaryTable', rows.slice(0, 200), [
    {key:'hour_start', label:'Hour'}, {key:'approach_no', label:'Approach'},
    {key:'total_count', label:'Total count'}, {key:'avg_15min_count', label:'Avg / 15 min'}, {key:'interval_records', label:'Records'}
  ]);
}

async function loadPeakSummary() {
  const rows = await api('/api/analytics/peak-summary');
  renderTable('peakSummaryTable', rows, [
    {key:'approach_no', label:'Approach'}, {key:'hour_start', label:'Peak hour'}, {key:'hourly_count', label:'Hourly count'}
  ]);
}

async function loadDataQuality() {
  const [missing, anomalies] = await Promise.all([
    api('/api/analytics/missing-intervals'),
    api('/api/analytics/anomalies')
  ]);
  renderTable('missingTable', missing.slice(0, 200), [
    {key:'expected_time', label:'Missing interval'}, {key:'approach_no', label:'Approach'}, {key:'detector_no', label:'Detector'}
  ]);
  renderTable('anomalyTable', anomalies.slice(0, 200), [
    {key:'interval_start', label:'Interval'}, {key:'approach_no', label:'Approach'}, {key:'detector_no', label:'Detector'},
    {key:'vehicle_count', label:'Count'}, {key:'anomaly_type', label:'Issue'}, {key:'detector_average', label:'Avg'}, {key:'detector_stddev', label:'Std dev'}
  ]);
}

async function loadSignalDurations() {
  const rows = await api('/api/analytics/signal-phase-durations');
  renderTable('signalDurationTable', rows, [
    {key:'intersection_code', label:'INT'}, {key:'phase_no', label:'Phase'}, {key:'signal_state', label:'State'},
    {key:'event_count', label:'Events'}, {key:'avg_seconds', label:'Avg sec'}, {key:'min_seconds', label:'Min sec'}, {key:'max_seconds', label:'Max sec'}
  ]);
}

async function refreshPhase2() {
  await Promise.all([loadDetectorMappings(), loadDailySummary(), loadHourlySummary(), loadPeakSummary(), loadDataQuality(), loadSignalDurations()]);
}

async function refreshAll() {
  await Promise.all([loadSummary(), loadDetectorRows(), loadSignalRows(), loadSignalSummary(), loadFiles(), loadDetectorChart(), refreshPhase2()]);
}

upload('detectorForm', '/api/import/detector-log', 'detectorResult');
upload('signalForm', '/api/import/signal-log', 'signalResult');
upload('videoForm', '/api/import/video', 'videoResult');
refreshAll().catch(console.error);
