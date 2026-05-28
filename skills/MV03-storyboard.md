# MV03：分镜、提示词与口播文稿

**步骤序号**：`3 / 6`  
**Skill ID**：`MV03`  
**适用范围**：大屏数字人短片（单线叙事 + 画面蒙太奇 + 数字人口播）。

## 本子 Skill 的范围（与甲方一致）

**核心交付**：在用户信息之上生成 **视频级叙事模板**——分镜结构、每镜口播、画面文字描述与可作绘图提示的**纯文本**（`mj_prompt` 等）。**不写**具体调用哪家绘图 API、参数拼接、出图任务与存储（见 `平台集成与数据编排说明.md`）。

## 核心定位

在 **MV01 / MV02** 定稿数据上，一次性产出：

1. **风格与画面参数**（内嵌 `style_profile`，不拆独立 Skill）：视觉后缀、转场、色调与文案基调。  
2. **工业分镜表**：每镜时长、景别、画面描述、绘图/视频用 `mj_prompt`、运动、素材引用。**必须有足够多的人物主动动作镜头，避免全片缓慢静态。**  
3. **口播文稿**：与镜头对齐的旁白/讲述文本（数字人面向观者的讲述），**每镜 `voice_script` 与分镜一一对应**。

不再单独维护「整场追悼会仪式流」或「家属致辞 Skill」；若需第一人称金句，写入对应镜的 `voice_script` 即可。

## 前置依赖

**MV01**、**MV02**（**G2 已通过**）。可选：`target_duration_sec` 来自 MV02 或产品默认（如 300）。

## 人类闸门 G3

家属审阅：**分镜顺序 + 每镜提示词 + 每镜口播**；通过后方可进入 **MV04**。

**局部循环**：可按 **`scene_id`** 驳回单镜或仅驳回「提示词 / 口播 / 时长」之一并重算该镜；未驳回镜头保持冻结版本。

## 落地流程

1. 根据 `target_duration_sec` 拆镜并分配时长；总时长误差建议 ±5 秒内。  
2. 填写 `style_profile`（由原 `style_preference` / `emotional_intensity` 映射）。  
3. 每镜写 `mj_prompt`：人物外观可先用 MV01 肖像描述；**MV04 会把人物/场景/道具锁定为圣经**，MV05 画面生成必须以 MV04 锁定版本为准，因此 MV03 中出现的新场景/新道具应尽量显式写出，便于 MV04 收敛去重。**写 `mj_prompt` 时必须明确写出人物正在执行的具体动作（如 walking toward / raising hand / turning around / carrying something），不要只描述静态站姿或模糊的"standing"。**  
4. `mj_prompt` 句末不手写死画幅参数；顶层 `aspect_ratio` 表达成片比例意图，具体如何传给出图服务由工程处理。  
5. 优先引用 `user_asset`；否则标记 AI 生成。  
6. 负面提示词统一置于 `negative_prompt`（可全局一份）。

## 画面提示词（叙事模板的一部分）

- **画幅意图**：顶层 `aspect_ratio`（如 `16:9`），供脚本与分镜一致。  
- **风格后缀**：`style_profile.visual_suffix` 可为英文提示词片段；若内含模型版本占位，仅作文本约定，**不由本子 Skill 定义推理服务参数**。  
- **拼接顺序建议**（供人复制或工程拼接）：`mj_prompt` + 风格后缀 + 画幅相关参数——实现细节见《平台集成…》。

## 输出规范（仅输出合法 JSON）

输出 JSON 必须包含以下顶层字段：`target_duration_sec`、`aspect_ratio`、`style_profile`、**`character_bible`**、**`scene_library`**、`total_scenes`、`scenes`。

`character_bible` 是视觉一致性的核心锚点，用于 MV04/MV05 阶段 `build_scene_prompts()` 函数将角色 DNA 注入每一帧的 image_prompt 与 video_prompt，**必须填写**。

`scene_library` 为本片出现的主要场景建立视觉词汇库，每个场景用英文 `visual_descriptor` 精确描述环境光线、陈设、年代感，同样用于 MV05 阶段 Prompt 锚定，**每个 ai_generated 场景须有对应条目**。

```json
{
  "target_duration_sec": 300,
  "aspect_ratio": "16:9",
  "character_bible": {
    "display_name": "张建国",
    "character_dna": {
      "facial_features": "75-year-old Chinese man, deeply wrinkled face, kind gentle eyes behind silver rectangular glasses, silver-white hair combed back neatly",
      "body_features": "medium build, slightly hunched posture from years of labor, broad weathered hands with calloused fingers",
      "clothing_style": "dark grey Zhongshan suit (中山装), plain white inner shirt, black cloth shoes",
      "mannerisms": "moves with quiet purpose, tends to look down at his hands when thinking, gestures are small but meaningful, carries himself with earned confidence"
    }
  },
  "scene_library": [
    {
      "scene_id": "kitchen_morning",
      "scene_name": "清晨厨房",
      "visual_descriptor": "humble Chinese rural kitchen, clay stove, simple wooden cabinets, warm amber morning light filtering through a small window, steam rising from a clay pot, 1990s-2000s China aesthetic"
    },
    {
      "scene_id": "carpenter_workshop",
      "scene_name": "木工作坊",
      "visual_descriptor": "traditional Chinese carpenter's workshop, wooden tools and planes hanging on the wall, sawdust on the floor, workbench with half-finished furniture, golden dust particles in slanted morning sunlight through small window"
    },
    {
      "scene_id": "courtyard",
      "scene_name": "老宅院子",
      "visual_descriptor": "rural Chinese courtyard, old grey brick walls, large old locust tree providing shade, vegetable garden in the corner, earth-packed ground, warm afternoon sunlight, 1980s-2000s rural China atmosphere"
    },
    {
      "scene_id": "riverside_fishing",
      "scene_name": "河边钓鱼",
      "visual_descriptor": "peaceful Chinese rural riverbank, clear shallow water, reeds along the bank, weeping willows, soft morning mist, simple bamboo fishing rod, tranquil and timeless atmosphere"
    }
  ],
  "style_profile": {
    "style_id": "warm_nostalgia",
    "emotional_intensity": "moderate",
    "visual_suffix": "vintage 2000s Chinese home video aesthetic, warm golden sunlight, natural motion blur on moving subjects, authentic documentary feel, realistic texture --v 7",
    "color_palette": ["#FFD700", "#FFF8DC", "#D2B48C", "#8B4513"],
    "tone": "empathetic, gentle, conversational",
    "transition_style": "slow_crossfade"
  },
  "total_scenes": 3,
  "scenes": {
    "scene_01": {
      "scene_id": "scene_01",
      "scene_ref": "kitchen_morning",
      "time": "00:00-00:05",
      "shot_type": "Extreme close-up shot",
      "description": "小米粥在砂锅里慢慢沸腾，蒸汽袅袅",
      "voice_script": "退休后，他成了家里的依靠。每天早上五点起床煮粥，这一煮，就是四十年。",
      "asset_type": "ai_generated_video",
      "mj_prompt": "Extreme close-up shot of millet porridge boiling slowly in a clay pot, steam rising gently, warm amber morning light, humble Chinese rural kitchen background, soft focus, photorealistic",
      "negative_prompt": "ugly, deformed, blurry, extra limbs, disfigured, cartoon, anime, illustration",
      "motion": "gentle_push_in_with_rising_steam",
      "fallback_asset": "default_porridge.jpg"
    },
    "scene_02": {
      "scene_id": "scene_02",
      "scene_ref": "kitchen_morning",
      "time": "00:05-00:10",
      "shot_type": "Medium shot",
      "description": "他在厨房忙碌的背影，晨光斜射进来",
      "voice_script": "他话不多，却把爱都煮进了粥里。",
      "asset_type": "ai_generated_video",
      "mj_prompt": "Medium shot of elderly Chinese man's back silhouette in humble kitchen, dark grey Zhongshan suit, silver hair, warm amber morning light through small window, clay stove, steam, 2000s China rural aesthetic, photorealistic",
      "negative_prompt": "ugly, deformed, blurry, extra limbs, disfigured, cartoon, anime, illustration, young man, western style",
      "motion": "tracking_follow",
      "fallback_asset": "photo_01"
    },
    "scene_03": {
      "scene_id": "scene_03",
      "time": "00:10-00:15",
      "shot_type": "Medium shot",
      "description": "旧照：与孙辈合影",
      "voice_script": "",
      "asset_type": "user_asset",
      "asset_ref": "photo_05",
      "mj_prompt": null,
      "negative_prompt": null,
      "motion": "gentle_pull_back",
      "fallback_asset": "photo_05"
    }
  }
```

**运动词汇 `motion` 可选值与示例**（鼓励多样化使用，避免全部使用 slow_ 前缀）：

| 类型 | 值 | 效果 |
|------|-----|------|
| 缓慢运动 | `slow_pan_left` / `slow_pan_right` / `slow_zoom_in` / `slow_zoom_out` / `gentle_push_in` / `gentle_pull_back` | 情绪铺垫、环境展示 |
| 跟随运动 | `tracking_follow` / `tracking_side` / `tracking_behind` | 跟拍人物行走/做事 |
| 动态动作 | `handheld_shake` / `quick_pan` / `whip_pan` / `dynamic_zoom_in` / `orbit_around_subject` | 强调动作力度或情绪爆发 |
| 微动态 | `subtle_drift` / `floating` / `breathing_motion` | 照片活化、轻微生命感 |
| 组合 | `tracking_follow_with_rising_steam` / `gentle_push_in_with_handheld` | 自定义组合（用 with_ 连接） |

> **分镜动态分布要求**：8-16 个分镜中，至少 **40% 的镜头**需包含人物主动动作（如走路、挥手、做事、转身、互动等），不能仅用缓慢运镜或静态画面填充。每个含人物动作的镜头应在 `description` 和 `mj_prompt` 中明确写出人物正在执行的具体动作，并在 `motion` 字段选用"跟随运动"或"动态动作"类型与之匹配。

**场景字段说明**：
- `scene_ref`：对应 `scene_library` 中的 `scene_id`，MV05 阶段 `build_scene_prompts()` 会自动匹配并将 `visual_descriptor` 注入 image_prompt/video_prompt。**凡 `ai_generated` 场景必须填写。**
- `character_bible` 由 AI 根据 MV01 肖像描述与 MV02 定稿信息自动生成，字段 `facial_features`/`body_features`/`clothing_style`/`mannerisms` 须用**英文**填写，以便直接嵌入 image_prompt。
- `scene_library` 的 `visual_descriptor` 同样用英文写，描述粒度到：光线方向、陈设年代感、地面/墙面材质、空气质感。
- `voice_script` 用中文，是画面配音旁白，控制在 25个字以内（ 5 秒内），精炼有感染力，不要长句。
生成 8-16个分镜（scenes）, 除了第一个分镜，其他分镜按人物年龄从小到大排序。
**说明**： 具体 TTS/合成由工程执行。
