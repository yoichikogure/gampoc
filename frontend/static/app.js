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

// ---- Phase 3: forecasting and decision-support recommendations ----
async function runForecast(modelName = 'historical_average') {
  const out = document.getElementById('forecastRunResult');
  out.textContent = 'Running forecast...';
  try {
    const data = await api(`/api/forecast/run?model_name=${modelName}&horizons=15,30,60`, { method: 'POST' });
    out.textContent = JSON.stringify(data, null, 2);
    await refreshPhase3();
  } catch (err) {
    out.textContent = err.message;
  }
}

async function generateRecommendations() {
  const out = document.getElementById('recommendationRunResult');
  out.textContent = 'Generating recommendations...';
  try {
    const data = await api('/api/recommendations/generate', { method: 'POST' });
    out.textContent = JSON.stringify(data, null, 2);
    await loadRecommendations();
  } catch (err) {
    out.textContent = err.message;
  }
}

async function loadForecastEvaluation() {
  const rows = await api('/api/forecast/evaluation?model_name=historical_average&horizons=15,30,60');
  renderTable('forecastEvaluationTable', rows, [
    {key:'horizon_minutes', label:'Horizon'}, {key:'model_name', label:'Model'},
    {key:'mae', label:'MAE'}, {key:'rmse', label:'RMSE'}, {key:'mape', label:'MAPE %'},
    {key:'test_points', label:'Test points'}, {key:'notes', label:'Notes'}
  ]);
}

async function loadForecastResults() {
  const rows = await api('/api/forecast/results?limit=200');
  renderTable('forecastResultsTable', rows, [
    {key:'generated_at', label:'Generated'}, {key:'target_time', label:'Target time'},
    {key:'horizon_minutes', label:'Horizon'}, {key:'approach_no', label:'Approach'},
    {key:'model_name', label:'Model'}, {key:'predicted_count', label:'Predicted count'},
    {key:'mae', label:'MAE'}, {key:'rmse', label:'RMSE'}, {key:'mape', label:'MAPE %'}
  ]);
}

async function loadRecommendations() {
  const rows = await api('/api/recommendations?limit=200');
  renderTable('recommendationTable', rows, [
    {key:'generated_at', label:'Generated'}, {key:'target_time', label:'Target time'},
    {key:'phase_no', label:'Phase'}, {key:'approach_no', label:'Approach'},
    {key:'recommendation', label:'Recommendation'}, {key:'reason', label:'Reason'},
    {key:'confidence', label:'Confidence'}, {key:'status', label:'Status'}
  ]);
}

async function refreshPhase3() {
  await Promise.all([loadForecastEvaluation(), loadForecastResults(), loadRecommendations()]);
}

refreshPhase3().catch(console.error);


// ---- Phase 4: video processing and vehicle-candidate detection ----
let selectedVideoId = null;

async function loadVideos() {
  const rows = await api('/api/videos');
  if (rows.length && !selectedVideoId) selectedVideoId = rows[0].id;
  const enriched = rows.map(r => ({
    ...r,
    actions: `<button onclick="sampleFrames(${r.id})">Sample frames</button> <button onclick="detectVehicles(${r.id})">Detect vehicles</button> <button onclick="selectVideo(${r.id})">View</button>`
  }));
  renderTable('videoTable', enriched, [
    {key:'id', label:'ID'}, {key:'camera_code', label:'Camera'}, {key:'source_type', label:'Type'},
    {key:'width', label:'Width'}, {key:'height', label:'Height'}, {key:'fps', label:'FPS'},
    {key:'duration_seconds', label:'Duration sec'}, {key:'frame_count', label:'Frames'},
    {key:'frame_samples', label:'Samples'}, {key:'detection_count', label:'Detections'}, {key:'actions', label:'Actions'}
  ]);
  if (selectedVideoId) await Promise.all([loadVideoFrames(selectedVideoId), loadVehicleDetections(selectedVideoId)]);
  await loadVideoDetectionSummary();
}

async function selectVideo(id) {
  selectedVideoId = id;
  await Promise.all([loadVideoFrames(id), loadVehicleDetections(id)]);
}

async function sampleFrames(id) {
  const out = document.getElementById('videoProcessResult');
  out.textContent = 'Sampling frames...';
  try {
    const data = await api(`/api/videos/${id}/sample-frames?every_seconds=5&max_frames=60`, { method: 'POST' });
    out.textContent = JSON.stringify(data, null, 2);
    selectedVideoId = id;
    await loadVideos();
  } catch (err) { out.textContent = err.message; }
}

async function detectVehicles(id) {
  const out = document.getElementById('videoProcessResult');
  out.textContent = 'Detecting vehicle candidates...';
  try {
    const data = await api(`/api/videos/${id}/detect-vehicles?every_seconds=1&max_frames=300&min_area=700`, { method: 'POST' });
    out.textContent = JSON.stringify(data, null, 2);
    selectedVideoId = id;
    await loadVideos();
  } catch (err) { out.textContent = err.message; }
}

async function loadVideoFrames(id) {
  const rows = await api(`/api/videos/${id}/frames?limit=12`);
  const el = document.getElementById('frameGallery');
  if (!el) return;
  if (!rows.length) { el.innerHTML = '<p class="hint">No sampled frames yet. Click “Sample frames”.</p>'; return; }
  el.innerHTML = rows.map(r => `<figure><img src="${r.image_url}" alt="frame ${r.frame_index}" /><figcaption>${r.frame_time_seconds}s / #${r.frame_index}</figcaption></figure>`).join('');
}

async function loadVehicleDetections(id) {
  const rows = await api(`/api/videos/${id}/detections?limit=200`);
  renderTable('vehicleDetectionTable', rows, [
    {key:'frame_index', label:'Frame'}, {key:'frame_time_seconds', label:'Sec'}, {key:'class_name', label:'Class'},
    {key:'confidence', label:'Confidence'}, {key:'bbox_x', label:'X'}, {key:'bbox_y', label:'Y'},
    {key:'bbox_w', label:'W'}, {key:'bbox_h', label:'H'}, {key:'detection_method', label:'Method'}
  ]);
}

async function loadVideoDetectionSummary() {
  const rows = await api('/api/analytics/video-detection-summary');
  renderTable('videoDetectionSummaryTable', rows, [
    {key:'video_source_id', label:'Video'}, {key:'camera_code', label:'Camera'}, {key:'class_name', label:'Class'},
    {key:'detection_method', label:'Method'}, {key:'detections', label:'Detections'},
    {key:'avg_confidence', label:'Avg confidence'}, {key:'first_second', label:'First sec'}, {key:'last_second', label:'Last sec'}
  ]);
}

loadVideos().catch(console.error);

// ---- Phase 5: incident detection and human review ----
async function detectIncidentsForSelectedVideo() {
  const out = document.getElementById('incidentRunResult');
  if (!selectedVideoId) {
    out.textContent = 'No video selected. Upload/register a video first or click View in the Phase 4 video table.';
    return;
  }
  out.textContent = 'Detecting incident candidates...';
  try {
    const data = await api(`/api/videos/${selectedVideoId}/detect-incidents?congestion_threshold=3&stalled_seconds=8`, { method: 'POST' });
    out.textContent = JSON.stringify(data, null, 2);
    await Promise.all([loadIncidents(), loadIncidentSummary(), loadSummary()]);
  } catch (err) {
    out.textContent = err.message;
  }
}

async function loadIncidentSummary() {
  const rows = await api('/api/analytics/incident-summary');
  renderTable('incidentSummaryTable', rows, [
    {key:'video_source_id', label:'Video'}, {key:'camera_code', label:'Camera'}, {key:'event_type', label:'Event type'},
    {key:'review_status', label:'Review'}, {key:'events', label:'Events'}, {key:'avg_confidence', label:'Avg confidence'},
    {key:'first_second', label:'First sec'}, {key:'last_second', label:'Last sec'}
  ]);
}

async function loadIncidents() {
  const rows = await api('/api/incidents?limit=300');
  const enhanced = rows.map(r => ({
    ...r,
    actions: `<button onclick="showIncidentSnapshot(${r.id})">View</button> <button onclick="reviewIncident(${r.id}, 'confirmed')">Confirm</button> <button onclick="reviewIncident(${r.id}, 'false_positive')">False +</button> <button onclick="reviewIncident(${r.id}, 'uncertain')">Uncertain</button>`
  }));
  renderTable('incidentTable', enhanced, [
    {key:'id', label:'ID'}, {key:'event_type', label:'Type'}, {key:'camera_code', label:'Camera'}, {key:'zone_label', label:'Zone'},
    {key:'confidence', label:'Confidence'}, {key:'queue_length_estimate', label:'Queue est.'}, {key:'review_status', label:'Review'},
    {key:'video_source_id', label:'Video'}, {key:'frame_time_seconds', label:'Sec'}, {key:'notes', label:'Notes'}, {key:'actions', label:'Actions'}
  ]);
}

async function reviewIncident(id, status) {
  await api(`/api/incidents/${id}/review`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ review_status: status })
  });
  await Promise.all([loadIncidents(), loadIncidentSummary()]);
}

function showIncidentSnapshot(id) {
  const el = document.getElementById('incidentSnapshot');
  if (!el) return;
  el.innerHTML = `<img src="/api/incidents/${id}/snapshot" alt="incident ${id}" onerror="this.outerHTML='<p class=&quot;hint&quot;>No snapshot available for this incident.</p>'" /><p class="hint">Incident #${id}</p>`;
}

async function refreshPhase5() {
  await Promise.all([loadIncidents(), loadIncidentSummary()]);
}

refreshPhase5().catch(console.error);
