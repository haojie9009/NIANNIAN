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

    const imgHtml = sc._img_url
      ? `<div class="media-slot has-media">
           <img class="zoomable" data-idx="${i}" src="${esc(sc._img_url)}" alt="scene image">
           <div class="media-cap">点击图片放大查看</div>
         </div>`
      : `<div class="media-slot"><div class="media-cap">画面图片</div>${imgBadge}</div>`;

    const vidHtml = sc._vid_url
      ? `<div class="media-slot has-media">
           <video controls preload="metadata" src="${esc(sc._vid_url)}"></video>
           <div class="media-cap" style="margin-top:6px;">
             <a class="btn btn-sm" href="${esc(sc._vid_url)}" download="scene-${String(i + 1).padStart(2, '0')}.mp4" target="_blank">下载视频</a>
           </div>
         </div>`
      : `<div class="media-slot"><div class="media-cap">短视频</div>${vidBadge}</div>`;

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
      if (act === 'img') genSceneImage(idx);
      else if (act === 'vid') genSceneVideo(idx);
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
async function genSceneImage(idx) {
  const sc = state.scenes[idx];
  if (!sc) return;
  sc._img_status = 'run';
  renderScenes();
  setPill('MV05', 'active');
  try {
    const res = await apiPost(`/pipeline/scene/image/${state.sid}/${idx}`, {});
    if (res.error) throw new Error(res.message || '图片生成失败');
    sc._img_url    = res.url || res.image_url;
    sc._img_status = 'done';
  } catch (e) {
    sc._img_status = 'err';
    toast('图片生成失败：' + e.message);
  } finally {
    renderScenes();
    if (state.scenes.length && state.scenes.every(s => s._img_url)) setPill('MV05', 'done');
  }
}

// ───── 单镜视频生成（异步提交 + 轮询）─────
async function genSceneVideo(idx) {
  const sc = state.scenes[idx];
  if (!sc || !sc._img_url) { toast('请先生成图片'); return; }

  if (sc._vid_poll) {
    clearInterval(sc._vid_poll);
    sc._vid_poll = null;
  }

  sc._vid_status = 'run';
  sc._vid_url    = null;
  renderScenes();

  let taskId, taskSource;
  try {
    const res = await apiPost(`/pipeline/scene/video/${state.sid}/${idx}`, {
      image_url: sc._img_url,
    });
    if (res.error) throw new Error(res.message || '视频提交失败');
    taskId     = res.task_id;
    taskSource = res.source || '';
  } catch (e) {
    sc._vid_status = 'err';
    toast('视频提交失败：' + e.message);
    renderScenes();
    return;
  }

  sc._vid_task_id     = taskId;
  sc._vid_task_source = taskSource;

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
        `/pipeline/scene/video/status/${state.sid}/${idx}/${taskId}?source=${encodeURIComponent(taskSource)}`
      );
      if (r.status === 'done') {
        clearInterval(sc._vid_poll);
        sc._vid_poll   = null;
        sc._vid_url    = r.url;
        sc._vid_status = 'done';
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
async function finalCut() {
  const btn = $('btnFinalCut');
  const old = btn.textContent;
  btn.disabled = true; btn.textContent = '合成中...';
  setPill('MV06', 'active');
  try {
    const res = await apiPost(`/pipeline/run/MV06/${state.sid}`, {});
    if (res.error) throw new Error(res.message || 'MV06 失败');
    setPill('MV06', 'done');
    const url = (res.result && (res.result.final_video_url || res.result.url)) || '';
    $('finalOutput').innerHTML = url
      ? `<video controls style="width:100%;border-radius:10px;" src="${esc(url)}"></video>
         <div style="margin-top:8px;"><a class="btn btn-primary" href="${esc(url)}" download="niannian-memorial.mp4" target="_blank">下载完整影像</a></div>`
      : `<pre style="background:var(--surf2);padding:10px;border-radius:8px;font-size:.78rem;overflow-x:auto;">${esc(JSON.stringify(res.result, null, 2))}</pre>`;
    show('phaseFinal');
    window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
  } catch (e) {
    setPill('MV06', '');
    $('errorOutput').textContent = e.message || String(e);
    show('phaseError');
  } finally {
    btn.disabled = false; btn.textContent = old;
  }
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
  // 2. 已有 MV04 则直接展示
  try {
    const r = await apiGet(`/pipeline/scenes/${state.sid}`);
    if (r.ready && Array.isArray(r.scenes) && r.scenes.length) {
      state.scenes = r.scenes;
      setPill('MV04', 'done');
      hide('phaseGenScenes');
      show('phaseScenes');
      renderScenes();
    }
  } catch { /* 没生成过则保持初始 UI */ }
}

document.addEventListener('DOMContentLoaded', () => {
  bootstrap();
  $('btnGenScenes').onclick = genScenes;
  $('btnFinalCut').onclick  = finalCut;
});
