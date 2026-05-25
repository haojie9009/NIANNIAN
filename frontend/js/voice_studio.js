// voice_studio.js — 声音工坊
(function(){
  'use strict';
  if (!window.NianAuth || !window.NianAuth.requireAuth()) return;

  var $ = function(id){ return document.getElementById(id); };
  var esc = function(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); };

  // 从 URL ?mid=xxx 或 localStorage 拿当前对象
  function getMidFromUrl(){
    var m = location.search.match(/[?&]mid=([^&]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }
  var DEFAULT_VOICE = {
    voice_id: '', provider: '', status: 'idle', samples: [],
    params: { speed: 1.0, pitch: 0, volume: 1.0, emotion: 'neutral', base_voice: 'longxiaochun' },
    preview_text: '今天天气真好，我们一起去散步吧。',
    last_clone_at: '', history: [], error: '',
  };
  var state = {
    mid: getMidFromUrl() || NianAuth.getActiveMemorialId() || '',
    memorials: [],
    voice: JSON.parse(JSON.stringify(DEFAULT_VOICE)),
    audios: [],
    presets: [],
    selected: new Set(),
    saveDebounce: null,
  };

  function toast(msg, ms){
    var t = $('vsToast'); t.textContent = msg; t.classList.add('show');
    clearTimeout(t._tm); t._tm = setTimeout(function(){ t.classList.remove('show'); }, ms||2500);
  }

  // ─── 加载对象列表 ───────────────────────────────────────────
  async function loadMemorials(){
    var r = await NianAuth.fetch('/api/memorials');
    var d = await r.json();
    state.memorials = d.memorials || [];
    var sel = $('vsMemSelect'); sel.innerHTML = '';
    state.memorials.forEach(function(m){
      var o = document.createElement('option');
      o.value = m.memorial_id;
      o.textContent = (m.name || '未命名') + (m.relation ? ' · ' + m.relation : '');
      sel.appendChild(o);
    });
    if (!state.mid && state.memorials.length) state.mid = state.memorials[0].memorial_id;
    if (state.mid) {
      sel.value = state.mid;
      NianAuth.setActiveMemorialId(state.mid);
    }
    sel.addEventListener('change', function(){
      state.mid = sel.value;
      NianAuth.setActiveMemorialId(state.mid);
      history.replaceState({}, '', '?mid=' + encodeURIComponent(state.mid));
      loadVoice();
    });
    var cur = state.memorials.find(function(m){ return m.memorial_id === state.mid; });
    if (cur) $('vsSubTitle').textContent = '正在为「' + cur.name + '」配置声音';
  }

  // ─── 加载声音配置 ───────────────────────────────────────────
  async function loadVoice(){
    if (!state.mid) {
      $('vsStatusText').textContent = '请先在主页创建一个纪念对象';
      return;
    }
    try {
      var r = await NianAuth.fetch('/api/memorials/' + state.mid + '/voice');
      var raw = await r.text();
      var d = {};
      try { d = JSON.parse(raw); } catch(_) {}
      if (!r.ok) {
        console.error('[voice] load failed', r.status, raw.slice(0,300));
        $('vsStatusText').textContent = '加载失败 HTTP ' + r.status + '：' + (d.detail || raw.slice(0,120));
        return;
      }
      state.voice = Object.assign({}, DEFAULT_VOICE, d.voice || {});
      state.voice.params = Object.assign({}, DEFAULT_VOICE.params, (d.voice && d.voice.params) || {});
      state.audios = d.audio_assets || [];
      state.presets = d.preset_voices || [];
      state.selected = new Set(state.voice.samples || []);
      renderAll();
    } catch(e) {
      console.error('[voice] load exception', e);
      $('vsStatusText').textContent = '加载失败：' + e.message;
    }
  }

  function renderAll(){
    renderStatus();
    renderSamples();
    renderParams();
    renderHistory();
  }

  // ─── 状态条 ─────────────────────────────────────────────────
  function renderStatus(){
    var s = state.voice.status || 'idle';
    var dot = $('vsStatusDot'); var txt = $('vsStatusText');
    dot.className = 'vs-status-dot ' + (s === 'ready' ? 'ready' : s === 'cloning' ? 'cloning' : s === 'mock' ? 'mock' : s === 'failed' ? 'failed' : '');
    var map = {
      idle:    '尚未克隆 · 选择音频样本后即可开始',
      cloning: '克隆中...',
      ready:   '✓ 已就绪 · DashScope 克隆成功 · voice_id=' + (state.voice.voice_id||'').slice(0,32),
      mock:    '⚠ Mock 模式 · 原因：' + (state.voice.error || 'DashScope 未配置或样本不可公网访问') + '（先用预制音色试听）',
      failed:  '✗ 失败：' + (state.voice.error || '未知错误'),
    };
    txt.textContent = map[s] || s;
  }

  // ─── 样本列表 ───────────────────────────────────────────────
  function renderSamples(){
    var box = $('vsSamples'); box.innerHTML = '';
    if (!state.audios.length) {
      box.innerHTML = '<div class="vs-empty">还没有音频样本。点击下方「上传新的声音样本」，或回到聊天页用 ⬆ 上传。</div>';
      return;
    }
    state.audios.forEach(function(a){
      var row = document.createElement('div');
      row.className = 'vs-sample' + (state.selected.has(a.asset_id) ? ' selected' : '');
      // 音频 URL 必须带 token，因为 <audio> 不会发 Authorization header
      var tok = (window.NianAuth && NianAuth.getToken && NianAuth.getToken()) || '';
      var audioUrl = a.url + (a.url.indexOf('?') >= 0 ? '&' : '?') + 'token=' + encodeURIComponent(tok);
      row.innerHTML =
        '<div class="chk"></div>' +
        '<div class="info">' +
          '<div class="name">' + esc(a.filename || a.asset_id) + '</div>' +
          '<div class="desc">' + esc(a.description || a.summary || '未填描述') + '</div>' +
        '</div>' +
        '<audio src="' + audioUrl + '" controls preload="metadata"></audio>';
      row.addEventListener('click', function(e){
        if (e.target.tagName === 'AUDIO' || e.target.closest('audio')) return;
        if (state.selected.has(a.asset_id)) state.selected.delete(a.asset_id);
        else state.selected.add(a.asset_id);
        renderSamples();
      });
      box.appendChild(row);
    });
  }

  // ─── 参数 ───────────────────────────────────────────────────
  function renderParams(){
    var p = state.voice.params;
    // 预制音色
    var bv = $('vsBaseVoice'); bv.innerHTML = '';
    state.presets.forEach(function(v){
      var o = document.createElement('option');
      o.value = v.id; o.textContent = v.name;
      bv.appendChild(o);
    });
    bv.value = p.base_voice || 'longxiaochun';
    var cur = state.presets.find(function(v){ return v.id === bv.value; });
    $('vsBaseLabel').textContent = cur ? cur.name.split(' · ')[0] : bv.value;

    $('vsSpeed').value = p.speed; $('vsSpeedVal').textContent = (+p.speed).toFixed(2);
    $('vsPitch').value = p.pitch; $('vsPitchVal').textContent = (p.pitch>0?'+':'') + p.pitch;
    $('vsVol').value = p.volume;  $('vsVolVal').textContent = (+p.volume).toFixed(1);
    document.querySelectorAll('#vsEmotion button').forEach(function(b){
      b.classList.toggle('active', b.dataset.v === (p.emotion||'neutral'));
    });
    $('vsPreviewText').value = state.voice.preview_text || '';
  }

  function collectParams(){
    return {
      speed:  parseFloat($('vsSpeed').value),
      pitch:  parseInt($('vsPitch').value, 10),
      volume: parseFloat($('vsVol').value),
      emotion: document.querySelector('#vsEmotion button.active').dataset.v,
      base_voice: $('vsBaseVoice').value,
    };
  }

  async function saveParams(silent){
    if (!state.mid) return;
    var body = {
      params: collectParams(),
      preview_text: $('vsPreviewText').value,
      samples: Array.from(state.selected),
    };
    try {
      var r = await NianAuth.fetch('/api/memorials/' + state.mid + '/voice', {
        method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)
      });
      var d = await r.json();
      state.voice = d.voice;
      if (!silent) toast('参数已保存');
    } catch(e){ if (!silent) toast('保存失败：' + e.message); }
  }

  function debouncedSave(){
    clearTimeout(state.saveDebounce);
    state.saveDebounce = setTimeout(function(){ saveParams(true); }, 800);
  }

  // ─── 历史 ───────────────────────────────────────────────────
  function renderHistory(){
    var box = $('vsHistory');
    var h = state.voice.history || [];
    if (!h.length) { box.innerHTML = '<div class="vs-history-empty">还没有克隆记录</div>'; return; }
    box.innerHTML = '';
    h.slice().reverse().forEach(function(item){
      var row = document.createElement('div');
      row.className = 'vs-hist-row';
      row.innerHTML =
        '<span class="ts">' + esc(item.created_at) + '</span>' +
        '<span class="vid">' + esc(item.voice_id) + '</span>' +
        '<span>' + (item.sample_count||0) + ' 个样本</span>' +
        '<span class="badge ' + (item.provider||'mock') + '">' + (item.provider||'mock') + '</span>';
      box.appendChild(row);
    });
  }

  // ─── 上传 ───────────────────────────────────────────────────
  $('vsUploadBtn').addEventListener('click', function(){ $('vsFileInput').click(); });
  $('vsFileInput').addEventListener('change', async function(e){
    var f = e.target.files[0]; if (!f) return;
    var desc = prompt('简单描述一下这段录音（在什么场合录的？谁的声音？）', '');
    if (desc === null) { e.target.value=''; return; }
    var form = new FormData();
    form.append('file', f);
    form.append('description', desc || '声音样本');
    toast('上传中...');
    try {
      var r = await NianAuth.fetch('/api/memorials/' + state.mid + '/upload', { method:'POST', body: form });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      toast('✓ 上传成功');
      await loadVoice();
    } catch(err) { toast('上传失败：' + err.message); }
    e.target.value = '';
  });

  // ─── 克隆 ───────────────────────────────────────────────────
  $('vsCloneBtn').addEventListener('click', async function(){
    if (state.selected.size === 0) { toast('请先勾选至少一个声音样本'); return; }
    var btn = this; btn.disabled = true; btn.textContent = '克隆中...';
    state.voice.status = 'cloning'; renderStatus();
    try {
      var r = await NianAuth.fetch('/api/memorials/' + state.mid + '/voice/clone', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ sample_ids: Array.from(state.selected), note: '' })
      });
      if (!r.ok) { var ed = await r.json().catch(function(){return{};}); throw new Error(ed.detail || ('HTTP '+r.status)); }
      var d = await r.json();
      state.voice = d.voice;
      renderAll();
      if (state.voice.provider === 'dashscope') toast('🎉 克隆成功！可以试听了', 3000);
      else toast('已切换 Mock 模式（先用预制音色试听）', 3500);
    } catch(e) {
      toast('克隆失败：' + e.message, 4000);
      state.voice.status = 'failed'; state.voice.error = e.message; renderStatus();
    } finally {
      btn.disabled = false; btn.textContent = '🎯 开始克隆';
    }
  });

  // ─── 试听 ───────────────────────────────────────────────────
  $('vsPreviewBtn').addEventListener('click', async function(){
    var text = $('vsPreviewText').value.trim();
    if (!text) { toast('请输入要合成的文本'); return; }
    var useClone = document.querySelector('input[name=vsMode]:checked').value === 'clone';
    var btn = this; btn.disabled = true; btn.textContent = '合成中...';
    await saveParams(true);
    try {
      var r = await NianAuth.fetch('/api/memorials/' + state.mid + '/voice/preview', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ text: text, use_clone: useClone })
      });
      if (!r.ok) {
        var ed = await r.json().catch(function(){return{};});
        throw new Error(ed.detail || ('HTTP '+r.status));
      }
      var blob = await r.blob();
      var url = URL.createObjectURL(blob);
      var pl = $('vsAudioPlayer'); pl.src = url; pl.play();
      toast('✓ 合成完成', 1800);
    } catch(e) {
      toast('合成失败：' + e.message, 4000);
    } finally {
      btn.disabled = false; btn.textContent = '▶ 合成并试听';
    }
  });

  // ─── 重置 ───────────────────────────────────────────────────
  $('vsResetBtn').addEventListener('click', async function(){
    if (!confirm('确定清空当前克隆？历史记录会保留。')) return;
    try {
      var r = await NianAuth.fetch('/api/memorials/' + state.mid + '/voice', { method:'DELETE' });
      var d = await r.json();
      state.voice = d.voice;
      state.selected = new Set();
      renderAll();
      toast('已重置');
    } catch(e) { toast('重置失败：' + e.message); }
  });

  // ─── 保存参数按钮 ─────────────────────────────────────────
  $('vsSaveParamsBtn').addEventListener('click', function(){ saveParams(false); });

  // ─── 监听参数变化 → 即时显示 + 防抖保存 ──────────────────
  ['vsSpeed','vsPitch','vsVol'].forEach(function(id){
    $(id).addEventListener('input', function(){
      var v = $(id).value;
      if (id==='vsSpeed') $('vsSpeedVal').textContent = (+v).toFixed(2);
      if (id==='vsPitch') $('vsPitchVal').textContent = (v>0?'+':'') + v;
      if (id==='vsVol')   $('vsVolVal').textContent = (+v).toFixed(1);
      debouncedSave();
    });
  });
  $('vsBaseVoice').addEventListener('change', function(){
    var cur = state.presets.find(function(v){ return v.id === $('vsBaseVoice').value; });
    $('vsBaseLabel').textContent = cur ? cur.name.split(' · ')[0] : '';
    debouncedSave();
  });
  document.querySelectorAll('#vsEmotion button').forEach(function(b){
    b.addEventListener('click', function(){
      document.querySelectorAll('#vsEmotion button').forEach(function(x){ x.classList.remove('active'); });
      b.classList.add('active');
      debouncedSave();
    });
  });
  $('vsPreviewText').addEventListener('input', debouncedSave);

  // ─── 顶部账号条简版 ────────────────────────────────────────
  (async function init(){
    await loadMemorials();
    if (state.mid) await loadVoice();
  })();
})();
