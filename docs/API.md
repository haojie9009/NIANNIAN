# 念念 NianNian Memorial API — 接口文档

> Base URL: `http://localhost:8000/api`
> 所有接口均通过 FastAPI 提供，自动交互式文档见 `/docs`（Swagger UI）。

---

## 目录

- [健康检查](#健康检查)
- [认证 Auth](#认证-auth)
- [表单采集 Intake](#表单采集-intake)
- [追思对话 Chat](#追思对话-chat)
- [数字人对话 Dialogue](#数字人对话-dialogue)
- [流水线 Pipeline](#流水线-pipeline)
- [素材管理 Assets](#素材管理-assets)
- [智能体 Agent](#智能体-agent)
- [实时语音 Agent Realtime](#实时语音-agent-realtime)
- [纪念对象 Memorials](#纪念对象-memorials)
- [文件上传 Uploads](#文件上传-uploads)
- [页面路由](#页面路由)

---

## 健康检查

| 接口 | `GET /api/health` |
|------|-------------------|
| 功能 | 检查服务是否存活 |
| 触发时机 | 页面加载前探活、容器健康检查 |
| 对应页面 | 所有页面 |

```json
// 响应 200
{"status": "ok", "service": "niannian-backend"}
```

---

## 认证 Auth

> 需要认证的接口在请求头中携带 `Authorization: Bearer <token>`

| 接口 | 方法 | 路径 | 认证 |
|------|------|------|------|
| 注册 | `POST` | `/api/auth/register` | 否 |
| 登录 | `POST` | `/api/auth/login` | 否 |
| 访问码登录 | `POST` | `/api/auth/code` | 否 |
| 当前用户 | `GET`  | `/api/auth/me` | 是 |

### 注册
- **触发时机**: 用户首次注册账号
- **对应页面**: `/login`
- **请求体**: `{ "email": "xxx@xx.com", "password": "123456", "display_name": "昵称" }`
- **响应**: `{ "token": "...", "user": { "user_id", "email", "display_name", "is_owner" } }`

### 密码登录
- **触发时机**: 用户输入邮箱密码登录
- **对应页面**: `/login`
- **请求体**: `{ "email": "xxx@xx.com", "password": "123456" }`

### 访问码登录
- **触发时机**: 输入主人访问码快速登录（以 owner 身份）
- **对应页面**: `/login`
- **请求体**: `{ "code": "访问码" }`

### 获取当前用户
- **触发时机**: 页面刷新、token 验证
- **对应页面**: 所有需要登录的页面

---

## 表单采集 Intake

| 接口 | 方法 | 路径 | 认证 |
|------|------|------|------|
| 提交/更新表单 | `POST` | `/api/intake/submit` | 否 |
| 获取会话详情 | `GET` | `/api/intake/session/{sid}` | 否 |
| 测试数据 | `GET` | `/api/intake/test-data` | 否 |
| 深度搜索 | `POST` | `/api/intake/deep-search` | 否 |
| 应用搜索字段 | `POST` | `/api/intake/apply-fields` | 否 |

### 提交/更新表单
- **功能**: 提交或更新逝者信息采集表单；无 `session_id` 时新建
- **触发时机**: 用户在表单页点击"提交"或自动保存
- **对应页面**: `/memorial`
- **请求体**: `{ "session_id": "可选", "form_data": { "deceased_name": "...", "birth_date": "...", ... } }`

### 获取会话详情
- **功能**: 获取指定 session 的完整状态（表单数据、素材、聊天历史、流水线状态等）
- **触发时机**: 页面加载恢复之前 session
- **对应页面**: 所有页面

### 测试数据
- **功能**: 返回预设的测试人物数据（陈文斌）
- **触发时机**: 开发调试时快速填充数据
- **对应页面**: `/memorial`

### 深度搜索
- **功能**: 通过 AI Agent 深度搜索补充逝者信息，返回整理后的文本和结构化字段
- **触发时机**: 用户在"深度搜索"页输入查询后
- **对应页面**: `/deep_search`
- **请求体**: `{ "query": "查询内容", "extra": "补充信息", "session_id": "可选" }`

### 应用搜索字段
- **功能**: 将深度搜索提取的结构化字段写入当前表单
- **触发时机**: 用户在深度搜索结果页点击"应用到表单"
- **对应页面**: `/deep_search`

---

## 追思对话 Chat

| 接口 | 方法 | 路径 | 认证 |
|------|------|------|------|
| 开场问候 | `POST` | `/api/chat/greeting` | 否 |
| 发送消息 | `POST` | `/api/chat/message` | 否 |
| 生成结构化JSON | `POST` | `/api/chat/generate-json` | 否 |
| 预览生平 | `GET` | `/api/chat/preview/{sid}` | 否 |

### 开场问候
- **功能**: 基于表单数据生成 AI 追思开场白
- **触发时机**: 用户完成表单后进入对话页，AI 自动生成第一段追思文字
- **对应页面**: `/memorial`

### 发送消息
- **功能**: 用户发送消息，AI 基于逝者信息和对话历史回复
- **触发时机**: 用户在追思对话中输入消息并发送
- **对应页面**: `/memorial`
- **请求体**: `{ "session_id": "...", "message": "..." }`

### 生成结构化 JSON（MV01）
- **功能**: 对话结束后，将采集的信息整理为结构化 JSON，进入影像制作流水线
- **触发时机**: 用户点击"完成对话"或"开始制作影像"
- **对应页面**: `/memorial` → 跳转到 `/pipeline`

### 预览生平
- **功能**: 从表单数据拼接出简易生平文字预览
- **触发时机**: 页面加载时展示逝者信息摘要
- **对应页面**: `/memorial`

---

## 数字人对话 Dialogue

| 接口 | 方法 | 路径 | 认证 |
|------|------|------|------|
| 获取对话状态 | `GET` | `/api/dialogue/state/{sid}` | 否 |
| 上传聊天记录分析 | `POST` | `/api/dialogue/analyze` | 否 |
| 更新人格 | `POST` | `/api/dialogue/persona/update` | 否 |
| 对话 | `POST` | `/api/dialogue/chat` | 否 |
| 重置 | `POST` | `/api/dialogue/reset` | 否 |

### 获取对话状态
- **功能**: 返回数字人人格 DNA、名称、对话历史等
- **触发时机**: 页面加载时恢复状态
- **对应页面**: `/dialogue`

### 上传聊天记录分析
- **功能**: 上传聊天记录文件（txt）→ AI 分析逝者说话风格 → 生成人格 DNA
- **触发时机**: 用户上传聊天记录文件后
- **对应页面**: `/dialogue`
- **请求**: `multipart/form-data`，字段：`file`, `target_name`, `role_desc`, `session_id`

### 更新人格
- **功能**: 基于新输入的内容合并/修改人格设定；`clear=true` 清空自定义修改
- **触发时机**: 用户在人格编辑区输入补充说明
- **对应页面**: `/dialogue`

### 对话
- **功能**: 与数字人进行多轮对话，AI 基于人格 DNA 模仿逝者风格回复
- **触发时机**: 用户输入消息发送
- **对应页面**: `/dialogue`

### 重置
- **功能**: 清空对话历史；可选清空人格
- **触发时机**: 用户点击"重新开始"
- **对应页面**: `/dialogue`

---

## 流水线 Pipeline

| 接口 | 方法 | 路径 | 认证 |
|------|------|------|------|
| 运行单步 | `POST` | `/api/pipeline/run/{step}/{sid}` | 否 |
| 全部运行 | `POST` | `/api/pipeline/run-all/{sid}` | 否 |
| 查询状态 | `GET` | `/api/pipeline/status/{sid}` | 否 |
| 获取输出 | `GET` | `/api/pipeline/output/{sid}/{step}` | 否 |
| 流程预览 | `POST` | `/api/pipeline/preview/{sid}` | 否 |
| 分镜图片 | `POST` | `/api/pipeline/scene/image/{sid}/{idx}` | 否 |
| 分镜视频 | `POST` | `/api/pipeline/scene/video/{sid}/{idx}` | 否 |
| 角色档案 | `GET` | `/api/pipeline/characters/{sid}` | 否 |
| 分镜列表 | `GET` | `/api/pipeline/scenes/{sid}` | 否 |

### 运行单步
- **功能**: 执行流水线指定步骤（MV01 ~ MV04）
- **触发时机**: 用户在流水线页面手动触发某个 MV 步骤
- **对应页面**: `/pipeline`
- **step 取值**: `MV01`, `MV02`, `MV03`, `MV04`

### 全部运行
- **功能**: 串行执行 MV01 → MV02 → MV03，返回两段大白话气泡 + 分镜列表
- **触发时机**: 用户点击"一键生成"
- **对应页面**: `/pipeline`

### 查询状态
- **功能**: 返回流水线各步骤状态、闸门状态、已完成的输出
- **触发时机**: 页面轮询检查生成进度
- **对应页面**: `/pipeline`

### 获取输出
- **功能**: 获取指定步骤的完整输出结果
- **触发时机**: 用户点击查看某一步的详细结果
- **对应页面**: `/pipeline`

### 流程预览
- **功能**: 用大白话讲解即将进行的影像制作流程
- **触发时机**: 用户进入流水线页面时展示预览说明
- **对应页面**: `/pipeline`

### 分镜图片
- **功能**: 为单个分镜生成首帧图片（返回 data URL）
- **触发时机**: 用户点击分镜的"生成图片"按钮
- **对应页面**: `/studio`

### 分镜视频
- **功能**: 为单个分镜生成短视频（基于已有的首帧图片）
- **触发时机**: 用户点击分镜的"生成视频"按钮
- **对应页面**: `/studio`
- **请求体**: `{ "image_url": "图片 data URL 或 https URL" }`

### 角色档案
- **功能**: 返回主角 + 配角的档案信息
- **触发时机**: 加载分镜时展示角色信息
- **对应页面**: `/studio`

### 分镜列表
- **功能**: 返回 MV04 已生成的分镜列表（含已渲染的图片/视频缓存）
- **触发时机**: 进入 Studio 页面加载分镜编辑器
- **对应页面**: `/studio`

---

## 素材管理 Assets

| 接口 | 方法 | 路径 | 认证 |
|------|------|------|------|
| 上传素材 | `POST` | `/api/assets/upload` | 否 |
| 获取文件 | `GET` | `/api/assets/file/{name}` | 否 |
| 背景图 | `GET` | `/api/assets/background` | 否 |
| 素材列表 | `GET` | `/api/assets/list/{sid}` | 否 |

### 上传素材
- **功能**: 上传逝者相关素材（图片/视频/音频），支持 jpg/png/webp/gif/mp4/mov/m4a/wav/mp3
- **触发时机**: 用户在表单/采访页上传照片或文件
- **对应页面**: `/memorial`
- **请求**: `multipart/form-data`，字段：`session_id`, `period`, `file`

### 获取文件
- **功能**: 通过文件名获取上传的素材文件
- **触发时机**: 页面展示已上传的素材预览
- **对应页面**: `/memorial`, `/studio`

### 背景图
- **功能**: 返回默认背景图（OurDearFriend.jpg）的 base64 编码
- **触发时机**: 页面加载时设置背景
- **对应页面**: `/memorial`

### 素材列表
- **功能**: 获取 session 下所有已上传的素材
- **触发时机**: 页面加载素材库区域
- **对应页面**: `/memorial`, `/studio`

---

## 智能体 Agent

| 接口 | 方法 | 路径 | 认证 |
|------|------|------|------|
| 流式对话 | `POST` | `/api/agent/chat` | 可选 |
| 语音识别 | `POST` | `/api/agent/asr` | 否 |

### 流式对话（SSE）
- **功能**: 与「念念」智能体进行流式文字对话，支持资料库智能提取
- **触发时机**: 首页智能体对话、引导式信息采集
- **对应页面**: `/`（首页）
- **请求体**: `{ "message": "...", "history": [{"role":"user","content":"..."}], "memorial_id": "可选" }`
- **响应**: `text/event-stream` 流式返回文字

### 语音识别（ASR）
- **功能**: 将语音文件转写为文字（优先 Paraformer，降级 Whisper）
- **触发时机**: 用户按住语音按钮说话
- **对应页面**: `/`（首页）
- **请求**: `multipart/form-data`，字段：`audio`（音频文件）

---

## 实时语音 Agent Realtime

| 接口 | 方法 | 路径 | 认证 |
|------|------|------|------|
| 实时语音 WebSocket | `WS` | `/api/agent/realtime` | 否 |

### 实时语音 WebSocket
- **功能**: 浏览器 ↔ 本服务 ↔ 阿里云 Qwen-Omni-Realtime 的双向实时语音通道
  - 浏览器推 PCM16 16kHz mono base64
  - 服务端回 PCM16 24kHz mono base64
  - 文字流通过 `response.audio_transcript.delta`
- **触发时机**: 用户点击"实时语音"按钮进入语音对话模式
- **对应页面**: `/`（首页）
- **协议**: WebSocket，透传阿里云 Realtime 事件

---

## 纪念对象 Memorials

> 以下接口均需要登录认证

| 接口 | 方法 | 路径 | 认证 |
|------|------|------|------|
| 列表 | `GET` | `/api/memorials` | 是 |
| 创建 | `POST` | `/api/memorials` | 是 |
| 详情 | `GET` | `/api/memorials/{mid}` | 是 |
| 更新元数据 | `PATCH` | `/api/memorials/{mid}` | 是 |
| 删除 | `DELETE` | `/api/memorials/{mid}` | 是 |
| 获取资料库 | `GET` | `/api/memorials/{mid}/dossier` | 是 |
| 保存资料库 | `PUT` | `/api/memorials/{mid}/dossier` | 是 |
| 合并资料库 | `POST` | `/api/memorials/{mid}/dossier/merge` | 是 |
| 对话历史 | `GET` | `/api/memorials/{mid}/conversations` | 是 |

### 列表
- **功能**: 获取当前用户所有纪念对象
- **触发时机**: 进入资料库/首页列表页
- **对应页面**: `/library`

### 创建
- **功能**: 新建一个纪念对象
- **触发时机**: 用户点击"新建纪念"
- **对应页面**: `/library`, `/`
- **请求体**: `{ "name": "姓名", "relation": "关系", "note": "备注" }`

### 详情
- **功能**: 获取纪念对象的元数据、完整资料库、已上传素材
- **触发时机**: 点击进入某个纪念对象详情页
- **对应页面**: `/library`

### 更新元数据
- **功能**: 部分更新纪念对象的名称/关系/备注/产品意向
- **触发时机**: 用户编辑纪念对象基本信息
- **对应页面**: `/library`

### 删除
- **功能**: 删除一个纪念对象
- **触发时机**: 用户确认删除
- **对应页面**: `/library`

### 获取/保存/合并资料库
- **功能**: 读取、整体替换、增量合并某个纪念对象的资料库（dossier）
- **触发时机**: 资料库编辑页保存 / Agent 自动提取后合并
- **对应页面**: `/library`

### 对话历史
- **功能**: 获取与某个纪念对象的对话记录
- **触发时机**: 查看历史对话
- **对应页面**: `/library`

---

## 文件上传 Uploads

> 以下接口均需要登录认证，归属于某个纪念对象（`mid`）

| 接口 | 方法 | 路径 | 认证 |
|------|------|------|------|
| 上传文件 | `POST` | `/api/memorials/{mid}/upload` | 是 |
| 素材列表 | `GET` | `/api/memorials/{mid}/assets` | 是 |
| 获取文件 | `GET` | `/api/memorials/{mid}/assets/{aid}` | 是 |
| 更新素材信息 | `PATCH` | `/api/memorials/{mid}/assets/{aid}` | 是 |

### 上传文件 + 自动打标签
- **功能**: 上传文件到指定纪念对象，LLM 自动打标签（主题/年代/场景/情绪）并推断可用场景
- **触发时机**: 用户在资料库/素材管理页上传文件
- **对应页面**: `/library`
- **限制**: 单文件 ≤ 50MB
- **请求**: `multipart/form-data`，字段：`file`, `description`

### 素材列表
- **功能**: 获取纪念对象下所有素材
- **触发时机**: 加载素材管理页
- **对应页面**: `/library`

### 获取文件
- **功能**: 下载/预览素材文件
- **触发时机**: 点击查看/播放某个素材
- **对应页面**: `/library`

### 更新素材信息
- **功能**: 修改素材的描述、标签、可用场景
- **触发时机**: 用户编辑素材信息
- **对应页面**: `/library`

---

## 页面路由

| URL 路径 | 对应页面 | 说明 |
|----------|----------|------|
| `/` | `index.html` | 首页 — 念念智能体引导，文字/语音/实时语音对话 |
| `/login` | `login.html` | 登录/注册页 |
| `/library` | `library.html` | 资料库 — 纪念对象管理 + 素材管理 + 资料库编辑 |
| `/memorial` | `memorial.html` | 追思页 — 表单采集 + AI 追思对话 |
| `/deep_search` | `deep_search.html` | 深度搜索 — AI Agent 补充信息 |
| `/dialogue` | `dialogue.html` | 数字人对话 — 上传聊天记录 + 人格分析 + 多轮对话 |
| `/pipeline` | `pipeline.html` | 流水线 — MV 影像制作流程（结构化 → 分镜 → 图片 → 视频） |
| `/studio` | `studio.html` | Studio — 分镜编辑器，逐镜生成图片/视频 |

---

## 认证说明

- **Token 获取**: 注册或登录后返回 JWT token
- **Token 传递**: 请求头 `Authorization: Bearer <token>`
- **可选认证的接口**: `GET /api/auth/me` 等标注"可选"的接口，未登录时也能正常调用，只是不携带用户信息
- **Owner 访问码**: 通过 `POST /api/auth/code` 用主人访问码直接以 owner 身份登录
