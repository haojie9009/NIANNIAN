// library.js — 资料库前端
(function(){
  'use strict';
  if (!NianAuth.requireAuth()) return;

  var state = { memorials: [], currentId: null, dossier: null, meta: null, assets: [] };

  function $(id){ return document.getElementById(id); }

  // 顶部用户信息
  (function(){
    var u = NianAuth.getUser() || {};
    $('libUser').innerHTML = (u.display_name || u.email || '访客') + (u.is_owner ? ' · 主人' : '') +
      ' <span class="logout">退出</span>';
    $('libUser').querySelector('.logout').onclick = function(){ NianAuth.logout(); };
  })();

  // ── 列表 ──
  async function loadMemorials(){
    var r = await NianAuth.fetch('/api/memorials');
    var d = await r.json();
    state.memorials = d.memorials || [];
    renderList();
    var activeId = NianAuth.getActiveMemorialId() || (state.memorials[0] && state.memorials[0].memorial_id);
    if (activeId) selectMemorial(activeId);
  }

  function renderList(){
    var ul = $('memList'); ul.innerHTML = '';
    state.memorials.forEach(function(m){
      var li = document.createElement('li');
      li.innerHTML = '<div class="n">' + escapeHtml(m.name) + '</div><div class="r">' + escapeHtml(m.relation || '—') + '</div>';
      if (m.memorial_id === state.currentId) li.classList.add('active');
      li.onclick = function(){ selectMemorial(m.memorial_id); };
      ul.appendChild(li);
    });
  }

  async function selectMemorial(mid){
    state.currentId = mid;
    NianAuth.setActiveMemorialId(mid);
    renderList();
    var r = await NianAuth.fetch('/api/memorials/' + mid);
    var d = await r.json();
    state.meta = d.meta; state.dossier = d.dossier || {}; state.assets = d.assets || [];
    renderDetail();
  }

  function renderDetail(){
    $('emptyHint').style.display = 'none';
    $('detailBody').style.display = 'block';
    $('dName').textContent = state.meta.name || '未命名';
    $('dRelation').textContent = state.meta.relation || '';
    $('dIntent').textContent = intentLabel(state.meta.product_intent || (state.dossier.product_intent && state.dossier.product_intent.primary)) || '方向未定';
    $('dUpdated').textContent = '更新于 ' + (state.meta.updated_at || '').replace('T',' ');
    bindDossierToInputs();
    renderMemories();
    renderAssets();
    loadConversations();
    $('quotesArea').value = (state.dossier.quotes || []).join('\n');
    $('objectsArea').value = (state.dossier.objects || []).join('\n');
  }

  function intentLabel(v){
    return ({video:'追思影像', biography:'个人传记', digital_human:'数字人'})[v] || '';
  }

  // ── 数据绑定 ──
  function getByPath(o, path){
    var parts = path.split('.'), cur = o;
    for (var i=0;i<parts.length;i++){ if (cur == null) return ''; cur = cur[parts[i]]; }
    return cur == null ? '' : cur;
  }
  function setByPath(o, path, val){
    var parts = path.split('.'), cur = o;
    for (var i=0;i<parts.length-1;i++){ if (!cur[parts[i]] || typeof cur[parts[i]] !== 'object') cur[parts[i]] = {}; cur = cur[parts[i]]; }
    cur[parts[parts.length-1]] = val;
  }
  function bindDossierToInputs(){
    document.querySelectorAll('[data-bind]').forEach(function(el){
      var v = getByPath(state.dossier, el.dataset.bind);
      if (Array.isArray(v)) v = v.join(', ');
      if (v === true) v = 'true';
      if (v === false) v = 'false';
      el.value = v == null ? '' : v;
    });
  }
  function readInputsToDossier(){
    document.querySelectorAll('[data-bind]').forEach(function(el){
      var path = el.dataset.bind; var raw = el.value;
      // 数组字段
      if (/(locations|keywords|habits|catchphrases)$/.test(path)) {
        var arr = raw.split(/[\n,，]/).map(function(s){return s.trim();}).filter(Boolean);
        setByPath(state.dossier, path, arr);
      } else if (path.indexOf('permissions.') === 0) {
        setByPath(state.dossier, path, raw === '' ? null : raw === 'true');
      } else {
        setByPath(state.dossier, path, raw);
      }
    });
    // 记忆
    var memories = [];
    document.querySelectorAll('#memoriesList li').forEach(function(li){
      memories.push({
        title: li.querySelector('.title-input').value,
        content: li.querySelector('.body-input').value,
        tags: [],
      });
    });
    state.dossier.memories = memories;
    state.dossier.quotes = $('quotesArea').value.split('\n').map(function(s){return s.trim();}).filter(Boolean);
    state.dossier.objects = $('objectsArea').value.split('\n').map(function(s){return s.trim();}).filter(Boolean);
  }

  // ── 记忆卡片 ──
  function renderMemories(){
    var ul = $('memoriesList'); ul.innerHTML = '';
    (state.dossier.memories || []).forEach(function(m, idx){
      ul.appendChild(buildMemCard(m, idx));
    });
  }
  function buildMemCard(m, idx){
    var li = document.createElement('li');
    li.innerHTML =
      '<button class="del">删除</button>' +
      '<input class="title-input" value="' + escapeAttr(m.title || '') + '" placeholder="记忆标题">' +
      '<textarea class="body-input" placeholder="记忆内容...">' + escapeHtml(m.content || '') + '</textarea>';
    li.querySelector('.del').onclick = function(){ li.remove(); };
    return li;
  }
  $('btnAddMem').addEventListener('click', function(){
    $('memoriesList').appendChild(buildMemCard({}, 0));
  });

  // ── 素材 ──
  function renderAssets(){
    // 声音工坊入口：附带当前 memorial_id，并展示统计
    var entry = $('voiceStudioEntry');
    if (entry && state.currentId) {
      entry.href = '/static/voice_studio.html?mid=' + encodeURIComponent(state.currentId);
      var audioCount = (state.assets || []).filter(function(a){ return a.kind === 'audio'; }).length;
      var voiceMeta = $('voiceStudioMeta');
      if (voiceMeta) voiceMeta.textContent = audioCount + ' 个音频样本 · 点击进入工坊管理克隆';
    }
    var ul = $('assetsList'); ul.innerHTML = '';
    if (!state.assets.length) {
      ul.innerHTML = '<li style="grid-column:1/-1;color:#8a7654;padding:30px;text-align:center">还没有素材。在聊天页上传后，文件会出现在这里。</li>';
      return;
    }
    state.assets.forEach(function(a){
      var li = document.createElement('li');
      // 资产 URL 必须带 token（img/audio 不会发 Authorization header）
      var tok = (window.NianAuth && NianAuth.getToken && NianAuth.getToken()) || '';
      var aUrl = a.url + (a.url.indexOf('?') >= 0 ? '&' : '?') + 'token=' + encodeURIComponent(tok);
      var thumb = (a.kind === 'image') ? '<img src="' + aUrl + '" alt="">' : iconForKind(a.kind);
      var tags = (a.tags || []).map(function(t){return '<span>' + escapeHtml(t) + '</span>';}).join('');
      li.innerHTML =
        '<div class="asset-thumb">' + thumb + '</div>' +
        '<div class="asset-name">' + escapeHtml(a.filename || '') + '</div>' +
        '<div class="asset-desc">' + escapeHtml(a.description || a.summary || '') + '</div>' +
        '<div class="asset-tags">' + tags + '</div>';
      ul.appendChild(li);
    });
  }
  function iconForKind(k){
    return ({image:'🖼', audio:'🎵', video:'🎬', document:'📄'})[k] || '📦';
  }

  // ── 对话 ──
  async function loadConversations(){
    try{
      var r = await NianAuth.fetch('/api/memorials/' + state.currentId + '/conversations?limit=300');
      var d = await r.json();
      var box = $('convList');
      if (!d.conversations || !d.conversations.length){
        box.innerHTML = '<div style="color:#8a7654;text-align:center;padding:30px">还没有对话记录。</div>';
        return;
      }
      box.innerHTML = d.conversations.map(function(c){
        var role = c.role === 'user' ? '我' : '念念';
        return '<div class="conv-row ' + (c.role === 'user' ? 'user':'') + '">' +
          '<div class="role">' + role + '</div>' +
          '<div><div class="content">' + escapeHtml(c.content || '') + '</div>' +
          '<div class="ts">' + (c.ts || '') + '</div></div>' +
          '</div>';
      }).join('');
    } catch(e){ console.warn(e); }
  }

  // ── tabs ──
  document.querySelectorAll('.tab').forEach(function(t){
    t.addEventListener('click', function(){
      document.querySelectorAll('.tab').forEach(function(x){x.classList.remove('active')});
      document.querySelectorAll('.pane').forEach(function(x){x.classList.remove('active')});
      t.classList.add('active');
      document.querySelector('[data-pane="' + t.dataset.tab + '"]').classList.add('active');
    });
  });

  // ── 保存 ──
  $('btnSave').addEventListener('click', async function(){
    readInputsToDossier();
    try{
      var r = await NianAuth.fetch('/api/memorials/' + state.currentId + '/dossier', {
        method:'PUT', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({dossier: state.dossier})
      });
      var d = await r.json();
      state.dossier = d.dossier;
      // 同步 product_intent 到 meta
      var pi = (state.dossier.product_intent || {}).primary;
      if (pi !== undefined) {
        await NianAuth.fetch('/api/memorials/' + state.currentId, {
          method:'PATCH', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({product_intent: pi})
        });
      }
      $('btnSave').textContent = '已保存 ✓';
      setTimeout(function(){ $('btnSave').textContent = '保存修改'; }, 1500);
      loadMemorials();
    } catch(e){ alert('保存失败：' + e.message); }
  });

  // ── 删除 ──
  $('btnDelete').addEventListener('click', async function(){
    if (!confirm('确认删除该纪念对象？所有对话、资料、文件将一并删除。')) return;
    await NianAuth.fetch('/api/memorials/' + state.currentId, {method:'DELETE'});
    state.currentId = null;
    NianAuth.setActiveMemorialId('');
    $('detailBody').style.display = 'none';
    $('emptyHint').style.display = 'block';
    loadMemorials();
  });

  // ── 新建 ──
  $('btnNew').addEventListener('click', function(){ $('newModal').classList.add('show'); });
  $('newCancel').addEventListener('click', function(){ $('newModal').classList.remove('show'); });
  $('newCreate').addEventListener('click', async function(){
    var name = $('newName').value.trim();
    if (!name) { alert('请填写名字'); return; }
    var r = await NianAuth.fetch('/api/memorials', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({name: name, relation: $('newRelation').value.trim(), note: $('newNote').value.trim()})
    });
    var d = await r.json();
    $('newModal').classList.remove('show');
    $('newName').value=''; $('newRelation').value=''; $('newNote').value='';
    await loadMemorials();
    selectMemorial(d.memorial.memorial_id);
  });

  // 工具
  function escapeHtml(s){ return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
  function escapeAttr(s){ return escapeHtml(s).replace(/"/g,'&quot;'); }

  loadMemorials();
})();
