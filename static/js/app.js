/*
 * Quarterly Earnings Research Dashboard – Client App
 *
 * Communicates with the Python backend via REST API:
 *   POST /api/run          → start full pipeline (optional {ticker})
 *   GET  /api/status       → current pipeline status + results
 *   GET  /api/file/:path   → read a generated file (markdown / json)
 *   GET  /api/ohlc/:ticker → OHLC + stats JSON for charting
 */

// ── State ────────────────────────────────────────────────────────────
let polling = null;
let currentTicker = null;
let currentDates = [];
let loadGeneration = 0;   // increments on each company switch to cancel stale loads
const POLL_MS = 3000;

// Chart state
let ohlcData = null;          // full OHLC JSON from API
let activeWindow = 'inter';   // 'inter' | 'post'

// Content cache — tracks files already loaded to prevent flicker during polling
const _loadedFiles = new Set();

// ── Helpers ──────────────────────────────────────────────────────────
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

function setStatus(state) {
  const dot = $('#statusIndicator');
  dot.className = 'status-dot ' + state;
  dot.title = state.charAt(0).toUpperCase() + state.slice(1);
}

function log(msg, cls = 'log-info') {
  const area = $('#logArea');
  const ts = new Date().toLocaleTimeString();
  const span = document.createElement('span');
  span.className = cls;
  span.textContent = `[${ts}] ${msg}`;
  area.appendChild(span);
  area.appendChild(document.createTextNode('\n'));
  area.scrollTop = area.scrollHeight;
}

function clearLogs() { $('#logArea').innerHTML = ''; }

function showToast(message, duration) {
  duration = duration || 3500;
  let toast = document.getElementById('toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = 'toast';
    toast.className = 'toast';
    document.body.appendChild(toast);
  }
  toast.textContent = message;
  toast.classList.add('show');
  clearTimeout(toast._timer);
  toast._timer = setTimeout(function() { toast.classList.remove('show'); }, duration);
}

function formatTimestamp(isoStr) {
  if (!isoStr) return '';
  try {
    const d = new Date(isoStr);
    return d.toLocaleString(undefined, {
      year: 'numeric', month: 'short', day: 'numeric',
      hour: 'numeric', minute: '2-digit', hour12: true,
    });
  } catch { return isoStr; }
}

// ── Simple Markdown → HTML ───────────────────────────────────────────
function md(text) {
  if (!text) return '<p style="color:var(--c-text-dim)">No data available.</p>';
  let h = text;
  // Fenced code blocks
  h = h.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>');
  // Tables
  h = h.replace(/^(\|.+\|)\n(\|[-| :]+\|)\n((?:\|.+\|\n?)*)/gm, (_, hdr, sep, body) => {
    const thCells = hdr.split('|').filter(c => c.trim()).map(c => `<th>${c.trim()}</th>`).join('');
    const rows = body.trim().split('\n').map(r => {
      const cells = r.split('|').filter(c => c.trim()).map(c => {
        let v = c.trim();
        let cls = '';
        if (/[↑▲]|increase|improved/i.test(v)) cls = ' class="positive"';
        else if (/[↓▼]|decrease|declined/i.test(v)) cls = ' class="negative"';
        return `<td${cls}>${v}</td>`;
      }).join('');
      return `<tr>${cells}</tr>`;
    }).join('');
    return `<table><thead><tr>${thCells}</tr></thead><tbody>${rows}</tbody></table>`;
  });
  // Headers
  h = h.replace(/^#### (.+)$/gm, '<h4>$1</h4>');
  h = h.replace(/^### (.+)$/gm, '<h3>$1</h3>');
  h = h.replace(/^## (.+)$/gm, '<h2>$1</h2>');
  h = h.replace(/^# (.+)$/gm, '<h1>$1</h1>');
  // Bold / italic
  h = h.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  h = h.replace(/\*(.+?)\*/g, '<em>$1</em>');
  // Blockquote
  h = h.replace(/^> (.+)$/gm, '<blockquote>$1</blockquote>');
  // Unordered lists
  h = h.replace(/^- (.+)$/gm, '<li>$1</li>');
  h = h.replace(/((?:<li>.*<\/li>\n?)+)/g, '<ul>$1</ul>');
  // Ordered lists
  h = h.replace(/^\d+\. (.+)$/gm, '<li>$1</li>');
  // Paragraphs
  h = h.replace(/\n{2,}/g, '</p><p>');
  if (!h.startsWith('<')) h = '<p>' + h;
  if (!h.endsWith('>')) h += '</p>';
  // Emoji coloring
  h = h.replace(/🟢/g, '<span style="color:var(--c-accent-pos)">🟢</span>');
  h = h.replace(/🔴/g, '<span style="color:var(--c-accent-neg)">🔴</span>');
  return h;
}

// ── Loading Overlay ──────────────────────────────────────────────
function showLoading(ticker, text) {
  const overlay = $('#loadingOverlay');
  const tickerEl = $('#loadingTicker');
  const textEl = $('#loadingText');
  if (!overlay) return;
  if (tickerEl) tickerEl.textContent = ticker || '';
  if (textEl) textEl.textContent = text || 'Loading analysis…';
  overlay.classList.add('visible');
}

function hideLoading() {
  const overlay = $('#loadingOverlay');
  if (overlay) overlay.classList.remove('visible');
}

// ── Help Modal ──────────────────────────────────────────────────
function toggleHelp() {
  const overlay = $('#helpOverlay');
  if (overlay) overlay.classList.toggle('visible');
}

// ── Tabs ─────────────────────────────────────────────────────────────
function switchTab(name) {
  $$('.tab-btn').forEach(b => b.classList.remove('active'));
  $$('.tab-pane').forEach(p => p.classList.remove('active'));
  $(`#tab-${name}`).classList.add('active');
  const btn = $(`.tab-btn[data-tab="${name}"]`);
  if (btn) btn.classList.add('active');
  // Redraw chart if switching to price tab
  if (name === 'price' && ohlcData) {
    setTimeout(() => drawChart(activeWindow), 50);
  }
}

// ── Pipeline Step UI Updates ─────────────────────────────────────────
function setStepState(stepKey, state) {
  // Update tab buttons that include this step in their data-steps attribute
  $$('.tab-btn[data-steps]').forEach(btn => {
    const steps = btn.dataset.steps.split(',');
    if (!steps.includes(stepKey)) return;
    // For multi-step tabs (e.g. News = research_company,get_reports),
    // compute aggregate state from all steps
    updateTabState(btn);
  });
}

function updateTabState(btn) {
  const steps = btn.dataset.steps.split(',');
  btn.classList.remove('step-done', 'step-active', 'step-error', 'step-partial');

  // Gather states from current pipeline status
  let doneCount = 0, activeCount = 0, errorCount = 0;
  steps.forEach(s => {
    const st = _stepStates[s];
    if (st === 'done') doneCount++;
    else if (st === 'active') activeCount++;
    else if (st === 'error') errorCount++;
  });

  if (activeCount > 0) btn.classList.add('step-active');
  else if (errorCount > 0 && doneCount > 0) btn.classList.add('step-partial');
  else if (errorCount > 0) btn.classList.add('step-error');
  else if (doneCount === steps.length) btn.classList.add('step-done');
  else if (doneCount > 0) btn.classList.add('step-partial');
}

// Internal state tracker for steps
const _stepStates = {};

function _setStepInternal(stepKey, state) {
  if (state) _stepStates[stepKey] = state;
  else delete _stepStates[stepKey];
  setStepState(stepKey, state);
}

function resetSteps() {
  Object.keys(_stepStates).forEach(k => delete _stepStates[k]);
  $$('.tab-btn[data-steps]').forEach(btn => {
    btn.classList.remove('step-done', 'step-active', 'step-error', 'step-partial');
  });
}

function updateProgressBar(data) {
  const bar = $('#progressBar');
  if (!bar) return;

  const totalSteps = 12;
  const completed = (data.completed_steps || []).length;
  const failed = (data.failed_steps || []).length;
  const done = completed + failed;
  const running = data.state === 'running';

  if (data.state === 'idle' && !data.ticker) {
    bar.style.display = 'none';
    return;
  }

  // Toggle running animations
  bar.classList.toggle('running', running);
  $('#progressFill').classList.toggle('running', running);
  const hero = $('#heroBanner');
  if (hero) hero.classList.toggle('running', running);

  bar.style.display = 'block';

  const pct = Math.round((done / totalSteps) * 100);
  $('#progressFill').style.width = pct + '%';
  if (failed > 0) {
    $('#progressFill').classList.add('has-errors');
  } else {
    $('#progressFill').classList.remove('has-errors');
  }

  const stepLabels = {
    'select_company': 'Selecting company',
    'research_company': 'Researching news',
    'get_reports': 'Downloading SEC filings',
    'get_numbers': 'Extracting financial numbers',
    'extract_goals': 'Extracting management goals',
    'analyze_tone': 'Analyzing tone',
    'analyze_price': 'Analyzing price movements',
    'get_logo': 'Fetching company logo',
    'compare_reports': 'Comparing reports',
    'generate_report': 'Generating final report',
    'ten_point_analysis': 'Running ten-point analysis',
    'animate': 'Creating animation',
  };

  // Tips shown during slow phases. Keys match what current_step actually reports.
  // Phase 1 runs research_company + get_reports in parallel, but current_step ends
  // up as get_reports, so the tip is keyed there.
  const stepTips = {
    'get_reports': 'Downloading filings and collecting news articles — this may take a minute',
    'get_numbers': 'Extracting numbers, goals, and tone from SEC filings — this may take a few minutes',
    'compare_reports': 'Cross-analyzing financials, goals, and tone — this may take a few minutes',
  };

  const tipEl = $('#progressTip');

  if (data.state === 'done') {
    $('#progressText').textContent = `Complete: ${completed}/${totalSteps} steps succeeded`;
    $('#progressStep').textContent = '';
    if (tipEl) tipEl.style.display = 'none';
  } else if (data.state === 'error') {
    $('#progressText').textContent = `Stopped: ${done}/${totalSteps} steps processed`;
    $('#progressStep').textContent = '';
    if (tipEl) tipEl.style.display = 'none';
  } else if (running) {
    $('#progressText').textContent = `${done}/${totalSteps} steps complete`;
    const currentLabel = stepLabels[data.current_step] || data.current_step || '';
    $('#progressStep').textContent = currentLabel ? `${currentLabel}\u2026` : '';
    const tip = stepTips[data.current_step] || '';
    if (tipEl) {
      tipEl.textContent = tip;
      tipEl.style.display = tip ? 'block' : 'none';
    }
  } else {
    $('#progressText').textContent = `${completed}/${totalSteps} steps completed`;
    $('#progressStep').textContent = '';
    if (tipEl) tipEl.style.display = 'none';
  }

  let countsHtml = '';
  if (completed > 0) countsHtml += `<span class="count-done">${completed} done</span>`;
  if (failed > 0) countsHtml += `<span class="count-error">${failed} failed</span>`;
  const pending = totalSteps - done;
  if (pending > 0 && running) countsHtml += `<span class="count-pending">${pending} remaining</span>`;
  $('#progressCounts').innerHTML = countsHtml;

  // Failure explanation
  const msgEl = $('#progressMessage');
  if (msgEl) {
    const failedSteps = data.failed_steps || [];
    if (failedSteps.length > 0 && !running) {
      const stepFailureInfo = {
        'select_company': 'Could not identify the company from this ticker symbol.',
        'research_company': 'Could not fetch recent news for this company.',
        'get_reports': 'Could not download SEC filings (10-Q/10-K). This may be a foreign company — ADRs such as Chinese stocks (e.g. BIDU, BABA) file 20-F/6-K reports instead, which are not currently supported.',
        'get_numbers': 'Could not extract financial numbers from the filings.',
        'extract_goals': 'Could not extract management goals from the filings.',
        'analyze_tone': 'Tonal analysis of filings failed.',
        'analyze_price': 'Stock price analysis failed.',
        'get_logo': 'Could not fetch the company logo.',
        'compare_reports': 'Could not compare the two filing periods.',
        'generate_report': 'Final report generation failed.',
        'ten_point_analysis': 'Ten-point analysis failed.',
        'animate': 'Animation video creation failed.',
      };
      let parts = [];
      for (const step of failedSteps) {
        parts.push(`<strong>${step.replace(/_/g, ' ')}:</strong> ${stepFailureInfo[step] || 'This step failed.'}`);
      }
      if (failedSteps.includes('get_reports') && data.state === 'error') {
        parts.push('The pipeline stopped because SEC filings are required for further analysis. Only US-listed companies that file 10-Q/10-K reports with the SEC are fully supported.');
      }
      msgEl.innerHTML = parts.join('<br>');
      msgEl.style.display = 'block';
    } else {
      msgEl.style.display = 'none';
    }
  }
}

async function rerunStep(stepName) {
  if (!currentTicker) {
    log('No stock selected.', 'log-err');
    return;
  }
  if (polling) {
    log('Cannot re-run step while pipeline is running.', 'log-err');
    return;
  }

  // Find the tab that owns this step for labeling
  const tabBtn = $$('.tab-btn[data-steps]').find(b => b.dataset.steps.split(',').includes(stepName));
  const label = tabBtn ? tabBtn.textContent.trim() + ' (' + stepName + ')' : stepName;

  if (!confirm(`Re-run "${label}" for ${currentTicker}?`)) return;

  log(`Re-running step: ${label} for ${currentTicker}…`, 'log-step');
  _setStepInternal(stepName, 'active');

  try {
    const res = await api(`/api/run-step/${currentTicker}/${stepName}`, { method: 'POST' });
    if (res.error) {
      log(`Re-run failed: ${res.error}`, 'log-err');
      _setStepInternal(stepName, 'error');
      return;
    }

    // Poll until step completes
    polling = true;
    setStatus('pending');
    const pollStep = setInterval(async () => {
      const data = await api('/api/status');
      if (!data || data.error) return;

      // Process logs
      if (data.logs) data.logs.forEach(l => {
        const cls = l.includes('✓') || l.includes('completed') || l.includes('saved') ? 'log-ok'
                  : l.includes('✗') || l.includes('ERROR') || l.includes('failed') ? 'log-err'
                  : l.includes('STEP') ? 'log-step' : '';
        log(l, cls);
      });

      if (data.state !== 'running') {
        clearInterval(pollStep);
        polling = false;
        setStatus(data.state === 'error' ? 'offline' : 'online');

        // Update step states
        const completed = data.completed_steps || [];
        const failed = data.failed_steps || [];
        if (completed.includes(stepName)) _setStepInternal(stepName, 'done');
        else if (failed.includes(stepName)) _setStepInternal(stepName, 'error');

        // Reload content for this stock
        await loadPastAnalysis(currentTicker);
        log(`✅ Step "${label}" re-run complete`, 'log-ok');
      }
    }, 1500);
  } catch (e) {
    log(`Re-run error: ${e.message}`, 'log-err');
    _setStepInternal(stepName, 'error');
  }
}

// ── API Calls ────────────────────────────────────────────────────────
function cacheBust(path) {
  const sep = path.includes('?') ? '&' : '?';
  return path + sep + '_t=' + Date.now();
}

async function api(path, opts = {}) {
  try {
    const res = await fetch(cacheBust(path), opts);
    return await res.json();
  } catch (e) {
    log(`API error: ${e.message}`, 'log-err');
    return { error: e.message };
  }
}

// ── Ticker Validation ────────────────────────────────────────────────
async function validateTicker(input) {
  try {
    const res = await fetch(cacheBust(`/api/validate-ticker/${encodeURIComponent(input)}`));
    return await res.json();
  } catch (e) {
    // If validation fails (network error), allow through
    return { valid: true, ticker: input.toUpperCase(), company_name: null, suggestions: [], message: '' };
  }
}

function showTickerSuggestions(validation) {
  // Remove any existing suggestion dropdown
  hideTickerSuggestions();

  const input = $('#tickerInput');
  const container = input.parentElement;

  const dropdown = document.createElement('div');
  dropdown.id = 'tickerSuggestions';
  dropdown.className = 'ticker-suggestions';

  const header = document.createElement('div');
  header.className = 'suggestion-header';
  header.textContent = validation.message;
  dropdown.appendChild(header);

  if (validation.suggestions && validation.suggestions.length > 0) {
    for (const s of validation.suggestions) {
      const item = document.createElement('button');
      item.className = 'suggestion-item';
      item.innerHTML = `<span class="suggestion-ticker">${escHtml(s.ticker)}</span> <span class="suggestion-name">${escHtml(s.name)}</span>`;
      item.onclick = () => {
        $('#tickerInput').value = s.ticker;
        hideTickerSuggestions();
        startPipeline(s.ticker);
      };
      dropdown.appendChild(item);
    }
  }

  container.style.position = 'relative';
  container.appendChild(dropdown);

  // Close on outside click
  setTimeout(() => {
    document.addEventListener('click', _closeSuggestionsHandler, { once: true });
  }, 50);
}

function _closeSuggestionsHandler(e) {
  const dd = $('#tickerSuggestions');
  if (dd && !dd.contains(e.target)) hideTickerSuggestions();
}

function hideTickerSuggestions() {
  const dd = $('#tickerSuggestions');
  if (dd) dd.remove();
}

// ── Run Pipeline ─────────────────────────────────────────────────────
async function runPipeline() {
  const raw = $('#tickerInput').value.trim();
  if (!raw) {
    log('Please enter a ticker symbol.', 'log-err');
    return;
  }

  hideTickerSuggestions();
  log(`Validating ticker "${raw}"…`, 'log-info');

  const validation = await validateTicker(raw);

  if (!validation.valid) {
    log(`Invalid ticker: ${validation.message}`, 'log-err');
    showTickerSuggestions(validation);
    return;
  }

  // Use the validated ticker (may differ in case from input)
  const ticker = validation.ticker;
  $('#tickerInput').value = ticker;
  if (validation.company_name) {
    log(`Validated: ${validation.company_name} (${ticker})`, 'log-ok');
  }
  startPipeline(ticker);
}

const AUTO_SELECT_TICKERS = [
  'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META', 'NVDA', 'TSLA', 'JPM', 'V', 'MA',
  'UNH', 'JNJ', 'PG', 'HD', 'DIS', 'NFLX', 'CRM', 'AMD', 'INTC', 'COST',
  'WMT', 'KO', 'PEP', 'ADBE', 'PYPL',
];

async function autoSelect() {
  const pick = AUTO_SELECT_TICKERS[Math.floor(Math.random() * AUTO_SELECT_TICKERS.length)];
  const companies = await api('/api/companies');
  if (Array.isArray(companies) && companies.some(c => c.ticker === pick)) {
    if (confirm(`${pick} has already been analyzed. Re-run?`)) {
      rerunAnalysis(pick);
    }
    return;
  }
  log(`Auto-selected: ${pick}`, 'log-ok');
  startPipeline(pick);
}

async function stopPipeline() {
  $('#stopBtn').disabled = true;
  log('Requesting pipeline stop…', 'log-info');
  const res = await api('/api/stop', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  if (res.error) {
    log('Stop failed: ' + res.error, 'log-err');
  } else {
    log('Stop requested — waiting for current step to finish…', 'log-info');
  }
  $('#stopBtn').disabled = false;
}

async function startPipeline(ticker) {
  // Cancel any in-flight loads from a previous company
  ++loadGeneration;
  _loadedFiles.clear();

  $('#runBtn').disabled = true;
  $('#autoBtn').disabled = true;
  $('#stopBtn').style.display = 'inline-flex';
  setStatus('pending');
  resetSteps();
  resetContentPanels();
  clearLogs();

  showLoading(ticker || '…', ticker ? `Starting analysis for ${ticker}…` : 'Auto-selecting company…');
  log(ticker ? `Starting pipeline for ${ticker}…` : 'Auto-selecting company…', 'log-step');

  $('#emptyState').style.display = 'none';
  $('#overviewGrid').style.display = 'grid';
  $('#heroBanner').style.display = 'flex';
  $('#progressBar').style.display = 'block';
  $('#progressFill').style.width = '0%';
  $('#progressFill').classList.remove('has-errors');
  $('#progressText').textContent = '0/12 steps complete';
  $('#progressStep').textContent = 'Starting\u2026';
  $('#progressCounts').innerHTML = '<span class="count-pending">12 remaining</span>';
  $('#progressMessage').style.display = 'none';
  loadHeroLogo(ticker || null);
  if (ticker) _loadedFiles.add(`hero/${ticker}`);
  $('#heroName').textContent = ticker ? `Analyzing ${ticker}…` : 'Selecting company…';
  $('#heroMeta').textContent = '';

  const body = ticker ? { ticker } : {};
  const res = await api('/api/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  if (res.error) {
    hideLoading();
    log(`Failed to start: ${res.error}`, 'log-err');
    setStatus('offline');
    $('#runBtn').disabled = false;
    $('#autoBtn').disabled = false;
    $('#stopBtn').style.display = 'none';
    return;
  }

  hideLoading();
  log('Pipeline started. Polling for updates…', 'log-ok');
  currentTicker = res.ticker || ticker;
  clearChat();
  setUrlHash(currentTicker);
  startPolling();
}

// ── Polling ───────────────────────────────────────────────────────────
function startPolling() {
  if (polling) clearInterval(polling);
  polling = setInterval(pollStatus, POLL_MS);
  pollStatus();
}

function stopPolling() {
  if (polling) { clearInterval(polling); polling = null; }
}

async function pollStatus() {
  const data = await api('/api/status');
  if (data.error) return;

  if (data.state === 'idle' && !data.ticker) return;

  currentTicker = data.ticker || currentTicker;
  currentDates = data.report_dates || [];
  if (currentTicker) setUrlHash(currentTicker);

  // Show/hide stop button based on running state
  $('#stopBtn').style.display = data.state === 'running' ? 'inline-flex' : 'none';

  // Update hero
  if (data.company_name) {
    const heroKey = `hero/${data.ticker}`;
    if (!_loadedFiles.has(heroKey)) {
      loadHeroLogo(data.ticker);
      _loadedFiles.add(heroKey);
    }
    $('#heroName').textContent = data.company_name;
    let metaText = `Report dates: ${currentDates.join(', ') || 'detecting…'}`;
    if (data.state === 'running') {
      metaText += '  \u2022  Pipeline running\u2026';
    } else if (data.completed_at) {
      metaText += `  \u2022  Last analyzed: ${formatTimestamp(data.completed_at)}`;
    }
    $('#heroMeta').textContent = metaText;
    if (!$('#tickerInput').value) $('#tickerInput').value = data.ticker;
  }

  // Update progress bar
  updateProgressBar(data);

  // Update pipeline steps
  const completed = data.completed_steps || [];
  const current = data.current_step || '';
  const failed = data.failed_steps || [];

  const stepOrder = [
    'select_company', 'research_company', 'get_reports', 'get_numbers',
    'extract_goals', 'analyze_tone', 'analyze_price', 'get_logo',
    'compare_reports', 'generate_report', 'ten_point_analysis', 'animate'
  ];

  // Steps that run in parallel phases — if any one is active, all in the group are
  const parallelGroups = [
    ['research_company', 'get_reports'],
    ['get_numbers', 'extract_goals', 'analyze_tone', 'analyze_price', 'get_logo'],
  ];

  // Build set of active steps: current step + its parallel siblings
  const activeSteps = new Set();
  if (current && data.state === 'running') {
    activeSteps.add(current);
    for (const group of parallelGroups) {
      if (group.includes(current)) {
        group.forEach(s => {
          if (!completed.includes(s) && !failed.includes(s)) activeSteps.add(s);
        });
      }
    }
  }

  stepOrder.forEach(s => {
    if (completed.includes(s)) _setStepInternal(s, 'done');
    else if (failed.includes(s)) _setStepInternal(s, 'error');
    else if (activeSteps.has(s)) _setStepInternal(s, 'active');
    else _setStepInternal(s, null);
  });

  // Load available content
  if (data.files) {
    loadAvailableContent(data.files, data.ticker);
  }

  // Logs
  if (data.logs) {
    data.logs.forEach(l => {
      const cls = l.includes('ERROR') ? 'log-err'
                : l.includes('STEP') || l.includes('SKILL') ? 'log-step'
                : l.includes('completed') || l.includes('saved') ? 'log-ok'
                : 'log-info';
      log(l, cls);
    });
  }

  refreshRunLog();

  if (data.state === 'done' || data.state === 'error') {
    stopPolling();
    setStatus(data.state === 'done' ? 'online' : 'offline');
    $('#runBtn').disabled = false;
    $('#autoBtn').disabled = false;
    $('#stopBtn').style.display = 'none';
    log(
      data.state === 'done'
        ? '✅ Pipeline completed successfully!'
        : '❌ Pipeline finished with errors.',
      data.state === 'done' ? 'log-ok' : 'log-err'
    );

    if (data.ticker) loadAllContent(data.ticker, currentDates);
    refreshRunLog();
    loadCompanies();
  }
}

// ── Content Loading ───────────────────────────────────────────────────
async function loadFile(ticker, filename) {
  const res = await fetch(cacheBust(`/api/file/${ticker}/${filename}`));
  if (!res.ok) return null;
  const data = await res.json();
  return data.content || null;
}

async function loadAvailableContent(files, ticker) {
  if (!files || !ticker) return;
  // Capture generation at call time; bail if user switches company mid-load
  const gen = loadGeneration;
  const stale = () => gen !== loadGeneration;
  const fkey = (fn) => `${ticker}/${fn}`;

  if (files.includes('news.md') && !_loadedFiles.has(fkey('news.md'))) {
    const c = await loadFile(ticker, 'news.md');
    if (stale()) return;
    if (c) {
      $('#newsContent').innerHTML = md(c);
      _loadedFiles.add(fkey('news.md'));
    }
  }

  for (const f of files) {
    if (stale()) return;
    if (f.endsWith('_numbers.md') && !_loadedFiles.has(fkey(f))) {
      const date = f.replace('_numbers.md', '');
      const c = await loadFile(ticker, f);
      if (stale()) return;
      if (c) {
        if (!$('#numbersLatest').dataset.loaded) {
          $('#numbersLatest').innerHTML = md(c);
          $('#numbersLatestDate').textContent = date;
          $('#numbersLatest').dataset.loaded = date;
        } else if ($('#numbersLatest').dataset.loaded !== date) {
          $('#numbersPrior').innerHTML = md(c);
          $('#numbersPriorDate').textContent = date;
        }
        _loadedFiles.add(fkey(f));
      }
    }
    if (f.endsWith('_goals.md') && !_loadedFiles.has(fkey(f))) {
      const date = f.replace('_goals.md', '');
      const c = await loadFile(ticker, f);
      if (stale()) return;
      if (c) {
        if (!$('#goalsLatest').dataset.loaded) {
          $('#goalsLatest').innerHTML = md(c);
          $('#goalsLatestDate').textContent = date;
          $('#goalsLatest').dataset.loaded = date;
        } else if ($('#goalsLatest').dataset.loaded !== date) {
          $('#goalsPrior').innerHTML = md(c);
          $('#goalsPriorDate').textContent = date;
        }
        _loadedFiles.add(fkey(f));
      }
    }
    if (f.endsWith('_tone.md') && !_loadedFiles.has(fkey(f))) {
      const c = await loadFile(ticker, f);
      if (stale()) return;
      if (c) {
        $('#toneContent').innerHTML = md(c);
        _loadedFiles.add(fkey(f));
      }
    }
    if (f.endsWith('_compare.md') && !_loadedFiles.has(fkey(f))) {
      const c = await loadFile(ticker, f);
      if (stale()) return;
      if (c) {
        $('#compareContent').innerHTML = md(c);
        updateOverview(c);
        _loadedFiles.add(fkey(f));
      }
    }
    // Load OHLC data when available
    if (f === 'ohlc.json' && !_loadedFiles.has(fkey('ohlc.json'))) {
      loadOHLCData(ticker);
      _loadedFiles.add(fkey('ohlc.json'));
    }
    if (f === 'ticker_analysis.md' && !_loadedFiles.has(fkey('ticker_analysis.md'))) {
      const c = await loadFile(ticker, 'ticker_analysis.md');
      if (stale()) return;
      if (c) {
        renderPriceAnalysis(c);
        _loadedFiles.add(fkey('ticker_analysis.md'));
      }
    }
    // Load logo when available
    if (f === 'logo.jpeg' && !_loadedFiles.has(fkey('logo.jpeg'))) {
      loadLogo(ticker);
      _loadedFiles.add(fkey('logo.jpeg'));
    }
    // Load bullets when available
    if (f === 'bullets.json' && !_loadedFiles.has(fkey('bullets.json'))) {
      if (stale()) return;
      loadBullets(ticker);
      _loadedFiles.add(fkey('bullets.json'));
    }
    // Load animation when available
    if (f === 'overview.mp4' && !_loadedFiles.has(fkey('overview.mp4'))) {
      if (stale()) return;
      loadAnimation(ticker);
      _loadedFiles.add(fkey('overview.mp4'));
    }
  }
}

async function loadAllContent(ticker, dates, gen) {
  if (!ticker) return;
  if (gen === undefined) gen = loadGeneration;
  const stale = () => gen !== loadGeneration;

  // Batch 1: Fetch all text files + report links in parallel
  const filePromises = [
    loadFile(ticker, 'news.md'),                                                // 0
    dates[0] ? loadFile(ticker, `${dates[0]}_numbers.md`) : Promise.resolve(null),  // 1
    dates[1] ? loadFile(ticker, `${dates[1]}_numbers.md`) : Promise.resolve(null),  // 2
    dates[0] ? loadFile(ticker, `${dates[0]}_goals.md`)   : Promise.resolve(null),  // 3
    dates[1] ? loadFile(ticker, `${dates[1]}_goals.md`)   : Promise.resolve(null),  // 4
    dates[0] ? loadFile(ticker, `${dates[0]}_tone.md`)    : Promise.resolve(null),  // 5
    loadFile(ticker, 'ticker_analysis.md'),                                      // 6
    api(`/api/report-links/${ticker}`),                                          // 7
    dates.length >= 2 ? loadFile(ticker, `${dates[1]}_${dates[0]}_compare.md`) : Promise.resolve(null), // 8
    dates.length >= 2 ? loadFile(ticker, `${dates[0]}_${dates[1]}_compare.md`) : Promise.resolve(null), // 9
  ];

  const [news, n1, n2, g1, g2, tone, priceAnalysis, reportMeta, cmp1, cmp2] =
    await Promise.all(filePromises);

  if (stale()) return;

  // Render all text content
  if (news) $('#newsContent').innerHTML = md(news);
  if (n1) { $('#numbersLatest').innerHTML = md(n1); $('#numbersLatestDate').textContent = dates[0]; }
  if (n2) { $('#numbersPrior').innerHTML = md(n2); $('#numbersPriorDate').textContent = dates[1]; }
  if (g1) { $('#goalsLatest').innerHTML = md(g1); $('#goalsLatestDate').textContent = dates[0]; }
  if (g2) { $('#goalsPrior').innerHTML = md(g2); $('#goalsPriorDate').textContent = dates[1]; }
  if (tone) $('#toneContent').innerHTML = md(tone);
  if (priceAnalysis) renderPriceAnalysis(priceAnalysis);

  const cmp = cmp1 || cmp2;
  if (cmp) {
    $('#compareContent').innerHTML = md(cmp);
    updateOverview(cmp);
  } else {
    await buildOverviewFallback(ticker, dates);
  }

  if (stale()) return;

  // Batch 2: Load media/data in parallel
  await Promise.all([
    loadOHLCData(ticker),
    Promise.resolve(loadLogo(ticker)),
    Promise.resolve(loadBullets(ticker)),
    Promise.resolve(loadAnimation(ticker)),
  ]);

  if (stale()) return;

  // Report links
  if (reportMeta && reportMeta.html) {
    $('#reportPending').style.display = 'none';
    $('#reportLinks').style.display = 'block';
    $('#htmlLink').href = reportMeta.html;
    if (reportMeta.pdf) {
      $('#pdfLink').href = reportMeta.pdf;
      $('#pdfLink').style.display = 'inline-flex';
    }
  }

  showMetricsPlaceholder(ticker, dates);
}

function showMetricsPlaceholder(ticker, dates) {
  const grid = $('#metricsGrid');
  grid.style.display = 'grid';
  grid.innerHTML = `
    <div class="metric-card neutral">
      <div class="metric-label">Ticker</div>
      <div class="metric-value flat">${ticker}</div>
    </div>
    <div class="metric-card neutral">
      <div class="metric-label">Reports Analyzed</div>
      <div class="metric-value flat">${dates.length}</div>
    </div>
    <div class="metric-card neutral">
      <div class="metric-label">Latest Filing</div>
      <div class="metric-value flat">${dates[0] || '—'}</div>
    </div>
    <div class="metric-card neutral">
      <div class="metric-label">Prior Filing</div>
      <div class="metric-value flat">${dates[1] || '—'}</div>
    </div>
  `;
}

// ── Overview Helpers ──────────────────────────────────────────────────
function updateOverview(markdownContent) {
  $('#overviewContent').innerHTML = md(markdownContent);
  $('#overviewBadge').textContent = 'Complete';
  $('#overviewBadge').className = 'card-badge badge-pos';
}

async function buildOverviewFallback(ticker, dates) {
  let parts = [];

  const news = await loadFile(ticker, 'news.md');
  if (news) {
    const snippet = news.substring(0, 600).trim();
    parts.push(`## Recent News\n${snippet}…`);
  }

  if (dates.length >= 1) {
    const nums = await loadFile(ticker, `${dates[0]}_numbers.md`);
    if (nums) {
      const metricsMatch = nums.match(/## Key Metrics Summary[\s\S]*?(?=\n## |$)/);
      if (metricsMatch) {
        parts.push(metricsMatch[0]);
      } else {
        parts.push(`## Latest Numbers (${dates[0]})\n${nums.substring(0, 500).trim()}…`);
      }
    }
  }

  if (dates.length >= 1) {
    const tone = await loadFile(ticker, `${dates[0]}_tone.md`);
    if (tone) {
      const summaryMatch = tone.match(/## Executive Summary[\s\S]*?(?=\n## |$)/);
      if (summaryMatch) parts.push(summaryMatch[0]);
    }
  }

  if (parts.length > 0) {
    const combined = `# Overview: ${ticker}\n\n${parts.join('\n\n')}`;
    $('#overviewContent').innerHTML = md(combined);
    $('#overviewBadge').textContent = 'Partial';
    $('#overviewBadge').className = 'card-badge badge-info';
  } else {
    $('#overviewContent').innerHTML = '<p style="color:var(--c-text-dim)">No analysis data available yet.</p>';
    $('#overviewBadge').textContent = 'Pending';
    $('#overviewBadge').className = 'card-badge badge-info';
  }
}

// ── Price Analysis & OHLC Chart ───────────────────────────────────────

async function loadOHLCData(ticker) {
  try {
    const res = await fetch(cacheBust(`/api/ohlc/${ticker}`));
    if (!res.ok) return;
    const data = await res.json();
    if (data.error) return;

    ohlcData = data;
    renderPriceSentiment(data);
    renderPriceStats(data);
    drawChart('inter');

    // Show chart elements
    $('#pricePending').style.display = 'none';
    $('#priceSentimentRow').style.display = 'flex';
    $('#priceChartCard').style.display = 'block';
    $('#priceStatsGrid').style.display = 'grid';
    $('#priceAnalysisCard').style.display = 'block';
  } catch (e) {
    // silently ignore if no OHLC data yet
  }
}

function renderPriceAnalysis(content) {
  $('#priceAnalysisContent').innerHTML = md(content);
  $('#priceAnalysisCard').style.display = 'block';
  $('#pricePending').style.display = 'none';
}

function sentimentColor(overall) {
  if (!overall) return 'var(--c-text-dim)';
  const o = overall.toLowerCase();
  if (o.includes('strongly bullish')) return '#22c55e';
  if (o.includes('bullish')) return '#4ade80';
  if (o.includes('strongly bearish')) return '#ef4444';
  if (o.includes('bearish')) return '#f87171';
  return 'var(--c-text-dim)';
}

function renderPriceSentiment(data) {
  const inter = data.inter_report;
  const post = data.post_earnings;

  if (inter && inter.sentiment) {
    const s = inter.sentiment;
    const st = inter.stats;
    $('#sentimentInterDates').textContent = `${inter.start_date} → ${inter.end_date}`;
    $('#sentimentInterBadge').textContent = (s.overall || 'N/A').toUpperCase();
    $('#sentimentInterBadge').style.color = sentimentColor(s.overall);
    if (st && !st.error) {
      const chg = st.total_change_pct;
      const sign = chg >= 0 ? '+' : '';
      $('#sentimentInterChange').textContent = `${sign}${chg}% over ${st.trading_days} days`;
      $('#sentimentInterChange').style.color = chg >= 0 ? 'var(--c-accent-pos)' : 'var(--c-accent-neg)';
    }
    $('#sentimentInterCard').style.borderColor = sentimentColor(s.overall);
  }

  if (post && post.sentiment) {
    const s = post.sentiment;
    const st = post.stats;
    $('#sentimentPostDates').textContent = `${post.start_date} → ${post.end_date}`;
    $('#sentimentPostBadge').textContent = (s.overall || 'N/A').toUpperCase();
    $('#sentimentPostBadge').style.color = sentimentColor(s.overall);
    if (st && !st.error) {
      const chg = st.total_change_pct;
      const sign = chg >= 0 ? '+' : '';
      $('#sentimentPostChange').textContent = `${sign}${chg}% over ${st.trading_days} days`;
      $('#sentimentPostChange').style.color = chg >= 0 ? 'var(--c-accent-pos)' : 'var(--c-accent-neg)';
    }
    $('#sentimentPostCard').style.borderColor = sentimentColor(s.overall);
  }
}

function renderPriceStats(data) {
  function statsTable(stats) {
    if (!stats || stats.error) return `<p style="color:var(--c-text-dim)">${stats?.error || 'No data'}</p>`;
    const rows = [
      ['Start Price', `$${stats.start_price?.toLocaleString('en-US', {minimumFractionDigits:2})}`],
      ['End Price', `$${stats.end_price?.toLocaleString('en-US', {minimumFractionDigits:2})}`],
      ['Total Change', `${stats.total_change_pct >= 0 ? '+' : ''}${stats.total_change_pct}%`],
      ['Period High', `$${stats.high?.toLocaleString('en-US', {minimumFractionDigits:2})} (${stats.high_date})`],
      ['Period Low', `$${stats.low?.toLocaleString('en-US', {minimumFractionDigits:2})} (${stats.low_date})`],
      ['Avg Price', `$${stats.avg_price?.toLocaleString('en-US', {minimumFractionDigits:2})}`],
      ['Annualized Vol', `${stats.annualized_volatility}%`],
      ['Max Drawdown', `-${stats.max_drawdown_pct}%`],
      ['Win Rate', `${stats.win_rate}%`],
      ['Days Above Mean', `${stats.days_above_mean} (${stats.pct_above_mean}%)`],
      ['Avg Volume', stats.avg_daily_volume?.toLocaleString('en-US')],
    ];
    return `<table>
      <thead><tr><th>Metric</th><th>Value</th></tr></thead>
      <tbody>${rows.map(([k, v]) => {
        let cls = '';
        if (k === 'Total Change') {
          cls = stats.total_change_pct >= 0 ? ' class="positive"' : ' class="negative"';
        }
        return `<tr><td>${k}</td><td${cls}>${v}</td></tr>`;
      }).join('')}</tbody>
    </table>`;
  }

  const inter = data.inter_report;
  const post = data.post_earnings;

  if (inter) {
    $('#priceInterStats').innerHTML = statsTable(inter.stats);
    if (inter.stats && !inter.stats.error) {
      $('#priceInterDays').textContent = `${inter.stats.trading_days} trading days`;
    }
  }
  if (post) {
    $('#pricePostStats').innerHTML = statsTable(post.stats);
    if (post.stats && !post.stats.error) {
      $('#pricePostDays').textContent = `${post.stats.trading_days} trading days`;
    }
  }
}

// ── OHLC Chart Renderer (Canvas) ──────────────────────────────────────

function showChartWindow(window) {
  activeWindow = window;
  $('#btnInterReport').classList.toggle('active', window === 'inter');
  $('#btnPostEarnings').classList.toggle('active', window === 'post');
  if (ohlcData) drawChart(window);
}

function drawChart(window) {
  const canvas = $('#priceCanvas');
  const volCanvas = $('#volumeCanvas');
  if (!canvas || !ohlcData) return;

  const windowData = window === 'inter' ? ohlcData.inter_report : ohlcData.post_earnings;
  if (!windowData || !windowData.data || windowData.data.length === 0) {
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = 'rgba(148,163,184,0.5)';
    ctx.font = '14px Inter, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('No price data available for this window', canvas.width / 2, canvas.height / 2);
    return;
  }

  const points = windowData.data;
  const stats = windowData.stats;

  // Size canvas to container
  const container = canvas.parentElement;
  const W = container.clientWidth || 800;
  const H = 280;
  canvas.width = W;
  canvas.height = H;
  volCanvas.width = W;
  volCanvas.height = 60;

  const ctx = canvas.getContext('2d');
  const vCtx = volCanvas.getContext('2d');
  ctx.clearRect(0, 0, W, H);
  vCtx.clearRect(0, 0, W, 60);

  const prices = points.map(p => p.price);
  const volumes = points.map(p => p.volume);
  const minP = Math.min(...prices);
  const maxP = Math.max(...prices);
  const maxV = Math.max(...volumes);
  const priceRange = maxP - minP || 1;

  const PAD_L = 60, PAD_R = 20, PAD_T = 20, PAD_B = 30;
  const chartW = W - PAD_L - PAD_R;
  const chartH = H - PAD_T - PAD_B;

  const xScale = (i) => PAD_L + (i / (points.length - 1 || 1)) * chartW;
  const yScale = (p) => PAD_T + (1 - (p - minP) / priceRange) * chartH;

  // CSS variables (approximate dark theme colors)
  const C_GRID = 'rgba(148,163,184,0.12)';
  const C_AXIS = 'rgba(148,163,184,0.4)';
  const C_TEXT = 'rgba(148,163,184,0.8)';
  const C_LINE_POS = '#22c55e';
  const C_LINE_NEG = '#ef4444';
  const C_MEAN = 'rgba(251,191,36,0.7)';
  const C_VOL_POS = 'rgba(34,197,94,0.5)';
  const C_VOL_NEG = 'rgba(239,68,68,0.5)';

  const isPositive = prices[prices.length - 1] >= prices[0];
  const lineColor = isPositive ? C_LINE_POS : C_LINE_NEG;

  // Grid lines (5 horizontal)
  ctx.strokeStyle = C_GRID;
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = PAD_T + (i / 4) * chartH;
    ctx.beginPath();
    ctx.moveTo(PAD_L, y);
    ctx.lineTo(W - PAD_R, y);
    ctx.stroke();
    // Price label
    const price = maxP - (i / 4) * priceRange;
    ctx.fillStyle = C_TEXT;
    ctx.font = '10px Inter, sans-serif';
    ctx.textAlign = 'right';
    ctx.fillText(`$${price.toFixed(0)}`, PAD_L - 4, y + 4);
  }

  // 30-day rolling mean line
  if (stats && stats.rolling_mean_30d) {
    const meanY = yScale(stats.rolling_mean_30d);
    ctx.strokeStyle = C_MEAN;
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(PAD_L, meanY);
    ctx.lineTo(W - PAD_R, meanY);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = C_MEAN;
    ctx.font = '9px Inter, sans-serif';
    ctx.textAlign = 'left';
    ctx.fillText(`30d mean $${stats.rolling_mean_30d.toFixed(0)}`, PAD_L + 4, meanY - 3);
  }

  // Gradient fill under line
  const grad = ctx.createLinearGradient(0, PAD_T, 0, H - PAD_B);
  grad.addColorStop(0, isPositive ? 'rgba(34,197,94,0.25)' : 'rgba(239,68,68,0.25)');
  grad.addColorStop(1, 'rgba(0,0,0,0)');

  ctx.beginPath();
  ctx.moveTo(xScale(0), yScale(prices[0]));
  for (let i = 1; i < points.length; i++) {
    ctx.lineTo(xScale(i), yScale(prices[i]));
  }
  ctx.lineTo(xScale(points.length - 1), H - PAD_B);
  ctx.lineTo(xScale(0), H - PAD_B);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();

  // Price line
  ctx.beginPath();
  ctx.strokeStyle = lineColor;
  ctx.lineWidth = 2;
  ctx.moveTo(xScale(0), yScale(prices[0]));
  for (let i = 1; i < points.length; i++) {
    ctx.lineTo(xScale(i), yScale(prices[i]));
  }
  ctx.stroke();

  // X-axis date labels (show ~5 evenly spaced)
  ctx.fillStyle = C_TEXT;
  ctx.font = '9px Inter, sans-serif';
  ctx.textAlign = 'center';
  const labelCount = Math.min(5, points.length);
  for (let i = 0; i < labelCount; i++) {
    const idx = Math.round((i / (labelCount - 1 || 1)) * (points.length - 1));
    const x = xScale(idx);
    const date = points[idx].date.slice(5); // MM-DD
    ctx.fillText(date, x, H - PAD_B + 14);
  }

  // Axis lines
  ctx.strokeStyle = C_AXIS;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(PAD_L, PAD_T);
  ctx.lineTo(PAD_L, H - PAD_B);
  ctx.lineTo(W - PAD_R, H - PAD_B);
  ctx.stroke();

  // Volume bars
  vCtx.clearRect(0, 0, W, 60);
  const barW = Math.max(1, chartW / points.length - 1);
  for (let i = 0; i < points.length; i++) {
    const x = xScale(i) - barW / 2;
    const barH = (points[i].volume / (maxV || 1)) * 50;
    const isUp = points[i].change_pct >= 0;
    vCtx.fillStyle = isUp ? C_VOL_POS : C_VOL_NEG;
    vCtx.fillRect(x, 60 - barH, barW, barH);
  }

  // Hover tooltip
  canvas.onmousemove = (e) => {
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const idx = Math.round(((mx - PAD_L) / chartW) * (points.length - 1));
    if (idx < 0 || idx >= points.length) {
      $('#chartTooltip').style.display = 'none';
      return;
    }
    const p = points[idx];
    const sign = p.change_pct >= 0 ? '+' : '';
    const tooltip = $('#chartTooltip');
    tooltip.style.display = 'block';
    tooltip.style.left = `${Math.min(mx + 12, W - 160)}px`;
    tooltip.style.top = `${Math.max(yScale(p.price) - 60, 10)}px`;
    tooltip.innerHTML = `
      <div class="tt-date">${p.date}</div>
      <div class="tt-price">$${p.price.toFixed(2)}</div>
      <div class="tt-change" style="color:${p.change_pct >= 0 ? '#4ade80' : '#f87171'}">${sign}${p.change_pct.toFixed(2)}%</div>
      <div class="tt-vol">Vol: ${(p.volume / 1e6).toFixed(1)}M</div>
    `;
  };
  canvas.onmouseleave = () => {
    $('#chartTooltip').style.display = 'none';
  };
}

// ── Company History ───────────────────────────────────────────────────
async function loadCompanies() {
  const companies = await api('/api/companies');
  if (!companies || companies.error || !Array.isArray(companies) || companies.length === 0) {
    $('#historyBar').style.display = 'none';
    return;
  }

  $('#historyBar').style.display = 'flex';
  const chips = $('#historyChips');
  chips.innerHTML = '';

  for (const c of companies) {
    const wrapper = document.createElement('div');
    wrapper.className = 'history-chip-group';

    const chip = document.createElement('button');
    chip.className = 'history-chip';
    if (currentTicker && c.ticker === currentTicker) chip.classList.add('active');

    chip.innerHTML = c.ticker;
    chip.onclick = () => loadPastAnalysis(c.ticker);

    const rerunBtn = document.createElement('button');
    rerunBtn.className = 'chip-action chip-rerun';
    rerunBtn.title = `Rerun analysis for ${c.ticker}`;
    rerunBtn.innerHTML = '↻';
    rerunBtn.onclick = (e) => { e.stopPropagation(); rerunAnalysis(c.ticker); };

    const deleteBtn = document.createElement('button');
    deleteBtn.className = 'chip-action chip-delete';
    deleteBtn.title = `Delete analysis for ${c.ticker}`;
    deleteBtn.innerHTML = '✕';
    deleteBtn.onclick = (e) => { e.stopPropagation(); deleteAnalysis(c.ticker); };

    wrapper.appendChild(chip);
    wrapper.appendChild(rerunBtn);
    wrapper.appendChild(deleteBtn);
    chips.appendChild(wrapper);
  }
}

async function loadPastAnalysis(ticker) {
  if (polling) {
    showToast('Analysis in progress \u2014 please wait until the current analysis completes.');
    return;
  }

  // Increment generation to cancel any in-flight loads from previous company
  const gen = ++loadGeneration;
  _loadedFiles.clear();

  showLoading(ticker, 'Loading analysis…');
  log(`Loading analysis for ${ticker}…`, 'log-step');

  const res = await api(`/api/load/${ticker}`);
  if (res.error) {
    hideLoading();
    log(`Failed to load: ${res.error}`, 'log-err');
    return;
  }
  // Abort if user already clicked another company
  if (gen !== loadGeneration) { hideLoading(); return; }

  currentTicker = ticker;
  clearChat();
  const data = await api('/api/status');
  if (!data || data.error) return;
  if (gen !== loadGeneration) { hideLoading(); return; }

  currentDates = data.report_dates || [];

  $('#emptyState').style.display = 'none';
  $('#overviewGrid').style.display = 'grid';
  $('#heroBanner').style.display = 'flex';
  loadHeroLogo(data.ticker || null);
  $('#heroName').textContent = data.company_name || data.ticker || '…';
  let pastMeta = `Report dates: ${currentDates.join(', ') || 'none'}`;
  if (data.completed_at) {
    pastMeta += `  \u2022  Last analyzed: ${formatTimestamp(data.completed_at)}`;
  } else {
    pastMeta += '  \u2022  Loaded from disk';
  }
  $('#heroMeta').textContent = pastMeta;
  $('#tickerInput').value = data.ticker || '';

  const completed = data.completed_steps || [];
  const failed = data.failed_steps || [];
  const stepOrder = [
    'select_company', 'research_company', 'get_reports', 'get_numbers',
    'extract_goals', 'analyze_tone', 'analyze_price', 'get_logo',
    'compare_reports', 'generate_report', 'ten_point_analysis', 'animate'
  ];
  stepOrder.forEach(s => {
    if (completed.includes(s)) _setStepInternal(s, 'done');
    else if (failed.includes(s)) _setStepInternal(s, 'error');
    else _setStepInternal(s, null);
  });

  setStatus('online');
  updateProgressBar(data);
  resetContentPanels();

  if (data.ticker) await loadAllContent(data.ticker, currentDates, gen);
  if (gen !== loadGeneration) { hideLoading(); return; }
  refreshRunLog();
  loadCompanies();

  setUrlHash(ticker);
  hideLoading();
  log(`✅ Loaded analysis for ${ticker}`, 'log-ok');
}

async function deleteAnalysis(ticker) {
  if (!confirm(`Delete all analysis data for ${ticker}? This cannot be undone.`)) return;

  log(`Deleting analysis for ${ticker}…`, 'log-step');
  try {
    const res = await fetch(cacheBust(`/api/company/${ticker}`), { method: 'DELETE' });
    const data = await res.json();
    if (data.error) {
      log(`Delete failed: ${data.error}`, 'log-err');
      return;
    }
    log(`✅ Deleted analysis for ${ticker}`, 'log-ok');

    if (currentTicker === ticker) {
      currentTicker = null;
      currentDates = [];
      ohlcData = null;
      resetContentPanels();
      resetSteps();
      $('#heroBanner').style.display = 'none';
      $('#progressBar').style.display = 'none';
      $('#emptyState').style.display = 'block';
      $('#overviewGrid').style.display = 'none';
      $('#metricsGrid').style.display = 'none';
      $('#tickerInput').value = '';
      setStatus('offline');
      setUrlHash(null);
    }

    await loadCompanies();
  } catch (e) {
    log(`Delete error: ${e.message}`, 'log-err');
  }
}

async function rerunAnalysis(ticker) {
  if (!confirm(`Rerun analysis for ${ticker}? This will delete all existing data and start fresh.`)) return;

  log(`Rerunning analysis for ${ticker}…`, 'log-step');

  currentTicker = ticker;
  clearChat();
  currentDates = [];
  ohlcData = null;
  _loadedFiles.clear();
  resetContentPanels();
  resetSteps();

  $('#emptyState').style.display = 'none';
  $('#overviewGrid').style.display = 'grid';
  $('#heroBanner').style.display = 'flex';
  $('#progressBar').style.display = 'block';
  $('#progressFill').style.width = '0%';
  $('#progressFill').classList.remove('has-errors');
  $('#progressText').textContent = '0/12 steps complete';
  $('#progressStep').textContent = 'Starting\u2026';
  $('#progressCounts').innerHTML = '<span class="count-pending">12 remaining</span>';
  $('#progressMessage').style.display = 'none';
  loadHeroLogo(ticker);
  _loadedFiles.add(`hero/${ticker}`);
  $('#heroName').textContent = `Rerunning ${ticker}…`;
  $('#heroMeta').textContent = '';
  $('#tickerInput').value = ticker;
  $('#runBtn').disabled = true;
  $('#autoBtn').disabled = true;
  setStatus('pending');

  try {
    const res = await fetch(cacheBust(`/api/rerun/${ticker}`), { method: 'POST' });
    const data = await res.json();
    if (data.error) {
      log(`Rerun failed: ${data.error}`, 'log-err');
      setStatus('offline');
      $('#runBtn').disabled = false;
      $('#autoBtn').disabled = false;
      return;
    }
    log(`Pipeline restarted for ${ticker}`, 'log-ok');
    startPolling();
  } catch (e) {
    log(`Rerun error: ${e.message}`, 'log-err');
    setStatus('offline');
    $('#runBtn').disabled = false;
    $('#autoBtn').disabled = false;
  }
}

function resetContentPanels() {
  ohlcData = null;
  _loadedFiles.clear();
  const nl = $('#numbersLatest');
  if (nl) { nl.innerHTML = '<p style="color:var(--c-text-dim)">Pending extraction…</p>'; delete nl.dataset.loaded; }
  const np = $('#numbersPrior');
  if (np) np.innerHTML = '<p style="color:var(--c-text-dim)">Pending extraction…</p>';
  const gl = $('#goalsLatest');
  if (gl) { gl.innerHTML = '<p style="color:var(--c-text-dim)">Pending extraction…</p>'; delete gl.dataset.loaded; }
  const gp = $('#goalsPrior');
  if (gp) gp.innerHTML = '<p style="color:var(--c-text-dim)">Pending extraction…</p>';
  $('#numbersLatestDate').textContent = '—';
  $('#numbersPriorDate').textContent = '—';
  $('#goalsLatestDate').textContent = '—';
  $('#goalsPriorDate').textContent = '—';
  $('#newsContent').innerHTML = '<p style="color:var(--c-text-dim)">Run the pipeline to generate a news summary.</p>';
  $('#toneContent').innerHTML = '<p style="color:var(--c-text-dim)">Run the pipeline to generate tonal analysis.</p>';
  $('#compareContent').innerHTML = '<p style="color:var(--c-text-dim)">Run the pipeline to generate comparison.</p>';
  $('#overviewContent').innerHTML = 'Waiting for analysis…';
  $('#overviewBadge').textContent = 'Pending';
  $('#overviewBadge').className = 'card-badge badge-info';
  $('#reportLinks').style.display = 'none';
  $('#reportPending').style.display = 'block';
  $('#metricsGrid').style.display = 'none';
  $('#runLogArea').innerHTML = 'No run log yet.\n';
  // Reset logo
  const logoContainer = $('#companyLogoContainer');
  if (logoContainer) logoContainer.style.display = 'none';
  const logoImg = $('#companyLogo');
  if (logoImg) logoImg.src = '';

  // Reset bullets tab
  const bulletsContent = $('#bulletsContent');
  if (bulletsContent) bulletsContent.style.display = 'block';
  const bulletsGrid = $('#bulletsGrid');
  if (bulletsGrid) bulletsGrid.style.display = 'none';
  const bulletsBadge = $('#bulletsBadge');
  if (bulletsBadge) { bulletsBadge.textContent = 'Pending'; bulletsBadge.className = 'card-badge badge-info'; }
  const yayBullets = $('#yayBullets');
  if (yayBullets) yayBullets.innerHTML = '';
  const nayBullets = $('#nayBullets');
  if (nayBullets) nayBullets.innerHTML = '';

  // Reset animation video in overview
  const videoCard = $('#overviewVideoCard');
  if (videoCard) videoCard.style.display = 'none';

  // Reset price tab
  $('#pricePending').style.display = 'block';
  $('#priceSentimentRow').style.display = 'none';
  $('#priceChartCard').style.display = 'none';
  $('#priceStatsGrid').style.display = 'none';
  $('#priceAnalysisCard').style.display = 'none';
  $('#priceAnalysisContent').innerHTML = '<p style="color:var(--c-text-dim)">Run the pipeline to generate price analysis.</p>';
  const canvas = $('#priceCanvas');
  if (canvas) {
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
  }
}

// ── Logo Loading ──────────────────────────────────────────────────
function loadLogo(ticker) {
  const img = $('#companyLogo');
  const container = $('#companyLogoContainer');
  if (!img || !container) return;

  const logoUrl = `/api/logo/${ticker}`;
  // Set src directly - if it fails the onerror will hide it
  img.onerror = () => { container.style.display = 'none'; };
  img.onload = () => { container.style.display = 'block'; };
  img.src = logoUrl + '?t=' + Date.now();
}

function loadHeroLogo(ticker) {
  const img = $('#heroLogo');
  const fallback = $('#heroTickerFallback');
  if (!img || !fallback) return;

  if (!ticker) {
    img.style.display = 'none';
    fallback.style.display = 'block';
    fallback.textContent = '…';
    return;
  }

  // Show fallback immediately while image loads
  fallback.textContent = ticker;
  fallback.style.display = 'block';
  img.style.display = 'none';

  const logoUrl = `/api/logo/${ticker}`;
  img.onerror = () => {
    img.style.display = 'none';
    fallback.style.display = 'block';
    fallback.textContent = ticker;
  };
  img.onload = () => {
    img.style.display = 'block';
    fallback.style.display = 'none';
  };
  img.src = cacheBust(logoUrl);
}

// ── Bullets Loading ──────────────────────────────────────────────
async function loadBullets(ticker) {
  try {
    const res = await api(`/api/bullets/${ticker}`);
    if (!res || res.error) return;

    const bulletsContent = $('#bulletsContent');
    const bulletsGrid = $('#bulletsGrid');
    const bulletsBadge = $('#bulletsBadge');
    if (!bulletsGrid) return;

    if (bulletsContent) bulletsContent.style.display = 'none';
    bulletsGrid.style.display = 'block';
    if (bulletsBadge) {
      bulletsBadge.textContent = 'Complete';
      bulletsBadge.className = 'card-badge badge-ok';
    }

    // Render yay bullets
    const yayContainer = $('#yayBullets');
    if (yayContainer && res.yay) {
      yayContainer.innerHTML = res.yay.map((b, i) => `
        <div class="bullet-card bullet-yay" style="animation-delay:${i * 0.1}s">
          <div class="bullet-num">${i + 1}</div>
          <div class="bullet-body">
            <div class="bullet-title">${escHtml(b.title)}</div>
            <div class="bullet-detail">${escHtml(b.detail)}</div>
            <div class="bullet-metric">📊 ${escHtml(b.metric)}</div>
          </div>
        </div>
      `).join('');
    }

    // Render nay bullets
    const nayContainer = $('#nayBullets');
    if (nayContainer && res.nay) {
      nayContainer.innerHTML = res.nay.map((b, i) => `
        <div class="bullet-card bullet-nay" style="animation-delay:${i * 0.1}s">
          <div class="bullet-num">${i + 1}</div>
          <div class="bullet-body">
            <div class="bullet-title">${escHtml(b.title)}</div>
            <div class="bullet-detail">${escHtml(b.detail)}</div>
            <div class="bullet-metric">📊 ${escHtml(b.metric)}</div>
          </div>
        </div>
      `).join('');
    }
  } catch (e) {
    console.warn('Failed to load bullets:', e);
  }
}

function escHtml(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ── Animation Loading ────────────────────────────────────────────
function loadAnimation(ticker) {
  const videoUrl = `/api/video/${ticker}`;
  fetch(cacheBust(videoUrl), { method: 'HEAD' })
    .then(res => {
      if (res.ok) {
        const videoCard = $('#overviewVideoCard');
        const video = $('#animationVideo');
        const downloadLink = $('#videoDownloadLink');

        if (videoCard) videoCard.style.display = 'block';
        if (video) {
          video.querySelector('source').src = videoUrl + '?t=' + Date.now();
          video.load();
        }
        if (downloadLink) downloadLink.href = videoUrl;
      }
    })
    .catch(() => {});
}

// ── Run Log ───────────────────────────────────────────────────────────
async function refreshRunLog() {
  if (!currentTicker) return;
  try {
    const res = await api(`/api/runlog/${currentTicker}`);
    if (res && res.content) {
      const area = $('#runLogArea');
      let html = res.content
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
      html = html.replace(/^(\[.*?\]) (CLAUDE OK.*)/gm, '<span class="log-ok">$1 $2</span>');
      html = html.replace(/^(\[.*?\]) (CLAUDE FAIL.*)/gm, '<span class="log-err">$1 $2</span>');
      html = html.replace(/^(\[.*?\]) (CLAUDE TIMEOUT.*)/gm, '<span class="log-err">$1 $2</span>');
      html = html.replace(/^(\[.*?\]) (CLAUDE EXCEPTION.*)/gm, '<span class="log-err">$1 $2</span>');
      html = html.replace(/^(\[.*?\]) (CLAUDE CALL.*)/gm, '<span class="log-step">$1 $2</span>');
      html = html.replace(/^(\[.*?\]) (STEP:.*)/gm, '<span class="log-step">$1 $2</span>');
      html = html.replace(/^(\[.*?\]) (✓.*)/gm, '<span class="log-ok">$1 $2</span>');
      html = html.replace(/^(\[.*?\]) (✗.*)/gm, '<span class="log-err">$1 $2</span>');
      html = html.replace(/^(\[.*?\]) (PIPELINE START.*)/gm, '<span class="log-step">$1 $2</span>');
      html = html.replace(/^(\[.*?\]) (PIPELINE COMPLETED.*)/gm, '<span class="log-ok">$1 $2</span>');
      html = html.replace(/^(\[.*?\]) (PIPELINE ERROR.*)/gm, '<span class="log-err">$1 $2</span>');
      html = html.replace(/^(\[.*?\]) (={10,})/gm, '<span class="log-info">$1 $2</span>');
      area.innerHTML = html;
      area.scrollTop = area.scrollHeight;
    }
  } catch (e) {
    // silently ignore
  }
}

// ── Init ──────────────────────────────────────────────────────────────
// ── URL Hash Routing ────────────────────────────────────────────────
function getTickerFromHash() {
  const hash = window.location.hash.replace(/^#\/?!?/, '').trim().toUpperCase();
  return hash || null;
}

function setUrlHash(ticker) {
  if (ticker) {
    const newHash = `#${ticker}`;
    if (window.location.hash !== newHash) {
      history.pushState(null, '', newHash);
    }
    document.title = `${ticker} — Stock Analyst`;
  } else {
    if (window.location.hash) history.pushState(null, '', window.location.pathname);
    document.title = 'Stock Analyst';
  }
}

// ── Chat Tab ─────────────────────────────────────────────────────────────────
const chatHistory = [];

function renderMarkdown(text) {
  // Markdown → HTML renderer for chat bubbles (with table support)
  let h = text;

  // Code blocks
  h = h.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>');
  // Inline code
  h = h.replace(/`([^`]+)`/g, '<code>$1</code>');

  // Tables: detect lines with | separators
  h = h.replace(/((?:^\|.+\|$\n?){2,})/gm, (block) => {
    const lines = block.trim().split('\n').filter(l => l.trim());
    if (lines.length < 2) return block;
    const parseRow = (line) => line.split('|').slice(1, -1).map(c => c.trim());
    const headers = parseRow(lines[0]);
    // Skip separator line (line with dashes)
    const dataStart = (lines[1] && /^[\s|:-]+$/.test(lines[1])) ? 2 : 1;
    const thCells = headers.map(h => `<th>${h}</th>`).join('');
    const rows = lines.slice(dataStart).map(line => {
      const cells = parseRow(line).map(c => `<td>${c}</td>`).join('');
      return `<tr>${cells}</tr>`;
    }).join('');
    return `<table><thead><tr>${thCells}</tr></thead><tbody>${rows}</tbody></table>`;
  });

  // Headers
  h = h.replace(/^#### (.+)$/gm, '<h4>$1</h4>');
  h = h.replace(/^### (.+)$/gm, '<h3>$1</h3>');
  h = h.replace(/^## (.+)$/gm, '<h2>$1</h2>');
  h = h.replace(/^# (.+)$/gm, '<h1>$1</h1>');
  // Bold
  h = h.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  // Italic
  h = h.replace(/\*(.+?)\*/g, '<em>$1</em>');
  // Blockquotes
  h = h.replace(/^> (.+)$/gm, '<blockquote>$1</blockquote>');
  // Unordered lists
  h = h.replace(/^- (.+)$/gm, '<li>$1</li>');
  h = h.replace(/((?:<li>.*<\/li>\n?)+)/g, '<ul>$1</ul>');
  // Horizontal rule
  h = h.replace(/^---$/gm, '<hr>');
  // Line breaks → paragraphs
  h = h.replace(/\n\n/g, '</p><p>');
  h = h.replace(/\n/g, '<br>');
  return '<p>' + h + '</p>';
}

function appendChatMsg(role, content) {
  const container = $('#chatMessages');
  const div = document.createElement('div');
  div.className = `chat-msg ${role}`;
  const bubble = document.createElement('div');
  bubble.className = 'chat-bubble';
  if (role === 'assistant') {
    bubble.innerHTML = renderMarkdown(content);
  } else {
    bubble.textContent = content;
  }
  div.appendChild(bubble);
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

function showTypingIndicator() {
  const container = $('#chatMessages');
  const div = document.createElement('div');
  div.className = 'chat-msg assistant';
  div.id = 'chatTyping';
  div.innerHTML = '<div class="chat-typing"><div class="chat-typing-dot"></div><div class="chat-typing-dot"></div><div class="chat-typing-dot"></div></div>';
  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

function removeTypingIndicator() {
  const el = $('#chatTyping');
  if (el) el.remove();
}

async function sendChat() {
  const input = $('#chatInput');
  const message = input.value.trim();
  if (!message) return;

  input.value = '';
  appendChatMsg('user', message);
  chatHistory.push({ role: 'user', content: message });

  showTypingIndicator();
  $('#chatSendBtn').disabled = true;

  try {
    const res = await fetch(cacheBust('/api/chat'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message,
        ticker: currentTicker || '',
        history: chatHistory.slice(-10),
      }),
    });
    const data = await res.json();
    removeTypingIndicator();

    if (data.error) {
      appendChatMsg('assistant', `⚠️ Error: ${data.error}`);
    } else {
      const response = data.response || 'No response received.';
      appendChatMsg('assistant', response);
      chatHistory.push({ role: 'assistant', content: response });
    }
  } catch (e) {
    removeTypingIndicator();
    appendChatMsg('assistant', `⚠️ Network error: ${e.message}`);
  }

  $('#chatSendBtn').disabled = false;
  $('#chatInput').focus();
}

function clearChat() {
  chatHistory.length = 0;
  const container = $('#chatMessages');
  container.innerHTML = '';
  appendChatMsg('assistant', 'Chat cleared. Ask me anything about ' + (currentTicker || 'a stock') + '!');
}

window.addEventListener('hashchange', () => {
  const ticker = getTickerFromHash();
  if (ticker && ticker !== currentTicker && !polling) {
    loadPastAnalysis(ticker);
  }
});

document.addEventListener('DOMContentLoaded', async () => {
  log('Dashboard loaded. Checking for running pipeline…', 'log-ok');
  setStatus('offline');

  // Add right-click re-run on tab buttons
  $$('.tab-btn[data-steps]').forEach(btn => {
    // Skip overview tab (select_company can't be re-run)
    if (btn.dataset.steps === 'select_company') return;
    btn.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      const steps = btn.dataset.steps.split(',');
      if (steps.length === 1) {
        rerunStep(steps[0]);
      } else {
        // For multi-step tabs, offer choice
        const choice = prompt(
          `Re-run which step for ${currentTicker}?\n` +
          steps.map((s, i) => `${i + 1}. ${s}`).join('\n') +
          '\n\nEnter number (or "all" for all):'
        );
        if (!choice) return;
        if (choice.trim().toLowerCase() === 'all') {
          steps.forEach(s => rerunStep(s));
        } else {
          const idx = parseInt(choice) - 1;
          if (idx >= 0 && idx < steps.length) rerunStep(steps[idx]);
        }
      }
    });
    btn.title = 'Right-click to re-run this step';
  });

  await loadCompanies();

  // Check if URL has a ticker hash (e.g. #NVDA)
  const hashTicker = getTickerFromHash();

  const data = await api('/api/status');
  if (data && data.state === 'running') {
    // Active pipeline takes priority
    currentTicker = data.ticker;
    currentDates = data.report_dates || [];

    $('#emptyState').style.display = 'none';
    $('#overviewGrid').style.display = 'grid';
    $('#heroBanner').style.display = 'flex';
    loadHeroLogo(data.ticker || null);
    if (data.ticker) _loadedFiles.add(`hero/${data.ticker}`);
    $('#heroName').textContent = data.company_name || data.ticker || '…';
    $('#heroMeta').textContent =
      `Report dates: ${currentDates.join(', ') || 'detecting…'}` +
      '  \u2022  Pipeline running\u2026';
    if (data.ticker) $('#tickerInput').value = data.ticker;

    const completed = data.completed_steps || [];
    const current = data.current_step || '';
    const failed = data.failed_steps || [];
    const stepOrder = [
      'select_company', 'research_company', 'get_reports', 'get_numbers',
      'extract_goals', 'analyze_tone', 'analyze_price', 'get_logo',
      'compare_reports', 'generate_report', 'ten_point_analysis', 'animate'
    ];
    stepOrder.forEach(s => {
      if (completed.includes(s)) setStepState(s, 'done');
      else if (failed.includes(s)) setStepState(s, 'error');
      else if (s === current) setStepState(s, 'active');
    });

    if (data.files && data.ticker) {
      await loadAvailableContent(data.files, data.ticker);
    }

    updateProgressBar(data);
    setStatus('pending');
    $('#runBtn').disabled = true;
    $('#autoBtn').disabled = true;
    log(`Reconnected to running pipeline for ${data.ticker}`, 'log-step');
    setUrlHash(data.ticker);
    startPolling();

  } else if (hashTicker) {
    // URL has a ticker — load it directly
    log(`Loading ${hashTicker} from URL…`, 'log-step');
    await loadPastAnalysis(hashTicker);

  } else if (data && data.state === 'done' && data.ticker) {
    // No hash but server has a completed analysis
    currentTicker = data.ticker;
    currentDates = data.report_dates || [];

    $('#emptyState').style.display = 'none';
    $('#overviewGrid').style.display = 'grid';
    $('#heroBanner').style.display = 'flex';
    loadHeroLogo(data.ticker || null);
    $('#heroName').textContent = data.company_name || data.ticker || '…';
    let initMeta = `Report dates: ${currentDates.join(', ') || 'detecting…'}`;
    if (data.completed_at) {
      initMeta += `  \u2022  Last analyzed: ${formatTimestamp(data.completed_at)}`;
    } else {
      initMeta += '  \u2022  Pipeline complete';
    }
    $('#heroMeta').textContent = initMeta;
    if (data.ticker) $('#tickerInput').value = data.ticker;

    const completed = data.completed_steps || [];
    const failed = data.failed_steps || [];
    const stepOrder = [
      'select_company', 'research_company', 'get_reports', 'get_numbers',
      'extract_goals', 'analyze_tone', 'analyze_price', 'get_logo',
      'compare_reports', 'generate_report', 'ten_point_analysis', 'animate'
    ];
    stepOrder.forEach(s => {
      if (completed.includes(s)) setStepState(s, 'done');
      else if (failed.includes(s)) setStepState(s, 'error');
    });

    updateProgressBar(data);
    setStatus('online');
    log(`Pipeline for ${data.ticker} already complete`, 'log-ok');
    if (data.ticker) loadAllContent(data.ticker, currentDates);
    setUrlHash(data.ticker);
    refreshRunLog();
  } else {
    log('Ready to analyze.', 'log-ok');
  }

  // Redraw chart on window resize
  window.addEventListener('resize', () => {
    if (ohlcData && $('#tab-price').classList.contains('active')) {
      drawChart(activeWindow);
    }
  });
});