// frontend/js/studio.js — 分镜制作台
const { apiGet, apiPost, getSessionId, toast, esc } = window.NN;

const state = {
  sid:    getSessionId(),
  scenes: [],
  chars:  null,
  mv04:   null,
};

const $    = id => document.getElementById(id);
const show = id => $(id).classList.remove('hidden');
const hide = id => $(id).classList.add('hidden');

function setPill(step, status) {
  const p = document.querySelector(`.studio-pill[data-step="${step}"]`);
  if (!p) return;
  p.classList.remove('active', 'done');
  if (status) p.classList.add(status);
}

function setThink(on, label) {
  $('genThink').classList.toggle('hidden', !on);
  if (on && label) $('genThinkLabel').textContent = label;
}

// ───── 角色档案展示（来自 /pipeline/characters/{sid}）─────
function renderCharSummary() {
  const box = $('charSummary');
  const c = state.chars;
  if (!c || !c.main) {
    box.innerHTML = `<div class="text-muted">尚未读取到角色档案，请先完成方案确认台。</div>`;
    return;
  }
  const cards = [c.main, ...(c.supporting || [])];
  let html = `<div class="char-grid">`;
  cards.forEach(ch => {
    html += `<div class="char-card">
      <div class="char-name">${esc(ch.name)} <span style="font-size:.7rem;color:var(--gold);">· ${esc(ch.role_label || '')}</span></div>
      ${ch.description ? `<div class="char-meta">${esc(ch.description)}</div>` : ''}
    </div>`;
  });
  html += `</div>`;
  box.innerHTML = html;
}

// ───── 分镜卡片渲染 ─────
function renderScenes() {
  const list = $('sceneList');
  list.innerHTML = '';
  if (!state.scenes.length) {
    list.innerHTML = `<div class="text-muted">（暂无分镜）</div>`;
    return;
  }
  state.scenes.forEach((sc, i) => {
    const id   = sc.id || sc.scene_id || `S${String(i + 1).padStart(2, '0')}`;
    const time = sc.time || sc.duration || '';
    const desc = sc.description || sc.scene_desc || sc.prompt_global || sc.visual || '';
    const narr = sc.narration   || sc.voiceover  || sc.subtitle      || '';
    const imgStatus = sc._img_status || 'idle';
    const vidStatus = sc._vid_status || 'idle';

    const imgBadge =
      imgStatus === 'done' ? '<span class="badge badge-done">已生成</span>' :
      imgStatus === 'run'  ? '<span class="badge badge-run">生成中...</span>' :
      imgStatus === 'err'  ? '<span class="badge badge-err">失败</span>' :
                              '<span class="badge badge-idle">未生成</span>';
    const vidBadge =
      vidStatus === 'done' ? '<span class="badge badge-done">已生成</span>' :
      vidStatus === 'run'  ? '<span class="badge badge-run">生成中（约 1-3 分钟）</span>' :
      vidStatus === 'err'  ? '<span class="badge badge-err">失败</span>' :
                              '<span class="badge badge-idle">未生成</span>';

    let imgHtml;
    if (sc._img_url && sc._img_loaded) {
      imgHtml = `<div class="media-slot has-media">
           <img class="zoomable" data-idx="${i}" src="${esc(sc._img_url)}" alt="scene image">
           <div class="media-cap">点击图片放大查看</div>
         </div>`;
    } else if (sc._img_url) {
      imgHtml = `<div class="media-slot">${imgBadge}
           <button class="btn btn-sm" data-act="viewimg" data-idx="${i}" style="margin-top:6px;">查看图片</button>
         </div>`;
    } else {
      imgHtml = `<div class="media-slot"><div class="media-cap">画面图片</div>${imgBadge}</div>`;
    }

    let vidHtml;
    if (sc._vid_url && sc._vid_loaded) {
      vidHtml = `<div class="media-slot has-media" id="vidSlot${i}">
           <video controls preload="none" data-src="${esc(sc._vid_url)}" onerror="this.style.display='none';var m=this.parentElement.querySelector('.media-cap');if(m)m.textContent='视频加载失败，请尝试下载';"></video>
           <div class="media-cap" style="margin-top:6px;">
             <a class="btn btn-sm" href="${esc(sc._vid_url)}" download="scene-${String(i + 1).padStart(2, '0')}.mp4" target="_blank">下载视频</a>
           </div>
         </div>`;
    } else if (sc._vid_url) {
      vidHtml = `<div class="media-slot">${vidBadge}
           <button class="btn btn-sm" data-act="viewvid" data-idx="${i}" style="margin-top:6px;">查看视频</button>
         </div>`;
    } else {
      vidHtml = `<div class="media-slot"><div class="media-cap">短视频</div>${vidBadge}</div>`;
    }

    list.insertAdjacentHTML('beforeend', `
      <div class="scene-row" data-idx="${i}">
        <div class="scene-head">
          <div><span class="scene-num">${i + 1}</span><span class="scene-id">${esc(id)}</span></div>
          <div class="scene-meta">${time ? esc(time) : ''}</div>
        </div>
        ${desc ? `<div class="scene-desc">${esc(desc)}</div>` : ''}
        ${narr ? `<div class="scene-narr">${esc(narr)}</div>` : ''}
        <div class="scene-media">${imgHtml}${vidHtml}</div>
        <div class="scene-actions">
          <button class="btn" data-act="img" data-idx="${i}" ${imgStatus === 'run' ? 'disabled' : ''}>${sc._img_url ? '重新生成图片' : '生成图片'}</button>
          <button class="btn" data-act="vid" data-idx="${i}" ${vidStatus === 'run' || !sc._img_url ? 'disabled' : ''}>${sc._vid_url ? '重新生成视频' : '生成视频'}</button>
        </div>
      </div>
    `);
  });

  list.querySelectorAll('button[data-act]').forEach(btn => {
    const act = btn.dataset.act;
    btn.onclick = () => {
      const idx = +btn.dataset.idx;
      if (act === 'img') genSceneImage(idx, state.scenes[idx]._img_url ? true : false);
      else if (act === 'vid') genSceneVideo(idx, !!state.scenes[idx]._vid_url);
      else if (act === 'viewimg') {
        state.scenes[idx]._img_loaded = true;
        renderScenes();
      } else if (act === 'viewvid') {
        state.scenes[idx]._vid_loaded = true;
        renderScenes();
      }
    };
  });

  list.querySelectorAll('img.zoomable').forEach(img => {
    img.onclick = () => openLightbox(img.src);
  });
}

// ───── 图片 Lightbox（点击放大）─────
function openLightbox(src) {
  let bg = $('lightbox');
  if (!bg) {
    bg = document.createElement('div');
    bg.id = 'lightbox';
    bg.className = 'lightbox';
    bg.innerHTML = `<img class="lightbox-img" alt=""><button class="lightbox-close" type="button" aria-label="关闭">×</button>`;
    document.body.appendChild(bg);
    bg.addEventListener('click', e => {
      if (e.target === bg || e.target.classList.contains('lightbox-close')) closeLightbox();
    });
    document.addEventListener('keydown', e => { if (e.key === 'Escape') closeLightbox(); });
  }
  bg.querySelector('.lightbox-img').src = src;
  bg.classList.add('open');
  document.body.style.overflow = 'hidden';
}
function closeLightbox() {
  const bg = $('lightbox');
  if (bg) bg.classList.remove('open');
  document.body.style.overflow = '';
}

// ───── 生成 MV04 分镜 ─────
async function genScenes() {
  hide('phaseError');
  setPill('MV04', 'active');
  setThink(true, '念念正在编排分镜（约 30 秒）...');
  $('btnGenScenes').disabled = true;
  try {
    const res = await apiPost(`/pipeline/run/MV04/${state.sid}`, {});
    if (res.error) throw new Error(res.message || 'MV04 失败');
    state.mv04 = res.result;
    let scenes = [];
    if (Array.isArray(res.result.scenes)) scenes = res.result.scenes;
    else if (res.result.scenes && typeof res.result.scenes === 'object') {
      scenes = Object.keys(res.result.scenes).sort().map(k => res.result.scenes[k]);
    } else if (Array.isArray(res.result.storyboard)) scenes = res.result.storyboard;
    state.scenes = scenes.filter(x => x && typeof x === 'object');
    setPill('MV04', 'done');
    setThink(false);
    hide('phaseGenScenes');
    show('phaseScenes');
    renderScenes();
  } catch (e) {
    setThink(false);
    setPill('MV04', '');
    $('btnGenScenes').disabled = false;
    $('errorOutput').textContent = e.message || String(e);
    show('phaseError');
  }
}

// ───── 单镜图片生成 ─────
async function genSceneImage(idx, force = false) {
  const sc = state.scenes[idx];
  if (!sc) return;

  // 获取用户上传的照片列表（仅图片，不含视频）
  let reference_photo_url = '';
  try {
    const assetsRes = await apiGet(`/assets/list/${state.sid}`);
    const photos = (assetsRes.assets || []).filter(a =>
      a.filename && a.filename.match(/\.(jpg|jpeg|png|webp|gif)$/i)
    );
    if (photos.length > 0) {
      // 仅使用标记为"逝者"的照片，无标记则不传参考图
      const deceased = photos.find(p => p.subject === 'deceased');
      reference_photo_url = deceased ? deceased.url : '';
    }
  } catch (e) {
    console.warn('[genSceneImage] 获取照片列表失败：', e.message);
  }

  sc._img_status = 'run';
  sc._img_loaded = false;
  renderScenes();
  setPill('MV05', 'active');
  try {
    const payload = { force };
    if (reference_photo_url) {
      payload.reference_photo_url = reference_photo_url;
    }
    const res = await apiPost(`/pipeline/scene/image/${state.sid}/${idx}`, payload);
    if (res.error) throw new Error(res.message || '图片生成失败');
    sc._img_url    = res.url || res.image_url;
    sc._img_status = 'done';
    sc._img_loaded = true;
  } catch (e) {
    sc._img_status = 'err';
    toast('图片生成失败：' + e.message);
  } finally {
    renderScenes();
    if (state.scenes.length && state.scenes.every(s => s._img_url)) setPill('MV05', 'done');
  }
}

// ───── 单镜视频生成（异步提交 + 轮询）─────
async function genSceneVideo(idx, force = false) {
  const sc = state.scenes[idx];
  if (!sc || !sc._img_url) { toast('请先生成图片'); return; }

  sc._vid_status = 'run';
  sc._vid_url    = null;
  sc._vid_loaded = false;
  renderScenes();

  let taskId, taskSource;
  try {
    const res = await apiPost(`/pipeline/scene/video/${state.sid}/${idx}`, {
      image_url: sc._img_url,
      force: force,
    });
    if (res.error) throw new Error(res.message || '视频提交失败');

    // ── Playback 模式：视频已缓存，直接完成 ──
    if (res.cached && res.url) {
      sc._vid_url     = res.url;
      sc._vid_status  = 'done';
      sc._vid_poll    = null;
      sc._video_task_id = null;
      sc._vid_loaded  = true;
      toast(`分镜 ${idx + 1} 视频（缓存）已就绪`);
      renderScenes();
      return;
    }

    // ── 复用已有 task_id（参考图未变，任务仍在进行中） ──
    if (res.reused) {
      taskId     = res.task_id;
      taskSource = res.source || '';
      sc._vid_status = 'run';
    } else {
      // 新任务：清掉旧状态
      if (sc._vid_poll) { clearInterval(sc._vid_poll); sc._vid_poll = null; }
      sc._vid_status = 'run';
      sc._vid_url    = null;
      taskId     = res.task_id;
      taskSource = res.source || '';
      renderScenes();
    }
  } catch (e) {
    sc._vid_status = 'err';
    toast('视频提交失败：' + e.message);
    renderScenes();
    return;
  }

  sc._video_task_id     = taskId;
  sc._video_task_source = taskSource;

  startSceneVideoPoll(idx, taskId, taskSource);
}

// 恢复指定分镜的视频轮询（页面刷新后使用已有的 task_id）
function resumeSceneVideoPoll(idx) {
  const sc = state.scenes[idx];
  if (!sc || !sc._video_task_id) return;
  if (sc._vid_url) return; // 已有视频，不需要轮询

  sc._vid_status = 'run';
  startSceneVideoPoll(idx, sc._video_task_id, sc._video_task_source);
}

// 核心轮询逻辑（genSceneVideo 和 resumeSceneVideoPoll 共用）
function startSceneVideoPoll(idx, taskId, taskSource) {
  const sc = state.scenes[idx];
  if (sc._vid_poll) {
    clearInterval(sc._vid_poll);
    sc._vid_poll = null;
  }

  const MAX_POLLS = 120;
  let   pollCount = 0;

  sc._vid_poll = setInterval(async () => {
    if (state.scenes[idx] !== sc) {
      clearInterval(sc._vid_poll);
      sc._vid_poll = null;
      return;
    }

    pollCount++;
    if (pollCount > MAX_POLLS) {
      clearInterval(sc._vid_poll);
      sc._vid_poll   = null;
      sc._vid_status = 'err';
      toast(`视频生成超时（分镜 ${idx + 1}）`);
      renderScenes();
      return;
    }

    try {
      const r = await apiGet(
        `/pipeline/scene/video/status/${state.sid}/${idx}/${taskId}?source=${encodeURIComponent(taskSource)}&image_url=${encodeURIComponent(sc._img_url || '')}`
      );
      if (r.status === 'done') {
        clearInterval(sc._vid_poll);
        sc._vid_poll   = null;
        sc._vid_url    = r.url;
        sc._vid_status = 'done';
        sc._vid_loaded = true;
        renderScenes();
      } else if (r.status === 'failed') {
        clearInterval(sc._vid_poll);
        sc._vid_poll   = null;
        sc._vid_status = 'err';
        toast(`视频生成失败（分镜 ${idx + 1}）：` + (r.message || '未知错误'));
        renderScenes();
      }
    } catch (e) {
      console.warn(`[vid poll ${idx}] 轮询出错：`, e.message);
    }
  }, 10_000);
}

// ───── 最终合成 MV06 ─────
let mv06_poll = null;

// MV06 子步骤定义（顺序、显示名、进度百分比）
const MV06_STEPS = [
  { key: 'submitted',       label: '任务已提交',             pct: 0 },
  { key: 'approving',       label: '准备中…',                pct: 5 },
  { key: 'tts_running',     label: 'TTS语音合成中…',         pct: 10 },
  { key: 'tts_done',        label: '已完成TTS语音合成',       pct: 25 },
  { key: 'bgm_running',     label: 'BGM背景音乐匹配中…',      pct: 30 },
  { key: 'bgm_done',        label: '已完成BGM背景音乐匹配',   pct: 45 },
  { key: 'llm_running',     label: 'LLM生成影像脚本中…',      pct: 50 },
  { key: 'llm_done',        label: '已完成LLM影像脚本生成',   pct: 70 },
  { key: 'video_running',   label: '视频合成中…',             pct: 75 },
  { key: 'video_done',      label: '视频合成完成',            pct: 95 },
  { key: 'done',            label: '全部完成',               pct: 100 },
];

function renderMV06Steps(currentKey) {
  const container = $('mv06ProgressSteps');
  if (!container) return;
  const ci = MV06_STEPS.findIndex(s => s.key === currentKey);
  let html = '<div style="display:flex;flex-direction:column;gap:6px;">';
  MV06_STEPS.forEach((s, i) => {
    if (s.key === 'done') return; // don't show "done" in the list
    let icon = '○', color = 'var(--muted-l)';
    if (i < ci) { icon = '✓'; color = 'var(--green)'; }
    else if (i === ci) { icon = '◉'; color = 'var(--gold)'; }
    html += `<div style="font-size:.84rem;color:${color};line-height:1.6;">${icon} ${s.label}</div>`;
  });
  html += '</div>';
  container.innerHTML = html;
}

function updateMV06Progress(stepKey) {
  const info = MV06_STEPS.find(s => s.key === stepKey);
  const pct = info ? info.pct : 0;
  const fill = $('mv06ProgressFill');
  const label = $('mv06ProgressLabel');
  if (fill) fill.style.width = pct + '%';
  if (label && info) label.textContent = info.label;
}

// ── 展示最终视频（带下载 + 重新生成按钮）─────
// loadNow=true: 点击"合成"生成的，立即加载; loadNow=false: 刷新页面恢复，不自动加载
function showFinalVideo(url, loadNow = true) {
  hide('phaseMV06Progress');
  renderMV06Steps('done');
  updateMV06Progress('done');
  if (!url) {
    $('finalOutput').innerHTML = `<p style="color:var(--red);">视频 URL 为空，请检查后端状态。</p>`;
    show('phaseFinal');
    return;
  }
  const videoTag = loadNow
    ? `<video controls preload="none" style="width:100%;border-radius:10px;" data-src="${esc(url)}"></video>`
    : `<div id="mv06VideoPlaceholder" style="width:100%;border-radius:10px;background:var(--surface);padding:40px;text-align:center;cursor:pointer;">
         <div style="font-size:2rem;margin-bottom:8px;">🎬</div>
         <div>最终影像已生成，点击播放</div>
       </div>`;
  $('finalOutput').innerHTML = `${videoTag}
     <div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap;">
       <a class="btn btn-primary" href="${esc(url)}" download="niannian-memorial.mp4" target="_blank">下载完整影像</a>
       <button class="btn" id="btnRegenMV06">重新生成</button>
     </div>`;
  show('phaseFinal');
  const regenBtn = $('btnRegenMV06');
  if (regenBtn) regenBtn.onclick = regenerateMV06;
  // 点击占位符 → 替换为真实 video 标签
  const ph = $('mv06VideoPlaceholder');
  if (ph) ph.onclick = () => {
    ph.outerHTML = `<video controls autoplay style="width:100%;border-radius:10px;" src="${esc(url)}"></video>`;
  };
  window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
}

async function finalCut() {
  // 取消已有的轮询
  if (mv06_poll) { clearInterval(mv06_poll); mv06_poll = null; }

  const btn = $('btnFinalCut');
  const old = btn.textContent;
  btn.disabled = true; btn.textContent = '合成中...';
  setPill('MV06', 'active');

  // 显示进度面板
  hide('phaseFinal');
  hide('phaseError');
  show('phaseMV06Progress');
  renderMV06Steps('submitted');
  updateMV06Progress('submitted');

  try {
    const res = await apiPost(`/pipeline/run/MV06/${state.sid}`, {});
    if (res.error) throw new Error(res.message || 'MV06 提交失败');
  } catch (e) {
    setPill('MV06', '');
    hide('phaseMV06Progress');
    $('finalOutput').innerHTML = '';
    $('errorOutput').textContent = e.message || String(e);
    show('phaseError');
    btn.disabled = false; btn.textContent = old;
    return;
  }

  // 提交成功，开始轮询状态
  const MAX_POLLS = 180;  // 180 × 10s = 30 min
  let pollCount = 0;
  let lastStep = '';

  mv06_poll = setInterval(async () => {
    pollCount++;
    if (pollCount > MAX_POLLS) {
      clearInterval(mv06_poll); mv06_poll = null;
      setPill('MV06', '');
      hide('phaseMV06Progress');
      $('finalOutput').innerHTML = '';
      $('errorOutput').textContent = '合成超时（30 分钟未完成），请刷新后重试';
      show('phaseError');
      btn.disabled = false; btn.textContent = old;
      return;
    }

    try {
      const r = await apiGet(`/pipeline/status/${state.sid}`);
      const mv06 = r.pipeline_state && r.pipeline_state['MV06'];
      if (!mv06 || mv06.status === 'running') {
        // 还在进行中，更新进度
        const stepKey = mv06 && mv06.step ? mv06.step : 'running';
        if (stepKey !== lastStep) {
          lastStep = stepKey;
          renderMV06Steps(stepKey);
          updateMV06Progress(stepKey);
        } else {
          // 步骤没变，只更新提示文字
          const label = mv06 && mv06.label ? mv06.label : '合成中…';
          $('mv06ProgressLabel').textContent = label + `（第 ${Math.ceil(pollCount * 10 / 60)} 分钟）`;
        }
        return;
      }

      clearInterval(mv06_poll); mv06_poll = null;

      if (mv06.status === 'done') {
        setPill('MV06', 'done');
        showFinalVideo(r.video_url || r.mv06_result?.final_video_url || '');
      } else {
        // error
        setPill('MV06', '');
        hide('phaseMV06Progress');
        $('finalOutput').innerHTML = '';
        const err = r.error_detail || mv06.error || '未知错误';
        $('errorOutput').textContent = err;
        show('phaseError');
      }
      btn.disabled = false; btn.textContent = old;
    } catch (e) {
      console.warn('[mv06 poll] 轮询出错：', e.message);
    }
  }, 10_000);
}

// 重新生成 MV06（先重置，再重新运行）
async function regenerateMV06() {
  if (!confirm('确定要重新生成最终影像吗？当前视频将被替换。')) return;

  hide('phaseFinal');
  hide('phaseError');
  $('finalOutput').innerHTML = '';
  setPill('MV06', '');

  // 重置按钮不可用
  const btn = $('btnFinalCut');
  const old = btn.textContent;
  btn.disabled = true; btn.textContent = '合成中...';

  // 先重置 MV06 状态
  try {
    await apiPost(`/pipeline/reset/${state.sid}/MV06`, {});
  } catch (e) {
    btn.disabled = false; btn.textContent = old;
    toast('重置失败：' + e.message);
    return;
  }

  // 然后重新运行（finalCut 会处理按钮状态）
  finalCut();
}

// 页面加载时恢复 MV06 状态（缓存视频 / 恢复轮询）
async function resumeMV06() {
  const r = await apiGet(`/pipeline/status/${state.sid}`);
  const mv06 = r.pipeline_state && r.pipeline_state['MV06'];
  if (!mv06) return;

  if (mv06.status === 'done') {
    // 已有缓存视频，直接展示
    const url = r.video_url || '';
    if (url) {
      setPill('MV06', 'done');
      showFinalVideo(url, false);  // 不自动加载视频
    }
  } else if (mv06.status === 'running') {
    // 后台还在跑，恢复轮询
    finalCutPollOnly(r);
  }
}

// 仅启动轮询（不重新提交），用于页面刷新后恢复进行中的 MV06
function finalCutPollOnly(initData) {
  if (mv06_poll) { clearInterval(mv06_poll); mv06_poll = null; }

  setPill('MV06', 'active');
  hide('phaseFinal');
  hide('phaseError');
  show('phaseMV06Progress');

  const MAX_POLLS = 180;
  let pollCount = 0;
  let lastStep = '';

  // 用初始数据更新一次 UI
  const mv06 = initData.pipeline_state && initData.pipeline_state['MV06'];
  if (mv06 && mv06.step) {
    lastStep = mv06.step;
    renderMV06Steps(lastStep);
    updateMV06Progress(lastStep);
    if (mv06.label) $('mv06ProgressLabel').textContent = mv06.label;
  }

  mv06_poll = setInterval(async () => {
    pollCount++;
    if (pollCount > MAX_POLLS) {
      clearInterval(mv06_poll); mv06_poll = null;
      setPill('MV06', '');
      hide('phaseMV06Progress');
      $('finalOutput').innerHTML = '';
      $('errorOutput').textContent = '合成超时（30 分钟未完成），请刷新后重试';
      show('phaseError');
      const btn2 = $('btnFinalCut');
      btn2.disabled = false; btn2.textContent = '合成最终影像';
      return;
    }

    try {
      const r = await apiGet(`/pipeline/status/${state.sid}`);
      const mv06 = r.pipeline_state && r.pipeline_state['MV06'];
      if (!mv06 || mv06.status === 'running') {
        const stepKey = mv06 && mv06.step ? mv06.step : 'running';
        if (stepKey !== lastStep) {
          lastStep = stepKey;
          renderMV06Steps(stepKey);
          updateMV06Progress(stepKey);
        } else {
          const label = mv06 && mv06.label ? mv06.label : '合成中…';
          $('mv06ProgressLabel').textContent = label + `（第 ${Math.ceil(pollCount * 10 / 60)} 分钟）`;
        }
        return;
      }

      clearInterval(mv06_poll); mv06_poll = null;

      if (mv06.status === 'done') {
        setPill('MV06', 'done');
        showFinalVideo(r.video_url || r.mv06_result?.final_video_url || '');
        const btnD = $('btnFinalCut');
        btnD.disabled = false; btnD.textContent = '重新生成';
      } else {
        setPill('MV06', '');
        hide('phaseMV06Progress');
        $('finalOutput').innerHTML = '';
        const err = r.error_detail || mv06.error || '未知错误';
        $('errorOutput').textContent = err;
        show('phaseError');
        const btnE = $('btnFinalCut');
        btnE.disabled = false; btnE.textContent = '合成最终影像';
      }
    } catch (e) {
      console.warn('[mv06 poll] 轮询出错：', e.message);
    }
  }, 10_000);
}

// ───── 启动 ─────
async function bootstrap() {
  if (!state.sid) {
    toast('请先完成前期访谈');
    setTimeout(() => location.href = 'memorial.html', 1500);
    return;
  }
  // 1. 角色档案
  try {
    state.chars = await apiGet(`/pipeline/characters/${state.sid}`);
    renderCharSummary();
  } catch {
    $('charSummary').innerHTML = `<div class="text-muted" style="color:var(--red);">未找到角色档案，请先在「方案确认台」完成前期流程。</div>`;
  }
  // 2. 已有 MV04 则直接展示（record 模式下跳过，始终显示"生成分镜"按钮）
  let cacheMode = '';
  try {
    const statusR = await apiGet(`/pipeline/status/${state.sid}`);
    cacheMode = statusR.cache_mode || '';
  } catch { /* 忽略 */ }

  if (cacheMode !== 'record') {
    try {
      const r = await apiGet(`/pipeline/scenes/${state.sid}`);
      if (r.ready && Array.isArray(r.scenes) && r.scenes.length) {
        // 后端字段名 → 前端短名映射，保持内存一致
        r.scenes.forEach(sc => {
          if (sc._image_url)    { sc._img_url     = sc._image_url; }
          if (sc._video_url)    { sc._vid_url     = sc._video_url; }
          if (sc._video_status) { sc._vid_status  = sc._video_status; }
          if (sc._video_url)    { sc._vid_loaded  = true; }
        });
        state.scenes = r.scenes;
        setPill('MV04', 'done');
        hide('phaseGenScenes');
        show('phaseScenes');
        renderScenes();
        // 恢复未完成视频的分镜轮询
        state.scenes.forEach((sc, i) => {
          if (sc._video_task_id && !sc._vid_url) {
            resumeSceneVideoPoll(i);
          }
        });
      }
    } catch { /* 没生成过则保持初始 UI */ }
  }
  // 未自动加载分镜时，检查当前 session 是否有 MV04，有则显示"加载上次分镜"按钮
  if (!state.scenes || !state.scenes.length) {
    try {
      const last = await apiGet(`/pipeline/last-storyboard?current_sid=${state.sid}`);
      if (last.found) show('btnLoadLastScenes');
    } catch { /* 忽略 */ }
  }
  // 3. 恢复 MV06 状态（缓存视频 / 恢复轮询）
  try {
    await resumeMV06();
  } catch { /* 没有 MV06 数据则忽略 */ }
}

// ───── 加载上次分镜（当前 session）─────
async function loadLastStoryboard() {
  try {
    const r = await apiGet(`/pipeline/scenes/${state.sid}`);
    if (r.ready && Array.isArray(r.scenes) && r.scenes.length) {
      r.scenes.forEach(sc => {
        if (sc._image_url)    { sc._img_url     = sc._image_url; }
        if (sc._video_url)    { sc._vid_url     = sc._video_url; }
        if (sc._video_status) { sc._vid_status  = sc._video_status; }
        if (sc._video_url)    { sc._vid_loaded  = true; }
      });
      state.scenes = r.scenes;
      setPill('MV04', 'done');
      hide('phaseGenScenes');
      show('phaseScenes');
      renderScenes();
      // 恢复未完成视频的轮询
      state.scenes.forEach((sc, i) => {
        if (sc._video_task_id && !sc._vid_url) {
          resumeSceneVideoPoll(i);
        }
      });
      toast('已加载上次分镜');
    } else {
      toast('当前 session 无分镜数据');
    }
  } catch (e) {
    toast('加载失败：' + e.message);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  bootstrap();
  $('btnGenScenes').onclick = genScenes;
  $('btnLoadLastScenes').onclick = loadLastStoryboard;
  $('btnFinalCut').onclick  = finalCut;

  // 视频懒加载：点击播放器时才开始加载并播放
  document.addEventListener('click', e => {
    const vid = e.target.closest('video[data-src]');
    if (vid && !vid.src) {
      vid.src = vid.dataset.src;
      vid.load();
      vid.play().catch(() => {}); // 自动播放，忽略浏览器策略限制
    }
  });
});

// 页面关闭时清理轮询
window.addEventListener('beforeunload', () => {
  if (mv06_poll) clearInterval(mv06_poll);
});
