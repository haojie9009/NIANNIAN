// frontend/js/pipeline.js — 前期确认台（pipeline）
const { apiGet, apiPost, getSessionId, toast, esc } = window.NN;

const state = {
  sid: getSessionId(),
  scenes: [],
};

// ───── 工具 ─────
function show(id) { document.getElementById(id).classList.remove('hidden'); }
function hide(id) { document.getElementById(id).classList.add('hidden'); }

function appendAiBubble(wrapId, text, name = '念念 AI') {
  const wrap = document.getElementById(wrapId);
  wrap.insertAdjacentHTML('beforeend', `
    <div class="chat-ai">
      <div class="ai-avatar">念</div>
      <div class="ai-bubble-wrap">
        <div class="ai-name">${esc(name)}</div>
        <div class="ai-bubble">${esc(text)}</div>
      </div>
    </div>`);
  wrap.scrollIntoView({ behavior: 'smooth', block: 'end' });
}

function setPipePill(step, status) {
  // status: '' | 'active' | 'done'
  const pill = document.querySelector(`.pipe-pill[data-step="${step}"]`);
  if (!pill) return;
  pill.classList.remove('active', 'done');
  if (status) pill.classList.add(status);
}

function setThink(thinkId, on, label) {
  const el = document.getElementById(thinkId);
  if (!el) return;
  el.classList.toggle('hidden', !on);
  if (on && label) {
    const lbl = el.querySelector('.think-label');
    if (lbl) lbl.textContent = label;
  }
}

function updateThinkLabel(label) {
  const el = document.getElementById('pipelineThink');
  if (!el) return;
  const lbl = el.querySelector('.think-label');
  if (lbl) lbl.textContent = label;
}

function showError(msg) {
  setThink('pipelineThink', false);
  document.getElementById('errorOutput').textContent = msg || '未知错误';
  show('phaseError');
}

// ───── Pill 状态映射 ─────
const STEP_PILLS = {
  mv01_running:  { MV01: 'active' },
  mv01_done:     { MV01: 'done' },
  mv01_error:    { MV01: 'done' },
  mv02_running:  { MV01: 'done', MV02: 'active' },
  mv02_done:     { MV01: 'done', MV02: 'done' },
  mv02_error:    { MV01: 'done', MV02: 'done' },
  mv03_running:  { MV01: 'done', MV02: 'done', MV03: 'active' },
  mv03_done:     { MV01: 'done', MV02: 'done', MV03: 'done' },
  mv03_error:    { MV01: 'done', MV02: 'done', MV03: 'done' },
  done:          { MV01: 'done', MV02: 'done', MV03: 'done' },
  error:         { MV01: 'done', MV02: 'done', MV03: 'done' },
};

function updatePillsFromStep(step) {
  const mapping = STEP_PILLS[step];
  if (!mapping) return;
  for (const [pill, status] of Object.entries(mapping)) {
    setPipePill(pill, status);
  }
}

// ───── 轮询 ─────
let pipeline_poll = null;
const MAX_POLLS = 120; // 120 * 10s = 20 分钟

function startPipelinePoll() {
  let pollCount = 0;
  pipeline_poll = setInterval(async () => {
    if (pollCount++ > MAX_POLLS) {
      clearInterval(pipeline_poll);
      setButtons(true);
      showError('处理超时，请重试');
      return;
    }
    try {
      const r = await apiGet(`/pipeline/status/${state.sid}`);
      const pc = r.pipeline_state?.pipeline_chain;
      if (!pc || pc.status === 'running') {
        updateThinkLabel(pc?.label || '处理中...');
        updatePillsFromStep(pc?.step || '');
        return;
      }
      // 终态
      clearInterval(pipeline_poll);
      if (pc.status === 'done') {
        setThink('pipelineThink', false);
        updatePillsFromStep('done');
        setButtons(true);
        (r.pipeline_bubbles || []).forEach(b => appendAiBubble('pipelineChat', b.content));
        setTimeout(() => show('phaseDone'), 300);
      } else {
        updatePillsFromStep('error');
        setButtons(true);
        showError(pc.error || '未知错误');
      }
    } catch (e) {
      // 忽略瞬态错误（网络波动等）
      console.warn('pipeline poll error:', e);
    }
  }, 10_000);
}

// ───── 阶段 1：Preview 大白话 ─────
async function loadPreview() {
  setThink('previewThink', true);
  try {
    const res = await apiPost(`/pipeline/preview/${state.sid}`, {});
    appendAiBubble('previewChat', res.text || '让我们一起为这部影像做好准备吧。');
  } catch (e) {
    appendAiBubble('previewChat',
      '我们会先把您说的内容整理成结构化的故事大纲，然后AI核对，最后确定影像的整体氛围和主角形象。准备好就开始吧。');
    console.error(e);
  } finally {
    setThink('previewThink', false);
  }
}

// ───── 阶段 2：运行 MV01→MV02→MV03（异步轮询） ─────
let pipeline_running = false;

function setButtons(enabled) {
  pipeline_running = !enabled;
  document.getElementById('btnStartPipeline').disabled = !enabled;
  document.getElementById('btnRetry').disabled = !enabled;
}

async function runPipeline() {
  if (pipeline_running) return;
  setButtons(false);
  if (pipeline_poll) clearInterval(pipeline_poll);
  hide('phasePreview');
  show('phasePipeline');
  setPipePill('MV01', 'active');
  setThink('pipelineThink', true, '任务已提交...');

  try {
    await apiPost(`/pipeline/run-all/${state.sid}`, {});
    startPipelinePoll();
  } catch (e) {
    setThink('pipelineThink', false);
    setButtons(true);
    showError(e.message || String(e));
  }
}

// ───── 启动 ─────
async function bootstrap() {
  if (!state.sid) {
    toast('会话不存在，请先完成 Step 1 表单');
    setTimeout(() => location.href = 'memorial.html', 1500);
    return;
  }
  // 校验 session
  try { await apiGet(`/intake/session/${state.sid}`); }
  catch {
    toast('会话已过期，请重新填写');
    setTimeout(() => location.href = 'memorial.html', 1500);
    return;
  }

  // 刷新恢复：仅检测 running 状态，中断中的任务继续轮询
  try {
    const r = await apiGet(`/pipeline/status/${state.sid}`);
    const pc = r.pipeline_state?.pipeline_chain;
    if (pc?.status === 'running') {
      setButtons(false);
      hide('phasePreview');
      show('phasePipeline');
      updateThinkLabel(pc.label || '处理中...');
      updatePillsFromStep(pc.step || '');
      startPipelinePoll();
      return;
    }
    // done / error 状态不自动恢复，统一重新加载 preview
  } catch {
    // 首次访问，正常加载
  }

  await loadPreview();
}

document.addEventListener('DOMContentLoaded', () => {
  bootstrap();
  document.getElementById('btnStartPipeline').onclick = runPipeline;
  document.getElementById('btnRetry').onclick = () => { hide('phaseError'); runPipeline(); };
});

window.addEventListener('beforeunload', () => {
  if (pipeline_poll) clearInterval(pipeline_poll);
});
