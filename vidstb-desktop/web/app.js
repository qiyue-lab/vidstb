/* Vidstb 电脑版前端 */

const state = {
  config: {},
  ffmpeg: {},
  hardware: [],
  gpu: [],
  queue: { jobs: [], counts: {} },
  quickLinks: [],
  files: [],          // { path, probe, selected: [], loading, error }
  merge: {            // 视频合并：{ files: [], estimate: null, measured: null, estimating: false }
    files: [], estimate: null, measured: null, estimating: false,
  },
};

let eventSource = null;
const logCache = {};

/* ---------------- 工具 ---------------- */
const $ = (id) => document.getElementById(id);

// 401 = 需要密码（密码被改过、或者换了设备）：直接跳登录页
function handleUnauthorized(res) {
  if (res.status !== 401) return false;
  if (!String(location.pathname).startsWith('/login')) location.href = '/login';
  return true;
}

async function apiGet(url) {
  const res = await fetch(url);
  if (handleUnauthorized(res)) throw new Error('需要密码');
  return res.json();
}

async function apiPost(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  if (handleUnauthorized(res)) throw new Error('需要密码');
  return res.json();
}

async function apiDelete(url) {
  const res = await fetch(url, { method: 'DELETE' });
  if (handleUnauthorized(res)) throw new Error('需要密码');
  return res.json();
}

function toast(message, ms = 2600) {
  const el = $('toast');
  el.textContent = message;
  el.classList.remove('hidden');
  clearTimeout(el._timer);
  el._timer = setTimeout(() => el.classList.add('hidden'), ms);
}

function formatDuration(us) {
  const total = Math.floor(us / 1000000);
  if (!total) return '--';
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (v) => String(v).padStart(2, '0');
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

function formatEta(seconds) {
  if (seconds == null) return '';
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  if (m <= 0) return `剩余 ${s}s`;
  return `剩余 ${m}m${String(s).padStart(2, '0')}s`;
}

function formatSize(bytes) {
  if (!bytes) return '';
  const gb = bytes / 1073741824;
  if (gb >= 1) return `${gb.toFixed(2)} GB`;
  const mb = bytes / 1048576;
  if (mb >= 1) return `${mb.toFixed(1)} MB`;
  return `${Math.round(bytes / 1024)} KB`;
}

/* ---------------- 状态同步 ---------------- */
function applyState(data) {
  if (!data) return;
  state.config = data.config || state.config;
  state.ffmpeg = data.ffmpeg || state.ffmpeg;
  state.hardware = data.hardware || state.hardware;
  state.gpu = data.gpu || [];
  state.queue = data.queue || state.queue;
  state.quickLinks = data.quick_links || state.quickLinks;
  if (data.resources) {
    state.resources = data.resources;
    watchUpdateTask();
  }
  render();
}

/* 后台（自动定时 / 别的页面）跑完检查更新时，在这里弹提示 */
function watchUpdateTask() {
  const task = (state.resources || {}).update_task || {};
  if (!resUpdateWatchInit) {
    resUpdateWatchInit = true;
    resSeenUpdateTask = task.id || '';
    resChecking = task.status === 'running';
    return;
  }
  if (task.status === 'running') {
    resChecking = true;
    return;
  }
  resChecking = false;
  if (!task.id || task.id === resSeenUpdateTask) return;
  resSeenUpdateTask = task.id;
  const updated = (task.result || {}).updated || [];
  if (updated.length) {
    const names = updated.slice(0, 3).map((row) => row.title).join('、');
    toast(`${updated.length} 条资源有更新：${names}`, 5000);
  } else {
    toast(task.message || '更新检查完成', 3600);
  }
}

function formatAgo(ts) {
  if (!ts) return '';
  const seconds = Math.max(0, Date.now() / 1000 - ts);
  if (seconds < 60) return '刚刚';
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  return `${Math.floor(seconds / 86400)} 天前`;
}

function connect() {
  if (eventSource) return;
  try {
    eventSource = new EventSource('/api/events');
    eventSource.onmessage = (event) => {
      try {
        applyState(JSON.parse(event.data));
      } catch (e) { /* ignore */ }
    };
    eventSource.onerror = () => {
      eventSource.close();
      eventSource = null;
      setTimeout(connect, 3000);
    };
  } catch (e) {
    setTimeout(connect, 3000);
  }
}

async function refresh() {
  try {
    applyState(await apiGet('/api/state'));
  } catch (e) { /* ignore */ }
}

/* ---------------- 渲染 ---------------- */
function render() {
  renderFfmpeg();
  renderSettings();
  renderFiles();
  renderMerge();
  renderJobs();
  renderResources();
}

function renderFfmpeg() {
  const chip = $('ffmpegChip');
  const ff = state.ffmpeg || {};
  if (!ff.available) {
    chip.className = 'chip chip-err';
    chip.textContent = 'ffmpeg 未就绪：请在设置中指定目录';
    return;
  }
  chip.className = 'chip chip-ok';
  chip.textContent = `ffmpeg ${ff.version || ''}`.trim();

  const gpu = $('gpuChip');
  const usable = (state.hardware || []).filter((h) => h.available);
  const names = state.gpu || [];
  if (usable.length) {
    gpu.classList.remove('hidden');
    gpu.className = 'chip chip-ok';
    gpu.textContent = `显卡可用：${usable.map((h) => h.label).join(' / ')}`;
  } else if (names.length) {
    gpu.classList.remove('hidden');
    gpu.className = 'chip chip-muted';
    gpu.textContent = names[0] + '（未能启用硬件编码）';
  } else {
    gpu.classList.add('hidden');
  }
}

function renderSettings() {
  const c = state.config || {};
  const setVal = (id, value) => {
    const el = $(id);
    if (el && document.activeElement !== el) el.value = value;
  };
  setVal('encoderMode', c.encoder_mode || 'auto');
  setVal('crf', c.crf ?? 16);
  setVal('preset', c.preset || 'slow');
  setVal('hwBitrateRatio', c.hw_bitrate_ratio ?? 1.5);
  setVal('parallel', c.parallel ?? 1);
  setVal('container', c.container || 'mkv');
  setVal('outputDir', c.output_dir || '');
  setVal('mergeBitrate', c.merge_bitrate_mbps ?? 50);
  setVal('mergeAudioKbps', c.merge_audio_bitrate_kbps ?? 256);
  setVal('mergeContainer', c.merge_container || 'mp4');
  setVal('mergeFit', c.merge_fit || 'pad');
  setVal('mergePreset', c.merge_preset || 'fast');
  setVal('mergeOutputDir', c.output_dir || '');

  const select = $('hwEncoder');
  const current = c.hw_encoder || 'auto';
  const options = ['<option value="auto">自动选择</option>'];
  (state.hardware || []).forEach((item) => {
    const suffix = item.available ? '' : '（不可用）';
    options.push(`<option value="${item.id}">${item.label}${suffix}</option>`);
  });
  select.innerHTML = options.join('');
  select.value = current;

  const style = c.style || {};
  setVal('fontName', style.font_name || '');
  setVal('fontSize', style.font_size || 0);
  setVal('outline', style.outline || 0);
  setVal('marginV', style.margin_v || 0);
  setVal('primaryColour', style.primary_colour || '');
  $('stackSubtitles').checked = !!c.stack_subtitles;
  $('useEmbeddedFonts').checked = !!c.use_embedded_fonts;
}

function renderFiles() {
  const box = $('fileList');
  if (!state.files.length) {
    box.innerHTML = '<p class="empty">还没有选择视频。支持 MKV / MP4 / MOV 等容器，可一次导入多个文件批量处理。</p>';
    return;
  }
  box.innerHTML = state.files.map((file, index) => {
    const probe = file.probe;
    let body;
    if (file.loading) {
      body = '<p class="file-meta">正在读取视频信息…</p>';
    } else if (file.error || (probe && probe.error)) {
      body = `<p class="file-meta" style="color:var(--danger)">${file.error || probe.error}</p>`;
    } else if (probe) {
      const m = probe.metadata || {};
      const meta = `${m.width}×${m.height} · ${formatDuration(m.duration_us)} · ${Math.round(m.frame_rate || 0)} fps`;
      const tracks = (probe.subtitles || []).map((track) => {
        const checked = file.selected.includes(track.subtitle_index) ? 'checked' : '';
        const disabled = track.supported ? '' : 'disabled';
        const cls = track.supported ? '' : 'disabled';
        return `<label class="track ${cls}">
          <input type="checkbox" data-file="${index}" data-track="${track.subtitle_index}" ${checked} ${disabled}>
          <span>${escapeHtml(track.display_name)}</span>
        </label>`;
      }).join('');
      body = `<p class="file-meta">${meta}</p>
        <div class="track-list">${tracks || NO_SUBTITLE_HINT}</div>`;
    }
    return `<div class="file-card">
      <div class="file-card-head">
        <div>
          <div class="file-title">${escapeHtml(basename(file.path))}</div>
          ${body}
        </div>
        <button class="btn btn-sm btn-danger" data-remove="${index}">移除</button>
      </div>
    </div>`;
  }).join('');

  box.querySelectorAll('input[data-track]').forEach((input) => {
    input.addEventListener('change', () => {
      const fileIndex = Number(input.dataset.file);
      const track = Number(input.dataset.track);
      const file = state.files[fileIndex];
      if (!file) return;
      if (input.checked) {
        if (!file.selected.includes(track)) file.selected.push(track);
      } else {
        file.selected = file.selected.filter((v) => v !== track);
      }
    });
  });
  box.querySelectorAll('button[data-remove]').forEach((btn) => {
    btn.addEventListener('click', () => {
      state.files.splice(Number(btn.dataset.remove), 1);
      renderFiles();
    });
  });
}

/* ---------------- 视频合并 ---------------- */
const MERGE_W = 3840;
const MERGE_H = 2160;

function renderMerge() {
  const box = $('mergeList');
  const files = state.merge.files;

  if (!files.length) {
    box.innerHTML = '<p class="empty">还没有选择视频。选择两段以上，按顺序合并成一条 4K 60fps 视频。</p>';
  } else {
    box.innerHTML = files.map((file, index) => {
      let meta = '';
      if (file.loading) meta = '读取中…';
      else if (file.error) meta = file.error;
      else if (file.probe) {
        const m = file.probe.metadata || {};
        meta = `${m.width}×${m.height} · ${Math.round(m.frame_rate || 0)}fps · ${formatDuration(m.duration_us)}`;
      }
      let tags = '';
      if (file.probe && !file.error) {
        const m = file.probe.metadata || {};
        if (m.width < MERGE_W || m.height < MERGE_H) tags += '<span class="tag">将放大到 4K</span>';
        if (!m.has_audio) tags += '<span class="tag">无音轨·补静音</span>';
      }
      const cls = file.error ? 'merge-item bad' : 'merge-item';
      return `<div class="${cls}">
        <span class="idx">${index + 1}</span>
        <div class="mi-body">
          <div class="mi-name">${escapeHtml(basename(file.path))}</div>
          <div class="mi-meta">${escapeHtml(meta)} ${tags}</div>
        </div>
        <div class="mi-actions">
          <button class="icon-btn" data-mup="${index}" ${index === 0 ? 'disabled' : ''} title="上移">↑</button>
          <button class="icon-btn" data-mdown="${index}" ${index === files.length - 1 ? 'disabled' : ''} title="下移">↓</button>
          <button class="icon-btn" data-mdel="${index}" title="移除">✕</button>
        </div>
      </div>`;
    }).join('');

    box.querySelectorAll('[data-mup]').forEach((btn) =>
      btn.addEventListener('click', () => {
        const i = Number(btn.dataset.mup);
        if (i > 0) swapMerge(i, i - 1);
      }));
    box.querySelectorAll('[data-mdown]').forEach((btn) =>
      btn.addEventListener('click', () => {
        const i = Number(btn.dataset.mdown);
        if (i < state.merge.files.length - 1) swapMerge(i, i + 1);
      }));
    box.querySelectorAll('[data-mdel]').forEach((btn) =>
      btn.addEventListener('click', () => {
        state.merge.files.splice(Number(btn.dataset.mdel), 1);
        state.merge.measured = null;
        renderMerge();
        updateMergeEstimate();
      }));
  }

  const ready = files.filter((f) => f.probe && !f.error).length;
  const startBtn = $('mergeStartBtn');
  if (startBtn) startBtn.disabled = ready < 2;
  renderMergeEstimate();
}

function swapMerge(a, b) {
  const list = state.merge.files;
  const tmp = list[a];
  list[a] = list[b];
  list[b] = tmp;
  state.merge.measured = null;
  renderMerge();
  updateMergeEstimate();
}

function renderMergeEstimate() {
  const measured = state.merge.measured;
  const est = measured || state.merge.estimate;
  const files = state.merge.files;
  const ready = files.filter((f) => f.probe && !f.error);
  if ($('estClips')) $('estClips').textContent = String(files.length);

  if (!est || ready.length < 2) {
    if ($('estSize')) $('estSize').textContent = '--';
    if ($('estDuration')) $('estDuration').textContent = '--';
    if ($('estBitrate')) $('estBitrate').textContent = '--';
    if ($('estNote')) $('estNote').textContent = '';
    const box = $('estWarnings');
    if (box) box.classList.add('hidden');
    const hint = $('mergeStateHint');
    if (hint) hint.textContent = state.merge.estimating ? '正在计算…'
      : (ready.length === 1 ? '再选一段就能估算大小' : '');
    return;
  }

  $('estSize').textContent = formatSize(est.estimated_bytes);
  $('estDuration').textContent = formatDuration(est.total_duration_us);

  if (est.basis === 'measured') {
    $('estSizeLabel').textContent = '合并后预计文件大小（实测校准）';
    $('estBitrate').textContent = est.with_audio
      ? `实测 ${est.measured_bitrate_mbps} Mbps + ${state.config.merge_audio_bitrate_kbps} kbps`
      : `实测 ${est.measured_bitrate_mbps} Mbps（无音轨）`;
    const gap = est.target_bitrate_mbps
      ? `，为目标码率 ${est.target_bitrate_mbps} Mbps 的 ${Math.round(est.measured_bitrate_mbps / est.target_bitrate_mbps * 100)}%`
      : '';
    $('estNote').textContent = `已抽样 ${est.sample_count} 段实测${gap}。编码用 ${est.encoder}，实际体积仍可能有几个百分点浮动。`;
  } else {
    $('estSizeLabel').textContent = '合并后预计文件大小（上限估算）';
    $('estBitrate').textContent = est.with_audio
      ? `${est.bitrate_mbps} Mbps + ${est.audio_kbps} kbps`
      : `${est.bitrate_mbps} Mbps（无音轨）`;
    $('estNote').textContent = '画面简单的片段会被编码器自动省下码率，实际往往明显小于这个上限。点「实测校准」可得到更接近真实的值。';
  }

  const warnings = [];
  const clips = est.clips || state.merge.files
    .filter((f) => f.probe && !f.error)
    .map((f) => ({
      name: basename(f.path), error: '', has_audio: f.probe.metadata.has_audio,
      width: f.probe.metadata.width, height: f.probe.metadata.height,
    }));
  clips.filter((c) => c.error).forEach((c) => warnings.push(`${c.name}：${c.error}`));
  const silent = clips.filter((c) => !c.error && !c.has_audio).length;
  if (silent) warnings.push(`${silent} 段没有音轨，将自动补等长静音`);
  const small = clips.filter((c) => !c.error && (c.width < MERGE_W || c.height < MERGE_H)).length;
  if (small) {
    warnings.push(`${small} 段分辨率低于 4K，会被放大。放大只是满足 4K 规格，不会让画面变得更清晰`);
  }
  const box = $('estWarnings');
  box.innerHTML = warnings.map((w) => `<div>${escapeHtml(w)}</div>`).join('');
  box.classList.toggle('hidden', !warnings.length);
  if ($('mergeStateHint')) $('mergeStateHint').textContent = '';
}

let mergeEstTimer = null;
function updateMergeEstimate() {
  const files = state.merge.files.filter((f) => f.probe && !f.error);
  if (files.length < 2) {
    state.merge.estimate = null;
    state.merge.estimating = false;
    renderMergeEstimate();
    return;
  }
  state.merge.estimating = true;
  clearTimeout(mergeEstTimer);
  mergeEstTimer = setTimeout(async () => {
    try {
      const data = await apiPost('/api/merge/estimate', {
        items: files.map((f) => ({ path: f.path, probe: f.probe })),
      });
      if (data && data.ok) state.merge.estimate = data;
    } catch (e) { /* ignore */ }
    state.merge.estimating = false;
    renderMergeEstimate();
  }, 150);
}

async function addMergePaths(paths) {
  const fresh = paths.filter((path) => path && !state.merge.files.some((f) => f.path === path));
  if (!fresh.length) return;
  fresh.forEach((path) =>
    state.merge.files.push({ path, probe: null, loading: true, error: '' }));
  renderMerge();

  for (const path of fresh) {
    const file = state.merge.files.find((f) => f.path === path);
    try {
      const data = await apiGet('/api/probe?path=' + encodeURIComponent(path));
      if (data.error) file.error = data.error;
      else file.probe = data.probe;
    } catch (e) {
      file.error = '读取失败';
    }
    file.loading = false;
    renderMerge();
  }
  state.merge.measured = null;
  updateMergeEstimate();
}

async function measureMergeSize() {
  const files = state.merge.files.filter((f) => f.probe && !f.error);
  if (files.length < 2) {
    toast('至少需要两段可合并的视频');
    return;
  }
  const btn = $('mergeMeasureBtn');
  btn.disabled = true;
  const hint = $('mergeActionHint');
  try {
    const started = await apiPost('/api/merge/measure', {
      items: files.map((f) => ({ path: f.path, probe: f.probe })),
    });
    if (started.error) {
      toast(started.error);
      return;
    }
    for (let i = 0; i < 300; i += 1) {
      const task = await apiGet(`/api/merge/measure?id=${started.id}`);
      btn.textContent = task.status === 'error' ? '实测校准'
        : `校准中 ${task.progress || 0}%`;
      if (task.status === 'done') {
        state.merge.measured = task.result;
        toast(`实测完成：约 ${formatSize(task.result.estimated_bytes)}`);
        break;
      }
      if (task.status === 'error') {
        toast(`校准失败：${task.message || '未知错误'}`);
        break;
      }
      await new Promise((r) => setTimeout(r, 1000));
    }
  } catch (e) {
    toast('校准请求失败');
  } finally {
    btn.textContent = '实测校准';
    btn.disabled = false;
    if (hint) hint.textContent = '默认按目标码率算上限；点「实测校准」抽每段中间 2 秒真编码，得到更接近真实的大小。';
    renderMerge();
  }
}

async function startMerge() {
  const files = state.merge.files.filter((f) => f.probe && !f.error);
  if (files.length < 2) {
    toast('至少需要两段可合并的视频');
    return;
  }
  const btn = $('mergeStartBtn');
  btn.disabled = true;
  try {
    const data = await apiPost('/api/merge/jobs', {
      paths: files.map((f) => f.path),
      probes: files.map((f) => f.probe),
    });
    if (data.error) {
      toast(data.error);
      return;
    }
    toast(`已开始合并：${files.length} 段 → 3840×2160 60fps`);
    refresh();
  } catch (e) {
    toast('创建合并任务失败');
  } finally {
    btn.disabled = false;
    renderMerge();
  }
}

const STATUS_TEXT = {
  queued: '排队中', running: '转码中', done: '已完成', error: '失败', cancelled: '已取消',
};

// 探测成功但一条字幕轨都没有时的说明，避免用户以为是程序坏了
const NO_SUBTITLE_HINT = `<p class="file-meta">没有检测到内嵌字幕轨</p>
  <p class="file-meta hint">这个视频里确实没有字幕流，常见三种情况：<br>
  ① 字幕已经烧进画面了（硬字幕）——这种不用再处理；<br>
  ② 源片本身就没带字幕——需要找带内嵌字幕的原片；<br>
  ③ 字幕是同目录下单独的 .srt / .ass 文件——外挂字幕暂不支持，请换成带内嵌字幕的版本。<br>
  判断方法：用 PotPlayer / VLC 打开，右键「字幕」菜单里如果能看到字幕轨列表，才有得烧。</p>`;

function renderJobs() {
  const box = $('jobList');
  const jobs = (state.queue && state.queue.jobs) || [];
  const counts = (state.queue && state.queue.counts) || {};
  const parts = [];
  if (counts.running) parts.push(`${counts.running} 个进行中`);
  if (counts.queued) parts.push(`${counts.queued} 个排队`);
  if (counts.done) parts.push(`${counts.done} 个完成`);
  if (counts.error) parts.push(`${counts.error} 个失败`);
  if (counts.cancelled) parts.push(`${counts.cancelled} 个取消`);
  $('queueSummary').textContent = parts.length ? `（${parts.join(' · ')}）` : '';

  if (!jobs.length) {
    box.innerHTML = '<p class="empty">队列为空。</p>';
    return;
  }

  box.innerHTML = jobs.map((job) => {
    const status = job.status;
    const barClass = status === 'done' ? 'done' : (status === 'error' ? 'error' : '');
    const sub = [];
    if (job.encoder) sub.push(job.encoder);
    if (job.resolution) sub.push(job.resolution);
    if (job.speed) sub.push(`${job.speed} 倍速`);
    if (job.eta_seconds) sub.push(formatEta(job.eta_seconds));
    if (job.kind === 'merge' && job.estimated_bytes) {
      if (job.status === 'done' && job.size_bytes) {
        sub.push(`预估 ${formatSize(job.estimated_bytes)} → 实际 ${formatSize(job.size_bytes)}`);
      } else {
        sub.push(`预估 ${formatSize(job.estimated_bytes)}`);
      }
    } else if (job.size_bytes) {
      sub.push(formatSize(job.size_bytes));
    }

    let actions = '';
    if (status === 'running' || status === 'queued') {
      actions += `<button class="btn btn-sm" data-cancel="${job.id}">取消</button>`;
    }
    if (status === 'error' || status === 'cancelled') {
      actions += `<button class="btn btn-sm" data-retry="${job.id}">重试</button>`;
    }
    if (status !== 'running') {
      actions += `<button class="btn btn-sm btn-ghost" data-del="${job.id}">删除</button>`;
    }
    if (status === 'error') {
      actions += `<button class="btn btn-sm btn-ghost" data-log="${job.id}">日志</button>`;
    }
    if (status === 'done') {
      actions += `<button class="btn btn-sm btn-ghost" data-open="${job.id}">打开位置</button>`;
    }

    const logHtml = logCache[job.id]
      ? `<div class="log-box">${escapeHtml(logCache[job.id].join('\n'))}</div>` : '';

    const kindTag = job.kind === 'merge'
      ? '<span class="tag tag-merge">合并 → 4K60</span>' : '';

    return `<div class="job">
      <div class="job-head">
        <div style="min-width:0">
          <div class="job-name">${kindTag}${escapeHtml(job.name)}</div>
          <div class="job-sub">${escapeHtml(sub.join(' · '))}</div>
        </div>
        <div class="job-actions">
          <span class="status ${status}">${STATUS_TEXT[status] || status}</span>
          ${actions}
        </div>
      </div>
      <div class="bar ${barClass}"><div style="width:${job.progress}%"></div></div>
      <div class="job-sub">${escapeHtml(job.message || '')}</div>
      ${logHtml}
    </div>`;
  }).join('');

  box.querySelectorAll('button[data-cancel]').forEach((btn) =>
    btn.addEventListener('click', async () => {
      await apiPost(`/api/jobs/${btn.dataset.cancel}/cancel`, {});
      refresh();
    }));
  box.querySelectorAll('button[data-retry]').forEach((btn) =>
    btn.addEventListener('click', async () => {
      await apiPost(`/api/jobs/${btn.dataset.retry}/retry`, {});
      refresh();
    }));
  box.querySelectorAll('button[data-del]').forEach((btn) =>
    btn.addEventListener('click', async () => {
      await apiDelete(`/api/jobs/${btn.dataset.del}`);
      refresh();
    }));
  box.querySelectorAll('button[data-open]').forEach((btn) =>
    btn.addEventListener('click', async () => {
      const job = jobs.find((j) => j.id === btn.dataset.open);
      if (job) await apiPost('/api/open', { path: job.output });
    }));
  box.querySelectorAll('button[data-log]').forEach((btn) =>
    btn.addEventListener('click', async () => {
      const id = btn.dataset.log;
      if (logCache[id]) {
        delete logCache[id];
        renderJobs();
        return;
      }
      const data = await apiGet(`/api/jobs/log?id=${id}`);
      logCache[id] = data.log || ['（无日志）'];
      renderJobs();
    }));
}

function escapeHtml(text) {
  return String(text == null ? '' : text)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function basename(path) {
  const parts = String(path).split(/[\\/]/);
  return parts[parts.length - 1];
}

/* ---------------- 文件导入 ---------------- */
async function addPaths(paths) {
  const fresh = paths.filter((path) => path && !state.files.some((f) => f.path === path));
  if (!fresh.length) return;
  fresh.forEach((path) => state.files.push({ path, probe: null, selected: [], loading: true, error: '' }));
  renderFiles();

  for (const path of fresh) {
    const file = state.files.find((f) => f.path === path);
    try {
      const data = await apiGet('/api/probe?path=' + encodeURIComponent(path));
      if (data.error) {
        file.error = data.error;
        file.loading = false;
      } else {
        file.probe = data.probe;
        file.loading = false;
        const tracks = (data.probe.subtitles || []).filter((t) => t.supported);
        const defaultTrack = tracks.find((t) => t.default) || tracks[0];
        if (defaultTrack) file.selected = [defaultTrack.subtitle_index];
      }
    } catch (e) {
      file.error = '读取失败';
      file.loading = false;
    }
    renderFiles();
  }
}

async function addToQueue() {
  const items = [];
  state.files.forEach((file) => {
    if (!file.probe || !file.selected.length) return;
    items.push({
      path: file.path,
      subtitle_indexes: file.selected,
      probe: file.probe,
    });
  });
  if (!items.length) {
    toast('请先选择视频并勾选至少一条字幕');
    return;
  }
  const data = await apiPost('/api/jobs', { items });
  toast(`已加入队列：${(data.ids || []).length} 个任务`);
  refresh();
}

/* ---------------- 文件浏览弹窗 ---------------- */
const browser = {
  mode: 'file',
  path: '',
  selected: new Set(),
  onConfirm: null,
  entries: [],
};

async function openBrowser(mode, onConfirm) {
  browser.mode = mode;
  browser.selected.clear();
  browser.onConfirm = onConfirm;
  $('browserTitle').textContent = mode === 'dir' ? '选择目录' : '选择视频文件（可多选）';
  $('browserRecursive').parentElement.style.display = mode === 'dir' ? '' : 'none';
  $('browserModal').classList.remove('hidden');
  const links = $('browserLinks');
  links.innerHTML = (state.quickLinks || [])
    .map((link) => `<span class="link" data-path="${escapeHtml(link.path)}">${escapeHtml(link.name)}</span>`)
    .join('');
  links.querySelectorAll('.link').forEach((el) =>
    el.addEventListener('click', () => browseTo(el.dataset.path)));
  browseTo(browser.path || (state.quickLinks[0] ? state.quickLinks[0].path : ''));
}

async function browseTo(path) {
  browser.path = path;
  $('browserPath').value = path;
  const data = await apiGet('/api/fs/list?path=' + encodeURIComponent(path));
  browser.entries = data.entries || [];
  const list = $('browserList');
  if (data.error) {
    list.innerHTML = `<p class="empty">${escapeHtml(data.error)}</p>`;
    return;
  }
  const parentBtn = data.parent
    ? '<div class="browser-item" data-up="1"><span class="ico">↩</span><span>返回上级</span></div>' : '';
  list.innerHTML = parentBtn + browser.entries.map((entry, index) => {
    const icon = entry.is_dir ? '📁' : (entry.is_video ? '🎬' : '📄');
    const size = entry.is_dir ? '' : formatSize(entry.size);
    return `<div class="browser-item ${browser.selected.has(entry.path) ? 'selected' : ''}" data-index="${index}">
      <span class="ico">${icon}</span>
      <span>${escapeHtml(entry.name)}</span>
      <span class="size">${size}</span>
    </div>`;
  }).join('');

  const up = list.querySelector('[data-up]');
  if (up) up.addEventListener('click', () => browseTo(data.parent));

  list.querySelectorAll('[data-index]').forEach((el) =>
    el.addEventListener('click', () => {
      const entry = browser.entries[Number(el.dataset.index)];
      if (!entry) return;
      if (entry.is_dir) {
        browseTo(entry.path);
        return;
      }
      if (browser.mode === 'dir') return;
      if (!entry.is_video) return;
      if (browser.selected.has(entry.path)) browser.selected.delete(entry.path);
      else browser.selected.add(entry.path);
      el.classList.toggle('selected');
      $('browserHint').textContent = `已选择 ${browser.selected.size} 个文件`;
    }));
  $('browserHint').textContent = browser.mode === 'dir'
    ? `当前目录：${path}` : `已选择 ${browser.selected.size} 个文件`;
}

/* ---------------- Windows 原生文件选择框 ---------------- */
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function pickWithSystemDialog(onPick) {
  // 原生选择框是模态窗口，服务端放在后台线程跑，这里轮询结果，
  // 避免点击后请求一直挂着、界面看起来"没反应"。
  let started;
  try {
    started = await apiPost('/api/fs/pick', {});
  } catch (e) {
    toast('无法调用系统文件选择框，改用内置浏览器');
    return false;
  }
  const deadline = Date.now() + 100000;
  let hinted = false;
  while (Date.now() < deadline) {
    await sleep(700);
    let task;
    try {
      task = await apiGet('/api/fs/pick?id=' + encodeURIComponent(started.id));
    } catch (e) {
      continue;
    }
    if (task.status === 'running') {
      if (!hinted) {
        toast('已在本机打开文件选择框，可能被浏览器窗口挡住了…', 4000);
        hinted = true;
      }
      continue;
    }
    if (task.status === 'error') {
      toast(task.message || '系统文件选择框不可用，改用内置浏览器', 3600);
      return false;
    }
    if (task.files && task.files.length) {
      onPick(task.files);
      toast(`已添加 ${task.files.length} 个视频`);
    }
    return true;
  }
  toast('等待系统文件选择框超时，改用内置浏览器', 3600);
  return false;
}

/* ---------------- 网盘资源库 ---------------- */
const RES_GROUP_NONE = '__none__';   // 与后端 GROUP_NONE 保持一致
// 第二行筛选：显示当前分组下出现了哪些网盘，与后端 PROVIDER_LABELS 对齐
const RES_PROVIDER_LIST = [
  { id: 'quark', label: '夸克网盘' },
  { id: 'baidu', label: '百度网盘' },
  { id: 'other', label: '其它链接' },
];
const resFilter = { keyword: '', provider: '', status: '', tag: '', group: '', title: '', updates: '' };
const resFiles = { item: null, tree: [], files: [], filter: '', newPaths: new Set() };
const resSelected = new Set();
let resView = 'list';   // 'list' | 'trash'
let resTrash = [];
let resChecking = false;          // 是否正在跑「检查更新」
let resSeenUpdateTask = '';       // 已经提示过的检查任务，避免重复 toast
let resUpdateWatchInit = false;   // 首次拿到状态时只记 id，不弹提示

async function resReload() {
  // 只拉全量，筛选一律交给 filteredResItems() 在本地做。
  // 服务端 SSE 每 15 秒会推一份「不带筛选条件」的全量 resources 覆盖
  // state.resources，如果这里存的是已筛选结果，推送一到就被冲回全部，
  // 分组筛选就会莫名其妙失效。
  try {
    const data = await apiGet('/api/resources');
    state.resources = data;
    renderResources();
  } catch (e) { /* ignore */ }
}

function renderResUpdateBar() {
  const data = state.resources || {};
  if (resView === 'trash') {
    $('resUpdateBar').classList.add('hidden');
    return;
  }
  $('resUpdateBar').classList.remove('hidden');
  const updated = (data.stats || {}).updated || 0;
  const chip = $('resUpdatesChip');
  chip.textContent = updated ? `🆕 有更新 ${updated}` : '🆕 有更新 0';
  chip.classList.toggle('active', !!resFilter.updates);
  $('resAckAllBtn').classList.toggle('hidden', !updated);
  $('resCheckUpdatesBtn').textContent = resChecking ? '检查中…' : '检查更新';
  $('resCheckUpdatesBtn').disabled = resChecking;
  $('resStopCheckBtn').classList.toggle('hidden', !resChecking);

  const auto = !!(state.config || {}).auto_update_check;
  if ($('resAutoCheck').checked !== auto) $('resAutoCheck').checked = auto;
  const hours = String((state.config || {}).update_check_hours || 6);
  if ($('resAutoHours').value !== hours) $('resAutoHours').value = hours;
  $('resAutoHours').disabled = !auto;

  const task = data.update_task || {};
  let hint = '';
  if (resChecking && task.total) {
    hint = `${task.message || '正在检查…'}（${task.done || 0}/${task.total}）`;
  } else {
    const ago = formatAgo(data.last_update_check || 0);
    hint = ago ? `上次检查：${ago}` : '还没有检查过更新';
    if (auto) hint += ' · 已开启自动检查';
  }
  $('resUpdateHint').textContent = hint;
}

async function resCheckUpdates(ids) {
  if (resChecking) {
    toast('正在检查更新，请稍等');
    return;
  }
  const started = await apiPost('/api/resources/check-updates', { ids: ids || [] });
  if (!started.ok || !started.id) {
    toast('没有可检查的资源（只有夸克 / 百度链接支持）');
    return;
  }
  resChecking = true;
  resSeenUpdateTask = started.id;
  renderResUpdateBar();
  for (let i = 0; i < 300; i += 1) {
    await sleep(1000);
    const task = await apiGet('/api/resources/check-updates?id=' + encodeURIComponent(started.id));
    if (!task || !task.id) continue;
    if (task.status === 'running') {
      await resReload();
      continue;
    }
    resChecking = false;
    await resReload();
    const updated = (task.result || {}).updated || [];
    if (updated.length) {
      const names = updated.slice(0, 3).map((row) => row.title).join('、');
      toast(`${updated.length} 条资源有更新：${names}`, 5000);
    } else {
      toast(task.message || '检查完成，没有新文件', 3600);
    }
    return;
  }
  resChecking = false;
  renderResUpdateBar();
  toast('检查超时了，可以再点一次');
}

async function resStopCheck() {
  await apiPost('/api/resources/check-updates/stop', {});
  resChecking = false;
  await resReload();
  toast('已停止检查');
}

async function resAck(id) {
  const data = await apiPost('/api/resources/ack-update', { id });
  if (!data.ok) {
    toast('标记失败');
    return;
  }
  toast('已标记已读，下次再有新文件会重新提示');
  await resReload();
  if (resFiles.item && resFiles.item.id === id) {
    const fresh = findRes(id);
    if (fresh) openFiles(fresh);
  }
}

async function resAckAll() {
  const data = await apiPost('/api/resources/ack-update', { all: true });
  toast(`已把 ${data.count || 0} 条标记为已读`);
  await resReload();
}

async function resToggleAutoCheck(enabled, hours) {
  const patch = { auto_update_check: !!enabled };
  if (hours) patch.update_check_hours = Number(hours);
  await apiPost('/api/config', patch);
  await refresh();
  await resReload();
  toast(enabled ? `已开启自动检查（每 ${hours || 6} 小时）` : '已关闭自动检查');
}

function resGroups() {
  return (state.resources || {}).groups || [];
}

function resGroupName(id) {
  if (!id) return '';
  const hit = resGroups().find((g) => g.id === id);
  return hit ? hit.name : '';
}

/* 当前分组范围内的资源：只按 group 过滤，不受搜索词 / 状态 / 网盘影响。
   第二行的网盘列表只随分组变化，避免打字时按钮数量跳来跳去。*/
function resScopeItems() {
  const items = (state.resources || {}).items || [];
  const g = resFilter.group;
  if (g === RES_GROUP_NONE) return items.filter((i) => !i.group);
  if (g) return items.filter((i) => i.group === g);
  return items;
}

function resProviderCounts() {
  const scope = resScopeItems();
  const counts = {};
  scope.forEach((i) => {
    const p = i.provider || 'other';
    counts[p] = (counts[p] || 0) + 1;
  });
  return { total: scope.length, counts };
}

/* 切到别的分组后，原先选中的网盘可能在这个组里一条都没有。
   此时自动回到「全部」，否则会出现「点了分组却一片空白」。*/
function syncResProviderFilter() {
  if (resView === 'trash' || !resFilter.provider) return;
  const { counts } = resProviderCounts();
  if (counts[resFilter.provider]) return;
  resFilter.provider = '';
  const sel = $('resProviderFilter');
  if (sel) sel.value = '';
}

function renderResProviders() {
  const { total, counts } = resProviderCounts();
  let html = '<span class="filter-label">网盘</span>';
  html += `<button class="tag-chip${resFilter.provider ? '' : ' active'}" data-provider="">全部<span class="grp-meta">${total} 条</span></button>`;
  RES_PROVIDER_LIST.forEach((p) => {
    const n = counts[p.id] || 0;
    if (!n) return;   // 这个分组里没有的网盘就不显示
    html += `<button class="tag-chip${resFilter.provider === p.id ? ' active' : ''}" data-provider="${p.id}">${p.label}<span class="grp-meta">${n} 条</span></button>`;
  });
  $('resTagBar').innerHTML = html;
}

/* 第三行：当前分组下的标题名。同一部作品常同时存了夸克和百度两条，
   按标题去重后点一下就能把几个网盘的版本一起列出来。
   范围随已选网盘联动（选了夸克就只列夸克里的标题），但不随搜索词 /
   状态变化，避免打字时按钮跳来跳去。*/
function resTitleScopeItems() {
  const items = resScopeItems();
  const p = resFilter.provider;
  return p ? items.filter((i) => (i.provider || 'other') === p) : items;
}

function resTitleCounts() {
  const scope = resTitleScopeItems();
  const order = [];
  const counts = {};
  scope.forEach((i) => {
    const t = String(i.title || '').trim();
    if (!t) return;
    if (counts[t] === undefined) { counts[t] = 0; order.push(t); }
    counts[t] += 1;
  });
  return { total: scope.length, order, counts };
}

/* 切换分组 / 网盘后，原先选中的标题可能已不在范围里，回到「全部」 */
function syncResTitleFilter() {
  if (resView === 'trash' || !resFilter.title) return;
  const { counts } = resTitleCounts();
  if (counts[resFilter.title]) return;
  resFilter.title = '';
}

function renderResTitles() {
  const { total, order, counts } = resTitleCounts();
  let html = '<span class="filter-label">标题</span>';
  html += `<button class="tag-chip${resFilter.title ? '' : ' active'}" data-title="">全部<span class="grp-meta">${total} 条</span></button>`;
  order.forEach((t) => {
    const safe = escapeHtml(t);
    html += `<button class="tag-chip title-chip${resFilter.title === t ? ' active' : ''}" data-title="${safe}" title="${safe}">${safe}<span class="grp-meta">${counts[t]} 条</span></button>`;
  });
  $('resTitleBar').innerHTML = html;
}

function renderResGroups() {
  const data = state.resources || {};
  const groups = resGroups();
  const un = data.ungrouped || { count: 0, total_bytes: 0 };
  const chip = (value, label, meta, extra) =>
    `<button class="tag-chip${resFilter.group === value ? ' active' : ''}${extra || ''}" data-group="${escapeHtml(value)}">${label}${meta ? `<span class="grp-meta">${meta}</span>` : ''}</button>`;
  let html = chip('', '全部');
  html += chip(RES_GROUP_NONE, '未分组', `${un.count} 条`);
  groups.forEach((g) => {
    const meta = g.total_bytes
      ? `${g.count} 条 · ${formatSize(g.total_bytes)}`
      : `${g.count} 条`;
    html += chip(g.id, escapeHtml(g.name), meta);
  });
  html += '<button class="tag-chip add" id="resGroupAddBtn">＋ 新建分组</button>';
  if (resFilter.group && resFilter.group !== RES_GROUP_NONE && resGroupName(resFilter.group)) {
    html += '<button class="tag-chip" id="resGroupRenameBtn">重命名</button>';
    html += '<button class="tag-chip danger" id="resGroupDelBtn">删除分组</button>';
  }
  $('resGroupBar').innerHTML = html;
  syncGroupPickers();
}

function syncGroupPickers() {
  const options = (selected) =>
    `<option value=""${selected ? '' : ' selected'}>未分组</option>` +
    resGroups().map((g) => `<option value="${g.id}"${selected === g.id ? ' selected' : ''}>${escapeHtml(g.name)}</option>`).join('');
  if ($('resGroupPick')) $('resGroupPick').innerHTML = options('');
  if ($('resBulkGroup')) $('resBulkGroup').innerHTML = options('');
}

function renderResBulkBar() {
  const count = resSelected.size;
  $('resBulkBar').classList.toggle('hidden', count === 0);
  $('resBulkCount').textContent = count ? `已选 ${count} 条` : '';
}

async function resNewGroup() {
  const name = (window.prompt('新分组名称（例如：国漫、待搬运）') || '').trim();
  if (!name) return;
  const data = await apiPost('/api/groups', { name });
  if (!data.ok) {
    toast('分组已存在或名称为空');
    return;
  }
  toast(`已新建分组：${data.group.name}`);
  await resReload();
}

async function resRenameGroup() {
  const id = resFilter.group;
  const old = resGroupName(id);
  if (!old) return;
  const name = (window.prompt('分组新名称', old) || '').trim();
  if (!name || name === old) return;
  const data = await apiPost('/api/groups/rename', { id, name });
  if (!data.ok) {
    toast('重命名失败：可能重名了');
    return;
  }
  toast('分组已重命名');
  await resReload();
}

async function resDeleteGroup() {
  const id = resFilter.group;
  const name = resGroupName(id);
  if (!name) return;
  if (!window.confirm(`删除分组「${name}」？组里的资源不会被删除，只会回到「未分组」。`)) return;
  const data = await apiDelete('/api/groups/' + id);
  resFilter.group = '';
  await resReload();
  toast(`已删除分组，${data.moved || 0} 条资源回到未分组`);
}

async function resBulkMove() {
  if (!resSelected.size) return;
  const group = $('resBulkGroup').value || '';
  const data = await apiPost('/api/resources/move', { ids: [...resSelected], group });
  resSelected.clear();
  await resReload();
  toast(`已移动 ${data.moved || 0} 条到「${resGroupName(group) || '未分组'}」`);
}

/* 单一数据源：state.resources 永远保存服务端全量（SSE 也会推全量覆盖），
   筛选结果全部在这里按 resFilter 派生。这样无论推送重绘多少次，
   当前选中的分组 / 标签 / 搜索都不会被冲掉。*/
function filteredResItems() {
  const items = (state.resources || {}).items || [];
  const f = resFilter;
  const keyword = (f.keyword || '').trim().toLowerCase();
  return items.filter((item) => {
    if (f.provider && (item.provider || 'other') !== f.provider) return false;
    if (f.title && String(item.title || '').trim() !== f.title) return false;
    if (f.status && item.status !== f.status) return false;
    if (f.tag && !(item.tags || []).includes(f.tag)) return false;
    if (f.group === RES_GROUP_NONE) {
      if (item.group) return false;
    } else if (f.group && item.group !== f.group) {
      return false;
    }
    if (f.updates && !(item.update && item.update.new_count > 0)) return false;
    if (keyword) {
      const haystack = [item.title, item.url, item.note,
                        (item.tags || []).join(' ')].join(' ').toLowerCase();
      if (haystack.indexOf(keyword) === -1) {
        const files = ((item.scan || {}).files) || [];
        if (!files.slice(0, 500).some((row) =>
            String(row.path || '').toLowerCase().indexOf(keyword) !== -1)) {
          return false;
        }
      }
    }
    return true;
  });
}

function renderResources() {
  const data = state.resources || {};
  const stats = data.stats || {};
  const counts = stats.status || {};
  syncResProviderFilter();   // 分组切换后校正第二行的网盘选择
  syncResTitleFilter();      // 再校正第三行的标题选择（依赖上面的网盘结果）
  const items = filteredResItems();
  const filtering = !!(resFilter.keyword || resFilter.provider || resFilter.status
                       || resFilter.tag || resFilter.group || resFilter.updates);
  const summary = [];
  const total = stats.total || 0;
  if (filtering) summary.push(`筛选出 ${items.length} 条`);
  if (total) summary.push(`共 ${total} 条`);
  RES_STATUS.forEach((s) => {
    const n = counts[s.id] || 0;
    if (n) summary.push(`${s.label} ${n}`);
  });
  const updatedCount = (stats.updated || 0);
  if (updatedCount) summary.push(`${updatedCount} 条有更新`);
  const trashCount = data.trash_count || 0;
  $('resTrashCount').textContent = trashCount ? `(${trashCount})` : '';
  $('resTrashClearBtn').classList.toggle('hidden', resView !== 'trash' || !trashCount);
  if (data.restored_from_backup) {
    summary.push(`已从备份恢复 ${data.restored_from_backup} 条`);
  }
  $('resSummary').textContent = summary.join(' · ');

  if (resView === 'trash') {
    $('resGroupBar').innerHTML = '';
    $('resTitleBar').innerHTML = '';
    $('resBulkBar').classList.add('hidden');
    renderResUpdateBar();
    renderResTrash();
    // 切回列表时强制重绘，避免复用回收站的缓存标记
    $('resList').dataset.rendered = '__trash__';
    return;
  }
  renderResUpdateBar();
  renderResGroups();
  renderResBulkBar();

  // 第二行：当前分组下有哪些网盘（第一行分组栏保持不变）
  renderResProviders();
  // 第三行：当前分组下的标题名，按标题去重
  renderResTitles();

  const box = $('resList');
  const html = items.length
    ? items.map(resCard).join('')
    : '<p class="empty">' +
      (total ? '没有符合筛选条件的资源。' : '还没有保存任何网盘资源。把链接粘到上面即可。') +
      '</p>';
  // SSE 推送很频繁，内容没变就别重写 innerHTML，免得把正展开的下拉/勾选打断
  if (box.dataset.rendered !== html) {
    box.innerHTML = html;
    box.dataset.rendered = html;
  }
}

function renderResTrash() {
  const box = $('resList');
  $('resTagBar').innerHTML = `<p class="empty">下面是删掉的资源，可以恢复。
    清空回收站会彻底删除，无法找回。</p>`;
  if (!resTrash.length) {
    box.innerHTML = '<p class="empty">回收站是空的。</p>';
    return;
  }
  box.innerHTML = resTrash.map((item) => `
    <div class="res-item" data-id="${item.id}">
      <div class="res-main">
        <div class="res-title">${escapeHtml(item.title)}
          <span class="chip chip-${item.provider}">${escapeHtml(item.provider_label)}</span></div>
        <div class="res-url">${escapeHtml(item.url)}</div>
        ${item.passcode ? `<div class="res-meta">提取码 <code>${escapeHtml(item.passcode)}</code></div>` : ''}
      </div>
      <div class="res-actions">
        <button class="btn btn-primary" data-act="restore" data-id="${item.id}">恢复</button>
        <button class="btn btn-ghost danger" data-act="purge" data-id="${item.id}">彻底删除</button>
      </div>
    </div>`).join('');
}

async function resLoadTrash() {
  const data = await apiGet('/api/resources/trash');
  resTrash = data.items || [];
}

async function resSwitchView(view) {
  resView = view;
  if (view === 'trash') {
    await resLoadTrash();
  }
  await resReload();
  $('resTrashLabel').textContent = view === 'trash' ? '返回资源列表' : '回收站';
}

const RES_STATUS = [
  { id: 'updating', label: '在更' },
  { id: 'finished', label: '已完结' },
  { id: 'dead', label: '已失效' },
];

function resStatusOptions(current) {
  return RES_STATUS.map((s) =>
    `<option value="${s.id}"${current === s.id ? ' selected' : ''}>${s.label}</option>`).join('');
}

function resCard(item) {
  const scan = item.scan;
  const stats = (scan && scan.stats) || {};
  const badge = `<span class="chip chip-${item.provider}">${escapeHtml(item.provider_label)}</span>`;
  const statusChip = `<span class="status-chip s-${item.status}">${escapeHtml(item.status_label)}</span>`;
  const tags = (item.tags || []).map((t) => `<span class="tag-mini">${escapeHtml(t)}</span>`).join('')
    || '<span class="tag-mini tag-empty">＋ 添加标签</span>';
  let scanLine = '';
  if (scan && scan.ok) {
    scanLine = `<div class="res-scan">${stats.files || 0} 个文件 · ${stats.video_files || 0} 个视频 · ${escapeHtml(formatSize(stats.total_bytes || 0))}</div>`;
  } else if (scan && scan.error) {
    scanLine = `<div class="res-scan err">读取失败：${escapeHtml(scan.error)}</div>`;
  }
  const upd = item.update;
  const updateChip = upd
    ? `<span class="chip chip-update" title="有新文件">🆕 更新 ${upd.new_count}</span>`
    : '';
  let newLine = '';
  if (upd) {
    const names = (upd.new_files || []).slice(0, 3).map((f) => f.name || f.path);
    const more = (upd.new_files || []).length > names.length ? ' 等' : '';
    const size = upd.new_bytes ? ` · ${escapeHtml(formatSize(upd.new_bytes))}` : '';
    newLine = `<div class="res-new-files">新增${upd.new_count} 个${size}：${escapeHtml(names.join('、'))}${more}</div>`;
  }
  const ackBtn = upd
    ? `<button class="btn btn-ghost" data-act="ack" data-id="${item.id}">标记已读</button>`
    : '';
  const suggest = (item.suggested_title || '').trim();
  const suggestLine = (suggest && suggest !== (item.title || '').trim())
    ? `<div class="res-suggest">建议标题：
         <button class="link-btn" data-act="usesuggest" data-id="${item.id}"
           title="一键改成这个标题">${escapeHtml(suggest)}</button></div>`
    : '';
  const openBtn = `<button class="btn btn-ghost" data-act="open" data-id="${item.id}">在浏览器打开</button>`;
  const scanBtn = item.scannable
    ? `<button class="btn" data-act="scan" data-id="${item.id}">${scan && scan.ok ? '重新读取' : '读取文件'}</button>`
    : '';
  const viewBtn = (scan && scan.ok)
    ? `<button class="btn btn-primary" data-act="view" data-id="${item.id}">查看文件</button>`
    : '';
  const groupOptions = `<option value=""${item.group ? '' : ' selected'}>未分组</option>` +
    resGroups().map((g) => `<option value="${g.id}"${item.group === g.id ? ' selected' : ''}>${escapeHtml(g.name)}</option>`).join('');
  return `
    <div class="res-item" data-id="${item.id}">
      <label class="res-check"><input type="checkbox" data-act="check" data-id="${item.id}"
        ${resSelected.has(item.id) ? 'checked' : ''}></label>
      <div class="res-main">
        <div class="res-title">
          <span class="res-title-text" data-act="rename" data-id="${item.id}"
                title="点击修改标题">${escapeHtml(item.title)}</span>
          <span class="rename-icon" data-act="rename" data-id="${item.id}"
                title="修改标题">✏️</span>
          ${badge} ${statusChip} ${updateChip}
        </div>
        <div class="res-url" title="${escapeHtml(item.url)}">${escapeHtml(item.url)}</div>
        ${item.passcode ? `<div class="res-meta">提取码 <code>${escapeHtml(item.passcode)}</code>
          <button class="link-btn" data-act="copycode" data-id="${item.id}">复制</button></div>` : ''}
        <div class="res-tags" data-act="tags" data-id="${item.id}"
             title="点击添加 / 修改标签（逗号分隔）">${tags}</div>
        ${item.note ? `<div class="res-note">${escapeHtml(item.note)}</div>` : ''}
        ${scanLine}
        ${newLine}
        ${suggestLine}
      </div>
      <div class="res-actions">
        ${ackBtn}${viewBtn}${scanBtn}${openBtn}
        <button class="btn btn-ghost" data-act="edit" data-id="${item.id}">编辑</button>
        <select class="mini-select" data-act="group" data-id="${item.id}"
                title="移动到分组">${groupOptions}</select>
        <select class="mini-select" data-act="status" data-id="${item.id}"
                title="资源状态">${resStatusOptions(item.status)}</select>
        <button class="btn btn-ghost" data-act="copyurl" data-id="${item.id}">复制链接</button>
        <button class="btn btn-ghost" data-act="similar" data-id="${item.id}"
                title="去盘搜按这个标题找同类资源">🔍 找同类</button>
        <button class="btn btn-ghost danger" data-act="del" data-id="${item.id}">删除</button>
      </div>
    </div>`;
}

async function resAddResource() {
  const raw = $('resInput').value.trim();
  if (!raw) {
    toast('请先粘贴网盘链接');
    return;
  }
  // 一行一个链接：parse_link 只会取整段里的第一个，多个链接必须拆开提交
  const lines = raw.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
  const payload = {
    title: $('resTitle').value.trim(),
    tags: $('resTags').value.split(/[,，]/).map((s) => s.trim()).filter(Boolean),
    group: ($('resGroupPick') && $('resGroupPick').value) || '',
    status: ($('resStatusPick') && $('resStatusPick').value) || 'updating',
  };
  if (lines.length > 1) payload.texts = lines;
  else payload.text = lines[0] || raw;

  const data = await apiPost('/api/resources', payload);
  if (!data.ok) {
    toast(data.error || '添加失败');
    return;
  }
  $('resInput').value = '';
  $('resTitle').value = '';
  $('resTags').value = '';

  if (Array.isArray(data.items)) {
    const skipped = data.skipped ? `，跳过重复 ${data.skipped} 条` : '';
    if (!data.created) {
      toast(`没有新增：这 ${data.skipped} 条之前都添加过了`);
      await resReload();
      return;
    }
    toast(`已添加 ${data.created} 条${skipped}，正在后台读取文件列表…`);
    await resReload();
    // 批量不在这里轮询，后台任务跑完由 SSE 推送更新
    data.items.forEach((item) => {
      if (item.scannable) apiPost('/api/resources/scan', { id: item.id }).catch(() => {});
    });
    return;
  }
  toast(`已保存：${data.item.title}`);
  await resReload();
  if (data.item.scannable) resScan(data.item.id);
}

async function resScan(itemId) {
  const started = await apiPost('/api/resources/scan', { id: itemId });
  if (!started.ok) {
    toast(started.error || '无法启动解析');
    return;
  }
  toast('正在读取网盘文件列表…');
  for (let i = 0; i < 120; i += 1) {
    await new Promise((r) => setTimeout(r, 1000));
    const task = await apiGet('/api/resources/scan?id=' + encodeURIComponent(started.id));
    if (task.status === 'running') continue;
    if (task.status === 'error') {
      toast(task.message || '读取失败', 4000);
    } else {
      toast(task.message || '读取完成');
    }
    await resReload();
    if (task.status === 'done' && resFiles.item && resFiles.item.id === itemId) {
      openFiles(resFiles.item);
    }
    return;
  }
  toast('读取超时，请重试');
}

function findRes(id) {
  return ((state.resources || {}).items || []).find((item) => item.id === id) || null;
}

/* ---------------- 修改标题 ---------------- */

let resRenaming = false;

function startInlineTags(id, host) {
  if (resRenaming) return;
  const item = findRes(id);
  if (!item || !host) return;
  resRenaming = true;
  const original = (item.tags || []).slice();
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'res-tag-input';
  input.value = original.join(', ');
  input.placeholder = '逗号分隔，回车保存';
  host.textContent = '';
  host.appendChild(input);
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);

  let closed = false;
  const finish = async (save) => {
    if (closed) return;
    closed = true;
    resRenaming = false;
    const next = input.value.split(/[,，]/).map((s) => s.trim()).filter(Boolean);
    if (save && JSON.stringify(next) !== JSON.stringify(original)) {
      await apiPost('/api/resources/update', { id, patch: { tags: next } });
      await resReload();
      toast(next.length ? `标签已更新：${next.join('、')}` : '已清空标签');
      return;
    }
    await resReload();
  };
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      input.blur();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      closed = true;
      resRenaming = false;
      resReload();
    }
  });
  input.addEventListener('blur', () => { finish(true); });
}

function startInlineRename(id, host) {
  if (resRenaming) return;
  const item = findRes(id);
  if (!item) return;
  resRenaming = true;
  const original = item.title;
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'res-title-input';
  input.value = original;
  host.textContent = '';
  host.appendChild(input);
  input.focus();
  input.select();

  let closed = false;
  const finish = async (save) => {
    if (closed) return;
    closed = true;
    resRenaming = false;
    const value = input.value.trim();
    if (save && value && value !== original) {
      await apiPost('/api/resources/update', { id, patch: { title: value } });
      await resReload();
      toast('标题已更新');
      return;
    }
    await resReload();
  };
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      input.blur();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      closed = true;
      resRenaming = false;
      resReload();
    }
  });
  input.addEventListener('blur', () => { finish(true); });
}

const resEdit = { id: '' };

function openResEdit(item) {
  if (!item) return;
  resEdit.id = item.id;
  $('resEditTitle').textContent = '编辑资源';
  $('resEditName').value = item.title || '';
  $('resEditTags').value = (item.tags || []).join(', ');
  $('resEditGroup').innerHTML = `<option value=""${item.group ? '' : ' selected'}>未分组</option>` +
    resGroups().map((g) => `<option value="${g.id}"${item.group === g.id ? ' selected' : ''}>${escapeHtml(g.name)}</option>`).join('');
  $('resEditStatus').innerHTML = resStatusOptions(item.status);
  $('resEditNote').value = item.note || '';
  $('resEditCode').value = item.passcode || '';
  $('resEditUrl').value = item.url || '';
  const suggest = (item.suggested_title || '').trim();
  $('resEditSuggest').innerHTML = (suggest && suggest !== (item.title || '').trim())
    ? `<span class="muted">根据文件名的建议：</span>`
      + `<button class="link-btn" id="resEditUseSuggest">${escapeHtml(suggest)}</button>`
    : '';
  $('resEditHint').textContent = '';
  $('resEditModal').classList.remove('hidden');
  setTimeout(() => $('resEditName').focus(), 30);
}

function closeResEdit() {
  resEdit.id = '';
  $('resEditModal').classList.add('hidden');
}

async function resSaveEdit() {
  const id = resEdit.id;
  if (!id) return;
  const patch = {
    title: $('resEditName').value.trim(),
    tags: $('resEditTags').value.split(/[,，]/).map((s) => s.trim()).filter(Boolean),
    note: $('resEditNote').value.trim(),
    passcode: $('resEditCode').value.trim(),
    url: $('resEditUrl').value.trim(),
    group: $('resEditGroup').value || '',
    status: $('resEditStatus').value || 'updating',
  };
  const data = await apiPost('/api/resources/update', { id, patch });
  if (!data.ok) {
    toast(data.error || '保存失败');
    return;
  }
  closeResEdit();
  await resReload();
  toast('已保存');
}

function openFiles(item) {
  const scan = item.scan;
  if (!scan || !scan.ok) {
    toast('还没有读取到文件列表');
    return;
  }
  resFiles.item = item;
  resFiles.tree = scan.tree || [];
  resFiles.files = scan.files || [];
  resFiles.filter = '';
  resFiles.newPaths = new Set(((item.update || {}).new_files || []).map((f) => f.path));
  $('filesFilter').value = '';
  $('filesTitle').textContent = item.title || '文件列表';
  $('filesAckBtn').classList.toggle('hidden', !item.update);
  const stats = scan.stats || {};
  $('filesStats').textContent =
    `${stats.files || 0} 个文件 · ${stats.video_files || 0} 个视频 · ${formatSize(stats.total_bytes || 0)}`;
  const hints = [];
  if (stats.truncated) hints.push('文件较多，只读取了前一部分');
  if (stats.unexpanded_dirs) {
    hints.push(`还有 ${stats.unexpanded_dirs} 个文件夹没读出来`
      + '（百度这次没允许直接列子目录，补上提取码或点「在浏览器打开」查看）');
  }
  $('filesHint').textContent = hints.join('；');
  renderFilesTree();
  $('filesModal').classList.remove('hidden');
}

function closeFiles() {
  $('filesModal').classList.add('hidden');
}

function matchesFilter(node) {
  if (!resFiles.filter) return true;
  return node.name.toLowerCase().includes(resFiles.filter.toLowerCase());
}

function renderFilesTree() {
  const box = $('filesTree');
  const nodes = resFiles.tree || [];
  if (!nodes.length) {
    box.innerHTML = '<p class="empty">这个分享里没有文件。</p>';
    return;
  }
  const rows = [];
  const walk = (list, depth, prefixPath) => {
    list.forEach((node) => {
      const path = prefixPath ? `${prefixPath}/${node.name}` : node.name;
      const show = !resFiles.filter || path.toLowerCase().includes(resFiles.filter.toLowerCase())
        || hasMatch(node);
      if (!show) return;
      const indent = depth * 18;
      const icon = node.is_dir ? '📁' : (node.kind === 'video' ? '🎬' : (node.kind === 'subtitle' ? '📝' : '📄'));
      const size = node.is_dir ? '' : formatSize(node.size || 0);
      const isNew = !node.is_dir && resFiles.newPaths.has(path);
      rows.push(
        `<div class="ft-row${node.kind === 'video' ? ' is-video' : ''}" style="padding-left:${indent + 8}px">
           <span class="ft-icon">${icon}</span>
           <span class="ft-name" title="${escapeHtml(path)}">${escapeHtml(node.name)}</span>
           ${isNew ? '<span class="ft-new">NEW</span>' : ''}
           <span class="ft-size">${escapeHtml(size)}</span>
         </div>`);
      if (node.is_dir && node.children && node.children.length) {
        walk(node.children, depth + 1, path);
      }
    });
  };
  walk(nodes, 0, '');
  box.innerHTML = rows.join('') || '<p class="empty">没有匹配的文件。</p>';
}

function hasMatch(node) {
  const needle = resFiles.filter.toLowerCase();
  if (!needle) return true;
  if ((node.name || '').toLowerCase().includes(needle)) return true;
  return (node.children || []).some(hasMatch);
}

async function resCopyFiles() {
  const text = (resFiles.files || []).map((f) => f.path).join('\n');
  try {
    await navigator.clipboard.writeText(text);
    toast(`已复制 ${resFiles.files.length} 行文件清单`);
  } catch (e) {
    toast('复制失败，请手动选择文本');
  }
}

/* ---------------- 盘搜 ---------------- */
const psState = {
  query: '',
  platform: 'all',      // 'all' 或某个平台 key
  items: [],            // 当前展示的（已按平台/关键词过滤）
  all: [],              // 本次搜索拿到的全部
  shown: 0,
  related: [],
  loading: false,
  detail: null,         // 详情弹窗里的那条
  fileTask: '',         // 正在读文件的任务 id
  selected: new Set(),  // 勾选待批量保存的
  saved: {},            // share_key -> 资源库里的 id
  saving: null,         // 保存弹窗的目标：单条 item 或 {batch:true}
};

const PS_PAGE = 30;

function psIsSaved(item) {
  return Boolean(item && item.share_key && psState.saved[item.share_key]);
}

async function psLoadSaved() {
  try {
    const data = await apiGet('/api/pansou/saved');
    psState.saved = data.saved || {};
  } catch (e) {
    psState.saved = psState.saved || {};
  }
}

function psVisible() {
  const onlyMatch = $('psOnlyMatch') ? $('psOnlyMatch').checked : true;
  const hideSaved = $('psHideSaved') ? $('psHideSaved').checked : false;
  return psState.all.filter((item) => {
    if (psState.platform !== 'all' && item.platform !== psState.platform) return false;
    if (onlyMatch && !item.matched) return false;
    if (hideSaved && psIsSaved(item)) return false;
    return true;
  });
}

function psPlatformChips() {
  const counts = {};
  psState.all.forEach((i) => { counts[i.platform] = (counts[i.platform] || 0) + 1; });
  const keys = Object.keys(counts).sort((a, b) => counts[b] - counts[a]);
  $('psPlatformBar').innerHTML = keys.map((k) =>
    `<button class="tag-chip${psState.platform === k ? ' active' : ''}" data-ps-platform="${escapeHtml(k)}">`
    + `${escapeHtml(psPlatformName(k))} ${counts[k]}</button>`).join('');
  $('psPlatformAll').classList.toggle('active', psState.platform === 'all');
}

function psPlatformName(key) {
  return (psState.platformNames || {})[key] || key;
}

function psRenderRelated() {
  const words = psState.related || [];
  $('psRelatedBar').innerHTML = words.length
    ? `<span class="muted">相关搜索：</span>` + words.map((w) =>
        `<button class="tag-chip" data-ps-related="${escapeHtml(w)}">${escapeHtml(w)}</button>`).join('')
    : '';
}

function psRenderList(reset = true) {
  if (reset) {
    psState.items = psVisible();
    psState.shown = 0;
  }
  const list = $('psList');
  if (!psState.items.length) {
    list.innerHTML = psState.loading
      ? `<div class="empty">正在搜索…</div>`
      : `<div class="empty">${psState.query ? '没有符合条件的结果' : '输入片名开始搜索'}</div>`;
    $('psMoreBtn').classList.add('hidden');
    return;
  }
  const slice = psState.items.slice(0, psState.shown + PS_PAGE);
  psState.shown = slice.length;
  list.innerHTML = slice.map(psRow).join('');
  $('psMoreBtn').classList.toggle('hidden', psState.shown >= psState.items.length);
  const savedCount = psState.items.filter(psIsSaved).length;
  $('psSummary').textContent = savedCount
    ? `${psState.items.length} 条（${savedCount} 条已在我的资源）`
    : `${psState.items.length} 条`;
  if ($('psBulkBar')) $('psBulkBar').classList.toggle('hidden', !psState.items.length);
  psRenderBulkBar();
}

function psRenderBulkBar() {
  if (!$('psBulkBar')) return;
  const count = psState.selected.size;
  $('psBulkCount').textContent = count ? `已选 ${count} 条` : '勾选多条可批量保存';
}

function psToggleSel(id, on) {
  if (on) psState.selected.add(id); else psState.selected.delete(id);
  psRenderBulkBar();
}

function psSelectAll() {
  psState.items.forEach((item) => {
    if (!psIsSaved(item)) psState.selected.add(item.id);
  });
  psRenderList(false);
  psRenderBulkBar();
}

function psClearSel() {
  psState.selected.clear();
  psRenderList(false);
  psRenderBulkBar();
}

function psRow(item) {
  const code = item.password
    ? `<span class="ps-code" data-ps-act="code" data-ps-id="${item.id}" title="点击复制提取码">`
      + `提取码 ${escapeHtml(item.password)}</span>`
    : '';
  const source = [item.source, item.datetime].filter(Boolean).map(escapeHtml).join(' · ');
  const viewBtn = item.scannable
    ? `<button class="btn btn-primary" data-ps-act="view" data-ps-id="${item.id}">查看</button>`
    : `<button class="btn" data-ps-act="open" data-ps-id="${item.id}">打开链接</button>`;
  const saved = psIsSaved(item);
  const saveBtn = saved
    ? `<button class="btn btn-ghost is-saved" data-ps-act="gotosaved" data-ps-id="${item.id}"
         title="已经在「我的资源」里了，点击去查看">已保存 ✓</button>`
    : `<button class="btn" data-ps-act="save" data-ps-id="${item.id}">保存</button>`;
  return `
    <div class="res-item ps-item${saved ? ' is-saved' : ''}">
      <label class="res-check"${saved ? ' title="已经保存过了"' : ''}>
        <input type="checkbox" data-ps-act="check" data-ps-id="${item.id}"
          ${psState.selected.has(item.id) ? 'checked' : ''} ${saved ? 'disabled' : ''}>
      </label>
      <div class="res-main">
        <div class="res-title">
          <span class="res-title-text${item.scannable ? ' ps-title-link' : ''}"
                ${item.scannable ? `data-ps-act="view" data-ps-id="${item.id}" title="点击查看里面的文件"` : ''}
                >${escapeHtml(item.title)}</span>
          <span class="chip chip-${escapeHtml(item.platform)}">${escapeHtml(psPlatformName(item.platform))}</span>
          ${item.matched ? '<span class="ps-hit">命中</span>' : ''}
          ${saved ? '<span class="ps-saved-chip">已在我的资源</span>' : ''}
        </div>
        <div class="res-url" title="${escapeHtml(item.url)}">${escapeHtml(item.url)}</div>
        ${source ? `<div class="res-meta">${source}</div>` : ''}
        ${code ? `<div class="res-meta">${code}</div>` : ''}
      </div>
      <div class="res-actions">
        ${viewBtn}
        <button class="btn btn-ghost" data-ps-act="copy" data-ps-id="${item.id}">复制链接</button>
        ${saveBtn}
      </div>
    </div>`;
}

function psFind(id) {
  return psState.all.find((i) => i.id === id) || null;
}

async function psSearch(refresh = false) {
  const query = $('psInput').value.trim();
  if (!query) {
    toast('先输入要搜的片名');
    return;
  }
  psState.query = query;
  psState.loading = true;
  $('psStatus').textContent = '正在搜索…';
  $('psList').innerHTML = '<div class="empty">正在搜索…（盘搜一次要几秒）</div>';
  const data = await apiPost('/api/pansou/search',
    { query, platform: psState.platform === 'all' ? 'quark' : psState.platform, refresh });
  psState.loading = false;
  if (data.error || data.ok === false) {
    $('psStatus').textContent = '';
    $('psList').innerHTML = `<div class="empty">搜索失败：${escapeHtml(data.error || '未知错误')}</div>`;
    return;
  }
  psState.all = data.items || [];
  psState.related = data.related || [];
  psState.platformNames = Object.fromEntries(
    (data.items || []).map((i) => [i.platform, i.platform_label]));
  $('psStatus').textContent = data.cached ? '（缓存结果）' : '';
  psState.selected.clear();
  await psLoadSaved();
  psPlatformChips();
  psRenderRelated();
  psRenderList();
  psLoadRelated(query);
}

async function psLoadRelated(query) {
  psState.related = [];
  psRenderRelated();
  const data = await apiGet('/api/pansou/related?q=' + encodeURIComponent(query));
  const words = data.related || [];
  if (!words.length || psState.query !== query) return;
  psState.related = words;
  psRenderRelated();
}

/* ---- 保存到我的资源（单条 / 批量） ---- */
function openPsSave(target) {
  psState.saving = target;
  const batch = target && target.batch;
  const rows = batch ? psSelectedItems() : [target];
  if (!rows.length) {
    toast('先勾选要保存的结果');
    return;
  }
  $('psSaveTitle').textContent = batch ? `保存 ${rows.length} 条到我的资源` : '保存到我的资源';
  $('psSaveHint').textContent = batch
    ? `将保存 ${rows.length} 条选中的结果；标题留空就用各自搜索结果的标题。`
    : rows[0].title || '';
  $('psSaveName').value = batch ? '' : (rows[0].title || '');
  $('psSaveName').disabled = batch;
  $('psSaveName').placeholder = batch ? '（各自用搜索结果的标题）' : '留空就用搜索结果的标题';
  $('psSaveGroup').innerHTML = `<option value="" selected>未分组</option>` +
    resGroups().map((g) => `<option value="${g.id}">${escapeHtml(g.name)}</option>`).join('');
  $('psSaveStatus').innerHTML = resStatusOptions('updating');
  $('psSaveTags').value = '';
  $('psSaveMsg').textContent = '';
  $('psSaveGoLibrary').classList.add('hidden');
  $('psSaveModal').classList.remove('hidden');
}

function closePsSave() {
  psState.saving = null;
  $('psSaveModal').classList.add('hidden');
}

function psSelectedItems() {
  return psState.all.filter((i) => psState.selected.has(i.id) && !psIsSaved(i));
}

async function psSaveConfirm() {
  const target = psState.saving;
  if (!target) return;
  const batch = Boolean(target.batch);
  const rows = batch ? psSelectedItems() : [target];
  if (!rows.length) {
    toast('没有要保存的结果');
    return;
  }
  const group = $('psSaveGroup').value || '';
  const status = $('psSaveStatus').value || 'updating';
  const tags = $('psSaveTags').value.split(/[,，]/).map((s) => s.trim()).filter(Boolean);
  $('psSaveConfirm').disabled = true;
  $('psSaveMsg').textContent = '正在保存…';
  const payload = batch
    ? { items: rows.map((i) => ({ url: i.url, title: i.title, password: i.password })),
        group, status, tags }
    : { url: rows[0].url, title: $('psSaveName').value.trim() || rows[0].title,
        password: rows[0].password, group, status, tags };
  const data = await apiPost('/api/pansou/save', payload);
  $('psSaveConfirm').disabled = false;
  if (!data.ok) {
    $('psSaveMsg').textContent = data.error || '保存失败';
    return;
  }
  const created = batch ? (data.created || 0) : (data.created ? 1 : 0);
  const updated = batch ? (data.updated || 0) : (data.created ? 0 : 1);
  const parts = [];
  if (created) parts.push(`新建 ${created} 条`);
  if (updated) parts.push(`更新 ${updated} 条`);
  if (data.failed && data.failed.length) parts.push(`失败 ${data.failed.length} 条`);
  $('psSaveMsg').textContent = parts.join(' · ') || '已保存';
  $('psSaveGoLibrary').classList.remove('hidden');
  if (batch) psClearSel();
  await psLoadSaved();
  psRenderList();
  resReload();
  toast(parts.join(' · ') || '已保存');
}

async function psSave(item) {
  /* 快捷保存：直接按默认（在更、无分组）存一条 */
  const data = await apiPost('/api/pansou/save', {
    url: item.url, title: item.title, password: item.password,
  });
  if (data.ok) {
    toast(data.created ? '已保存到我的资源' : '我的资源里已有，已更新');
    await psLoadSaved();
    psRenderList();
    resReload();
  } else {
    toast(data.error || '保存失败');
  }
}

/* 资源库 -> 盘搜：拿标题去找同类 */
function resFindSimilar(item) {
  const title = (item && (item.title || '')).trim();
  switchTab('pansou');
  $('psInput').value = title;
  psState.platform = 'all';
  psState.selected.clear();
  if (title) psSearch(false);
}

function switchTab(name) {
  document.querySelectorAll('.tab').forEach((b) =>
    b.classList.toggle('active', b.dataset.tab === name));
  $('panel-subtitle').classList.toggle('hidden', name !== 'subtitle');
  $('panel-merge').classList.toggle('hidden', name !== 'merge');
  $('panel-resources').classList.toggle('hidden', name !== 'resources');
  $('panel-pansou').classList.toggle('hidden', name !== 'pansou');
  if (name === 'resources') resReload();
}

function openPsDetail(item) {
  psState.detail = item;
  $('psDetailTitle').textContent = item.title || '资源详情';
  $('psDetailPlatform').textContent = psPlatformName(item.platform);
  $('psDetailPlatform').className = `chip chip-${item.platform}`;
  $('psDetailSource').textContent =
    [item.source, item.datetime].filter(Boolean).join(' · ') || '';
  $('psDetailUrl').value = item.url;
  $('psCodeField').classList.toggle('hidden', !item.password);
  $('psDetailCode').value = item.password || '';
  $('psDetailHint').textContent = '';
  $('psFilesTree').innerHTML = '';
  $('psReloadFiles').classList.add('hidden');
  $('psModal').classList.remove('hidden');
  if (item.scannable) {
    psLoadFiles(item);
  } else {
    $('psFilesHint').textContent =
      '这个网盘还不支持直接读文件，点「浏览器打开」看内容，或先「保存到我的资源」。';
  }
}

function closePsDetail() {
  psState.detail = null;
  psState.fileTask = '';
  $('psModal').classList.add('hidden');
}

async function psLoadFiles(item, refresh = false) {
  const target = item || psState.detail;
  if (!target) return;
  $('psFilesHint').textContent = '正在读取文件清单…';
  $('psFilesTree').innerHTML = '';
  $('psReloadFiles').classList.add('hidden');
  const started = await apiPost('/api/pansou/files',
    { url: target.url, password: target.password, refresh });
  if (started.error) {
    $('psFilesHint').textContent = started.error;
    return;
  }
  psState.fileTask = started.id;
  for (let i = 0; i < 60; i += 1) {
    await new Promise((r) => setTimeout(r, 700));
    const task = await apiGet('/api/pansou/files?id=' + started.id);
    if (!task || task.status === 'idle') break;
    if (task.status === 'running') {
      $('psFilesHint').textContent = task.message || '正在读取…';
      continue;
    }
    if (task.status === 'error') {
      $('psFilesHint').textContent = `读取失败：${task.message}`;
      $('psReloadFiles').classList.remove('hidden');
      return;
    }
    const result = task.result || {};
    const stats = result.stats || {};
    $('psFilesHint').textContent =
      `${stats.files || 0} 个文件 · ${stats.video_files || 0} 个视频 · ${formatSize(stats.total_bytes || 0)}`;
    $('psFilesTree').innerHTML = psTreeRows(result.tree || []).join('');
    $('psReloadFiles').classList.remove('hidden');
    return;
  }
  $('psFilesHint').textContent = '读取超时了，点「重新读取」再试一次';
  $('psReloadFiles').classList.remove('hidden');
}

function psTreeRows(nodes, depth = 0, out = []) {
  nodes.forEach((node) => {
    const icon = node.is_dir ? '📁' : (node.kind === 'video' ? '🎬'
      : (node.kind === 'subtitle' ? '📝' : '📄'));
    const size = node.is_dir ? '' : formatSize(node.size || 0);
    out.push(
      `<div class="ft-row${node.kind === 'video' ? ' is-video' : ''}" style="padding-left:${depth * 18 + 8}px">
         <span class="ft-icon">${icon}</span>
         <span class="ft-name" title="${escapeHtml(node.path || node.name)}">${escapeHtml(node.name)}</span>
         <span class="ft-size">${escapeHtml(size)}</span>
       </div>`);
    if (node.children && node.children.length) psTreeRows(node.children, depth + 1, out);
  });
  return out;
}

/* ---------------- 事件绑定 ---------------- */
function bindEvents() {
  document.querySelectorAll('.tab').forEach((btn) =>
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach((b) => b.classList.toggle('active', b === btn));
      switchTab(btn.dataset.tab);
    }));

  /* ---- 盘搜 ---- */
  $('psSearchBtn').addEventListener('click', () => psSearch(false));
  $('psRefreshBtn').addEventListener('click', () => psSearch(true));
  $('psInput').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') psSearch(false);
  });
  $('psPlatformAll').addEventListener('click', () => {
    psState.platform = 'all';
    psPlatformChips();
    psRenderList();
  });
  $('psPlatformBar').addEventListener('click', (event) => {
    const chip = event.target.closest('[data-ps-platform]');
    if (!chip) return;
    psState.platform = chip.dataset.psPlatform;
    psPlatformChips();
    psRenderList();
  });
  $('psRelatedBar').addEventListener('click', (event) => {
    const chip = event.target.closest('[data-ps-related]');
    if (!chip) return;
    $('psInput').value = chip.dataset.psRelated;
    psSearch(false);
  });
  $('psOnlyMatch').addEventListener('change', () => psRenderList());
  if ($('psHideSaved')) $('psHideSaved').addEventListener('change', () => psRenderList());
  $('psMoreBtn').addEventListener('click', () => psRenderList(false));
  $('psSelectAllBtn').addEventListener('click', psSelectAll);
  $('psClearSelBtn').addEventListener('click', psClearSel);
  $('psSaveSelectedBtn').addEventListener('click', () => openPsSave({ batch: true }));
  $('psSaveClose').addEventListener('click', closePsSave);
  $('psSaveCancel').addEventListener('click', closePsSave);
  $('psSaveConfirm').addEventListener('click', psSaveConfirm);
  $('psSaveGoLibrary').addEventListener('click', () => {
    closePsSave();
    closePsDetail();
    switchTab('resources');
  });
  $('psSaveModal').addEventListener('click', (event) => {
    if (event.target === $('psSaveModal')) closePsSave();
  });
  $('psList').addEventListener('change', (event) => {
    const box = event.target.closest('[data-ps-act="check"]');
    if (!box) return;
    psToggleSel(box.dataset.psId, box.checked);
  });
  $('psList').addEventListener('click', async (event) => {
    const el = event.target.closest('[data-ps-act]');
    if (!el) return;
    const item = psFind(el.dataset.psId);
    if (!item) return;
    const act = el.dataset.psAct;
    if (act === 'check') return;       // 交给 change 处理
    if (act === 'view') { openPsDetail(item); return; }
    if (act === 'open') { window.open(item.url, '_blank'); return; }
    if (act === 'gotosaved') { switchTab('resources'); return; }
    if (act === 'copy') {
      try {
        await navigator.clipboard.writeText(item.password
          ? `${item.url} 提取码：${item.password}` : item.url);
        toast('链接已复制');
      } catch (e) { toast('复制失败'); }
      return;
    }
    if (act === 'code') {
      try {
        await navigator.clipboard.writeText(item.password || '');
        toast('提取码已复制');
      } catch (e) { toast('复制失败'); }
      return;
    }
    if (act === 'save') { openPsSave(item); return; }
  });
  $('psClose').addEventListener('click', closePsDetail);
  $('psCancel').addEventListener('click', closePsDetail);
  $('psModal').addEventListener('click', (event) => {
    if (event.target === $('psModal')) closePsDetail();
  });
  $('psCopyUrl').addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText($('psDetailUrl').value);
      toast('链接已复制');
    } catch (e) { toast('复制失败'); }
  });
  $('psCopyCode').addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText($('psDetailCode').value);
      toast('提取码已复制');
    } catch (e) { toast('复制失败'); }
  });
  $('psOpenUrl').addEventListener('click', () => {
    const url = $('psDetailUrl').value;
    if (url) window.open(url, '_blank');
  });
  $('psReloadFiles').addEventListener('click', () => psLoadFiles(psState.detail, true));
  $('psSave').addEventListener('click', () => {
    if (!psState.detail) return;
    openPsSave(psState.detail);
  });

  /* ---- 网盘资源库 ---- */
  $('resAddBtn').addEventListener('click', resAddResource);
  $('resInput').addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && event.ctrlKey) resAddResource();
  });
  let resSearchTimer = null;
  $('resSearch').addEventListener('input', () => {
    clearTimeout(resSearchTimer);
    resSearchTimer = setTimeout(() => {
      resFilter.keyword = $('resSearch').value.trim();
      resReload();
    }, 300);
  });
  $('resProviderFilter').addEventListener('change', () => {
    resFilter.provider = $('resProviderFilter').value;
    resReload();
  });
  $('resStatusFilter').addEventListener('change', () => {
    resFilter.status = $('resStatusFilter').value;
    resReload();
  });
  $('resTagBar').addEventListener('click', async (event) => {
    const chip = event.target.closest('[data-provider]');
    if (!chip) return;
    resFilter.provider = chip.dataset.provider || '';
    const sel = $('resProviderFilter');
    if (sel) sel.value = resFilter.provider;   // 与顶部「全部网盘」下拉保持一致
    renderResources();                          // 本地派生，先立刻响应
    await resReload();
  });
  $('resTitleBar').addEventListener('click', async (event) => {
    const chip = event.target.closest('[data-title]');
    if (!chip) return;
    resFilter.title = chip.dataset.title || '';
    renderResources();                          // 本地派生，先立刻响应
    await resReload();
  });

  /* 更新提示：检查 / 筛选 / 已读 / 自动检查 */
  $('resUpdatesChip').addEventListener('click', () => {
    resFilter.updates = resFilter.updates ? '' : '1';
    resReload();
  });
  $('resCheckUpdatesBtn').addEventListener('click', () => resCheckUpdates());
  $('resStopCheckBtn').addEventListener('click', resStopCheck);
  $('resAckAllBtn').addEventListener('click', resAckAll);
  $('resAutoCheck').addEventListener('change', () => {
    resToggleAutoCheck($('resAutoCheck').checked, $('resAutoHours').value);
  });
  $('resAutoHours').addEventListener('change', () => {
    resToggleAutoCheck($('resAutoCheck').checked, $('resAutoHours').value);
  });

  $('resList').addEventListener('click', async (event) => {
    const el = event.target.closest('[data-act]');
    if (!el) return;
    const id = el.dataset.id;
    const act = el.dataset.act;
    const item = findRes(id);
    if (act === 'status') return;
    if (act === 'scan') {
      resScan(id);
      return;
    }
    if (act === 'rename') {
      startInlineRename(id, event.target.closest('.res-title'));
      return;
    }
    if (act === 'tags') {
      startInlineTags(id, el);
      return;
    }
    if (act === 'edit') {
      openResEdit(item);
      return;
    }
    if (act === 'usesuggest') {
      const value = (item && item.suggested_title) || '';
      if (value) {
        await apiPost('/api/resources/update', { id, patch: { title: value } });
        await resReload();
        toast('标题已更新');
      }
      return;
    }
    if (act === 'view') {
      openFiles(item);
      return;
    }
    if (act === 'ack') {
      resAck(id);
      return;
    }
    if (act === 'open') {
      if (item) window.open(item.url, '_blank');
      return;
    }
    if (act === 'copyurl') {
      try {
        await navigator.clipboard.writeText((item && item.url) || '');
        toast('链接已复制');
      } catch (e) { toast('复制失败'); }
      return;
    }
    if (act === 'similar') {
      if (item) resFindSimilar(item);
      return;
    }
    if (act === 'copycode') {
      try {
        await navigator.clipboard.writeText((item && item.passcode) || '');
        toast('提取码已复制');
      } catch (e) { toast('复制失败'); }
      return;
    }
    if (act === 'del') {
      if (!window.confirm('确定删除这条资源？删除后可以在回收站里恢复。')) return;
      await apiDelete('/api/resources/' + id);
      await resReload();
      toast('已移入回收站');
    }
    if (act === 'restore') {
      const data = await apiPost('/api/resources/restore', { id });
      if (data.ok) {
        await resSwitchView('list');
        toast('已恢复');
      }
      return;
    }
    if (act === 'purge') {
      if (!window.confirm('彻底删除后无法找回，确定？')) return;
      await apiDelete('/api/resources/trash/' + id);
      await resSwitchView('trash');
      toast('已彻底删除');
    }
  });

  $('resTrashBtn').addEventListener('click', () => {
    resSwitchView(resView === 'trash' ? 'list' : 'trash');
  });
  $('resToPansouBtn').addEventListener('click', () => {
    const keyword = ($('resSearch').value || '').trim();
    switchTab('pansou');
    if (keyword) {
      $('psInput').value = keyword;
      psSearch(false);
    } else {
      $('psInput').focus();
    }
  });
  $('resTrashClearBtn').addEventListener('click', async () => {
    if (!resTrash.length) return;
    if (!window.confirm(`彻底删除回收站里的 ${resTrash.length} 条资源？无法找回。`)) return;
    await apiDelete('/api/resources/trash');
    await resSwitchView('list');
    toast('回收站已清空');
  });

  $('resList').addEventListener('change', async (event) => {
    const el = event.target.closest('[data-act="status"]');
    if (el) {
      await apiPost('/api/resources/update', { id: el.dataset.id, patch: { status: el.value } });
      await resReload();
      return;
    }
    const move = event.target.closest('[data-act="group"]');
    if (move) {
      await apiPost('/api/resources/update', { id: move.dataset.id, patch: { group: move.value } });
      await resReload();
      toast(`已移动到「${resGroupName(move.value) || '未分组'}」`);
      return;
    }
    const box = event.target.closest('[data-act="check"]');
    if (box) {
      if (box.checked) resSelected.add(box.dataset.id);
      else resSelected.delete(box.dataset.id);
      renderResBulkBar();
    }
  });

  $('resGroupBar').addEventListener('click', async (event) => {
    const chip = event.target.closest('[data-group]');
    if (chip) {
      resFilter.group = chip.dataset.group;
      resSelected.clear();
      renderResources();   // 立刻按新分组显示，不等网络往返
      await resReload();   // 再拉最新全量校正数据
      return;
    }
    if (event.target.id === 'resGroupAddBtn') resNewGroup();
    else if (event.target.id === 'resGroupRenameBtn') resRenameGroup();
    else if (event.target.id === 'resGroupDelBtn') resDeleteGroup();
  });

  $('resBulkMoveBtn').addEventListener('click', resBulkMove);
  $('resBulkClearBtn').addEventListener('click', () => {
    resSelected.clear();
    resReload();
  });

  $('resExportBtn').addEventListener('click', async () => {
    const data = await apiGet('/api/resources/export');
    const blob = new Blob([JSON.stringify(data.items || [], null, 2)],
      { type: 'application/json' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = 'vidstb-resources.json';
    link.click();
    URL.revokeObjectURL(link.href);
    toast(`已导出 ${(data.items || []).length} 条`);
  });

  $('resImportBtn').addEventListener('click', () => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json,application/json';
    input.addEventListener('change', async () => {
      const file = input.files && input.files[0];
      if (!file) return;
      try {
        const items = JSON.parse(await file.text());
        const data = await apiPost('/api/resources/import', { items });
        toast(`已导入 ${data.added || 0} 条`);
        await resReload();
      } catch (e) {
        toast('导入失败：不是有效的 JSON');
      }
    });
    input.click();
  });

  $('resEditSave').addEventListener('click', resSaveEdit);
  $('resEditCancel').addEventListener('click', closeResEdit);
  $('resEditClose').addEventListener('click', closeResEdit);
  $('resEditModal').addEventListener('click', (event) => {
    if (event.target === $('resEditModal')) closeResEdit();
    if (event.target.id === 'resEditUseSuggest') {
      $('resEditName').value = event.target.textContent;
    }
  });
  $('resEditModal').addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && event.target.tagName !== 'TEXTAREA') {
      event.preventDefault();
      resSaveEdit();
    } else if (event.key === 'Escape') {
      closeResEdit();
    }
  });
  $('resEditName').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); resSaveEdit(); }
  });

  $('filesClose').addEventListener('click', closeFiles);
  $('filesModal').addEventListener('click', (event) => {
    if (event.target === $('filesModal')) closeFiles();
  });
  $('filesFilter').addEventListener('input', () => {
    resFiles.filter = $('filesFilter').value.trim();
    renderFilesTree();
  });
  $('filesCopyBtn').addEventListener('click', resCopyFiles);
  $('filesAckBtn').addEventListener('click', () => {
    if (resFiles.item) resAck(resFiles.item.id);
  });
  $('filesReloadBtn').addEventListener('click', () => {
    if (resFiles.item) resScan(resFiles.item.id);
  });
  $('filesOpenBtn').addEventListener('click', () => {
    if (resFiles.item) window.open(resFiles.item.url, '_blank');
  });

  $('pickBtn').addEventListener('click', () => {
    openBrowser('file', (paths) => addPaths(paths));
  });

  $('nativePickBtn').addEventListener('click', async () => {
    const ok = await pickWithSystemDialog(addPaths);
    if (!ok) openBrowser('file', (paths) => addPaths(paths));
  });

  $('scanBtn').addEventListener('click', () => {
    openBrowser('dir', async (path) => {
      const recursive = $('browserRecursive').checked;
      const data = await apiPost('/api/fs/scan', { path, recursive });
      if (!data.files || !data.files.length) {
        toast('该目录下没有找到视频文件');
        return;
      }
      addPaths(data.files);
      toast(`扫描到 ${data.files.length} 个视频`);
    });
  });

  $('addSelectedBtn').addEventListener('click', addToQueue);

  $('mergePickBtn').addEventListener('click', () => {
    openBrowser('file', (paths) => addMergePaths(paths));
  });

  $('mergeNativePickBtn').addEventListener('click', async () => {
    const ok = await pickWithSystemDialog(addMergePaths);
    if (!ok) openBrowser('file', (paths) => addMergePaths(paths));
  });

  $('mergeScanBtn').addEventListener('click', () => {
    openBrowser('dir', async (path) => {
      const recursive = $('browserRecursive').checked;
      const data = await apiPost('/api/fs/scan', { path, recursive });
      if (!data.files || !data.files.length) {
        toast('该目录下没有找到视频文件');
        return;
      }
      addMergePaths(data.files);
      toast(`扫描到 ${data.files.length} 个视频`);
    });
  });

  $('mergeClearBtn').addEventListener('click', () => {
    state.merge.files = [];
    state.merge.estimate = null;
    state.merge.measured = null;
    renderMerge();
  });

  $('mergeStartBtn').addEventListener('click', startMerge);
  $('mergeMeasureBtn').addEventListener('click', measureMergeSize);

  $('mergePickOutputDirBtn').addEventListener('click', () => {
    openBrowser('dir', (path) => {
      $('mergeOutputDir').value = path;
      saveConfig({ output_dir: path });
    });
  });

  $('pathAddBtn').addEventListener('click', () => {
    const value = $('pathInput').value.trim();
    if (value) addPaths([value]);
    $('pathInput').value = '';
  });
  $('pathInput').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') $('pathAddBtn').click();
  });

  $('pickOutputDirBtn').addEventListener('click', () => {
    openBrowser('dir', (path) => {
      $('outputDir').value = path;
      saveConfig({ output_dir: path });
    });
  });

  $('toggleStyleBtn').addEventListener('click', () => {
    const panel = $('stylePanel');
    panel.style.display = panel.style.display === 'none' ? '' : 'none';
  });

  $('lanBtn').addEventListener('click', openLan);
  $('lanClose').addEventListener('click', closeLan);
  $('lanClose2').addEventListener('click', closeLan);
  $('lanModal').addEventListener('click', (e) => {
    if (e.target === $('lanModal')) closeLan();
  });
  $('lanCopy').addEventListener('click', () => {
    if (!lanState.current) return;
    copyText(lanState.current).then(() => toast('地址已复制')).catch(() => toast('复制失败，请手动选中'));
  });
  $('lanAccessToggle').addEventListener('change', async () => {
    const on = $('lanAccessToggle').checked;
    await saveConfig({ lan_access: on });
    toast(on ? '已开启，重启本工具后手机才能连上' : '已关闭，重启后只有本机能打开');
  });
  $('lanPwdSave').addEventListener('click', async () => {
    const pwd = $('lanPwd').value.trim();
    if (!pwd) { toast('请输入要设置的密码'); return; }
    await saveAccess({ password: pwd });
  });
  $('lanPwdClear').addEventListener('click', async () => {
    if (!(lanState.access && lanState.access.has_password)) { toast('本来就没设密码'); return; }
    await saveAccess({ password: '' });
  });
  $('lanIpsSave').addEventListener('click', async () => {
    await saveAccess({ allowed_ips: $('lanIps').value });
  });

  $('detectBtn').addEventListener('click', async () => {
    $('detectBtn').disabled = true;
    $('detectBtn').textContent = '检测中…';
    try {
      const data = await apiGet('/api/ffmpeg/detect?refresh=1');
      state.hardware = data.hardware || [];
      state.gpu = data.gpu || [];
      state.ffmpeg = data.ffmpeg || state.ffmpeg;
      render();
      const usable = (state.hardware || []).filter((h) => h.available);
      toast(usable.length ? `可用显卡编码：${usable.map((h) => h.label).join('、')}`
        : '没有可用的显卡编码器，将使用 CPU 编码');
    } finally {
      $('detectBtn').disabled = false;
      $('detectBtn').textContent = '检测显卡';
    }
  });

  $('stopQueueBtn').addEventListener('click', async () => {
    const btn = $('stopQueueBtn');
    btn.disabled = true;
    btn.textContent = '停止中…';
    try {
      const data = await apiPost('/api/queue/stop', {});
      const n = (data && data.stopped) || 0;
      toast(n ? `已停止：${n} 个任务正在中止` : '队列里没有需要停止的任务');
    } catch (e) {
      toast('停止失败，请重试');
    } finally {
      btn.disabled = false;
      btn.textContent = '停止队列';
      refresh();
    }
  });
  $('startQueueBtn').addEventListener('click', async () => {
    const data = await apiPost('/api/queue/start', {});
    toast(((data && data.resumed) || 0) ? `已重新开始 ${data.resumed} 个任务` : '没有可重新开始的任务');
    refresh();
  });
  $('clearFinishedBtn').addEventListener('click', async () => {
    await apiPost('/api/queue/clear', {});
    refresh();
  });

  $('browserClose').addEventListener('click', closeBrowser);
  $('browserCancel').addEventListener('click', closeBrowser);
  $('browserOk').addEventListener('click', () => {
    const result = browser.mode === 'dir' ? [browser.path] : Array.from(browser.selected);
    closeBrowser();
    if (browser.onConfirm) browser.onConfirm(result);
  });
  $('browserModal').addEventListener('click', (event) => {
    if (event.target === $('browserModal')) closeBrowser();
  });
  $('browserPath').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') browseTo($('browserPath').value.trim());
  });

  // 设置项
  const configFields = [
    ['encoderMode', 'encoder_mode', 'str'],
    ['hwEncoder', 'hw_encoder', 'str'],
    ['crf', 'crf', 'int'],
    ['preset', 'preset', 'str'],
    ['hwBitrateRatio', 'hw_bitrate_ratio', 'float'],
    ['parallel', 'parallel', 'int'],
    ['container', 'container', 'str'],
    ['outputDir', 'output_dir', 'str'],
    ['mergeBitrate', 'merge_bitrate_mbps', 'float'],
    ['mergeAudioKbps', 'merge_audio_bitrate_kbps', 'int'],
    ['mergeContainer', 'merge_container', 'str'],
    ['mergeFit', 'merge_fit', 'str'],
    ['mergePreset', 'merge_preset', 'str'],
    ['mergeOutputDir', 'output_dir', 'str'],
  ];
  configFields.forEach(([id, key, kind]) => {
    const el = $(id);
    const handler = () => {
      let value = el.value;
      if (kind === 'int') value = parseInt(value, 10) || 0;
      if (kind === 'float') value = parseFloat(value) || 0;
      saveConfig({ [key]: value });
      if (id.startsWith('merge')) {
        state.merge.measured = null;
        updateMergeEstimate();
      }
    };
    el.addEventListener('change', handler);
    if (el.tagName === 'INPUT') el.addEventListener('blur', handler);
  });

  const styleFields = [
    ['fontName', 'font_name', 'str'],
    ['fontSize', 'font_size', 'int'],
    ['outline', 'outline', 'float'],
    ['marginV', 'margin_v', 'int'],
    ['primaryColour', 'primary_colour', 'str'],
  ];
  styleFields.forEach(([id, key, kind]) => {
    const el = $(id);
    el.addEventListener('change', () => {
      let value = el.value;
      if (kind === 'int') value = parseInt(value, 10) || 0;
      if (kind === 'float') value = parseFloat(value) || 0;
      saveConfig({ style: Object.assign({}, state.config.style || {}, { [key]: value }) });
    });
  });

  ['stackSubtitles', 'useEmbeddedFonts'].forEach((id) => {
    $(id).addEventListener('change', () => {
      saveConfig({ [id === 'stackSubtitles' ? 'stack_subtitles' : 'use_embedded_fonts']: $(id).checked });
    });
  });
}

function closeBrowser() {
  $('browserModal').classList.add('hidden');
}

async function saveConfig(patch) {
  const data = await apiPost('/api/config', patch);
  if (data.config) state.config = data.config;
  if (data.ffmpeg) state.ffmpeg = data.ffmpeg;
  render();
}

/* ---------------- 手机访问（局域网） ---------------- */
const lanState = { info: null, current: '' };

function copyText(text) {
  // http://192.168.x.x 不是安全上下文，navigator.clipboard 可能不可用，降级到老办法
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text);
  }
  const box = document.createElement('textarea');
  box.value = text;
  box.style.position = 'fixed';
  box.style.opacity = '0';
  document.body.appendChild(box);
  box.select();
  try {
    document.execCommand('copy');
  } finally {
    document.body.removeChild(box);
  }
  return Promise.resolve();
}

function renderLanQr(url) {
  lanState.current = url;
  try {
    $('lanQr').innerHTML = window.QRCode.svg(url, { scale: 6, margin: 2 });
  } catch (err) {
    $('lanQr').innerHTML = '<p class="muted">地址太长，画不出二维码，请手动输入</p>';
  }
  Array.from(document.querySelectorAll('#lanUrls .lan-url')).forEach((el) => {
    el.classList.toggle('active', el.dataset.url === url);
  });
}

function renderTailscale(ts) {
  const box = $('lanTs');
  box.classList.remove('hidden');
  if (!ts) { box.innerHTML = ''; return; }
  if (ts.installed && ts.running && ts.url) {
    box.innerHTML =
      `<div class="lan-ts-title">走流量 · Tailscale 已连接</div>
       <button class="lan-url" data-url="${escapeHtml(ts.url)}">${escapeHtml(ts.url)}</button>
       <p class="hint">手机装 Tailscale 并登录<b>同一个账号</b>，之后不管 WiFi 还是流量都能打开这个地址。</p>`;
    Array.from(box.querySelectorAll('.lan-url')).forEach((el) => {
      el.addEventListener('click', () => renderLanQr(el.dataset.url));
    });
    return;
  }
  const steps =
    '<ol class="lan-steps">'
    + '<li>电脑上下载安装 Tailscale，登录账号</li>'
    + '<li>手机装 Tailscale App，登录<b>同一个账号</b></li>'
    + '<li>回到这里，就能看到 100 开头的地址，扫它即可</li>'
    + '</ol>';
  box.innerHTML =
    `<div class="lan-ts-title">走流量 · Tailscale${ts.installed ? '未连接' : '未安装'}</div>
     <p class="hint">${escapeHtml(ts.hint || '')}</p>
     <a class="btn btn-ghost btn-sm" href="${escapeHtml(ts.download || '')}"
        target="_blank" rel="noreferrer">下载 Tailscale</a>
     ${steps}`;
}

async function loadAccess() {
  try {
    const data = await apiGet('/api/access');
    lanState.access = data;
    $('lanPwdState').textContent = data.has_password
      ? '已经设了访问密码（原文不显示；改密码就填新的，所有设备要重新登录一次）'
      : '还没设密码：能连到这台电脑的人都能打开页面。建议设一个。';
    $('lanIps').value = (data.allowed_ips || []).join(', ');
    $('lanMyIp').textContent = data.client_ip || '-';
    $('lanIpsQuick').innerHTML = (data.suggested || [])
      .map((net) => `<button class="btn btn-ghost btn-sm" data-net="${net}">+ ${net}</button>`)
      .join('');
    Array.from($('lanIpsQuick').querySelectorAll('button')).forEach((el) => {
      el.addEventListener('click', () => {
        const parts = $('lanIps').value.split(/[,，]/).map((s) => s.trim()).filter(Boolean);
        if (!parts.includes(el.dataset.net)) parts.push(el.dataset.net);
        $('lanIps').value = parts.join(', ');
      });
    });
  } catch (err) {
    $('lanPwdState').textContent = '访问控制状态没读到，请确认服务在运行。';
  }
}

async function saveAccess(payload) {
  try {
    const data = await apiPost('/api/access', payload);
    if (!data.ok) { toast(data.error || '保存失败'); return; }
    lanState.access = data.access;
    $('lanPwdState').textContent = data.access.has_password
      ? '已经设了访问密码（原文不显示；改密码就填新的，所有设备要重新登录一次）'
      : '还没设密码：能连到这台电脑的人都能打开页面。建议设一个。';
    $('lanIps').value = (data.access.allowed_ips || []).join(', ');
    $('lanMyIp').textContent = data.access.client_ip || '-';
    $('lanPwd').value = '';
    toast('已保存，立即生效');
  } catch (err) {
    toast('保存失败');
  }
}

async function openLan() {
  $('lanModal').classList.remove('hidden');
  $('lanQr').innerHTML = '<p class="muted">正在获取地址…</p>';
  $('lanTip').textContent = '';
  $('lanUrls').innerHTML = '';
  $('lanTs').innerHTML = '';
  let info;
  try {
    info = await apiGet('/api/lan');
  } catch (err) {
    $('lanQr').innerHTML = '';
    $('lanTip').textContent = '没能连上本工具的服务，请确认它还在运行。';
    return;
  }
  lanState.info = info;
  $('lanAccessToggle').checked = info.lan_access !== false;
  renderTailscale(info.tailscale);
  loadAccess();

  if (info.local_only) {
    $('lanQr').innerHTML = '';
    $('lanTip').innerHTML = info.lan_access === false
      ? '已经关掉“允许手机访问”，现在只有本机 127.0.0.1 能用。<br>勾上上面的开关，再<b>重启本工具</b>就能用手机连。'
      : '现在是<b>只监听本机</b>的状态，手机连不上。<br>重新双击启动本工具即可监听局域网。';
    return;
  }
  if (!info.urls || !info.urls.length) {
    $('lanQr').innerHTML = '';
    $('lanTip').textContent = '没找到局域网地址，请确认电脑已经连上 WiFi 或网线。';
    return;
  }

  $('lanTip').textContent = '手机扫下面的码，或手动输入地址：';
  $('lanUrls').innerHTML = info.urls
    .map((url) => `<button class="lan-url" data-url="${url}">${url}</button>`)
    .join('');
  Array.from(document.querySelectorAll('#lanUrls .lan-url')).forEach((el) => {
    el.addEventListener('click', () => renderLanQr(el.dataset.url));
  });
  renderLanQr(info.urls[0]);
}

function closeLan() {
  $('lanModal').classList.add('hidden');
}

/* ---------------- 启动 ---------------- */
async function init() {
  bindEvents();
  await refresh();
  connect();
}

init();
