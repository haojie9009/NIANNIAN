# MV04：人物/场景/道具锁定（圣经）与试听

**步骤序号**：`4 / 6`  
**Skill ID**：`MV04`  
**适用范围**：大屏数字人短片。

## 本子 Skill 的范围（与甲方一致）

产出三类**内容侧资产圣经**并完成锁定绑定：

1. **人物圣经**：人物外貌特征文字稿、声线标签、（可选）不同情绪下的说话风格标签。
2. **场景圣经**：短片中允许出现的“固定场景集合”（以及每个场景的视觉与情绪基调文字稿）。
3. **道具圣经**：重要道具的外观、象征意义与出镜约束文字稿。

以及**供审听用的示例文案/引用键**（如 `preview_audio_ref` 仅作文本占位，表示「此处应对应一段试听」）。

本子 Skill 只做**内容与一致性锁定**，不写语音克隆算法、文件落盘路径、API/队列、数据库等工程实现。

## 核心定位

在 MV03 的分镜与口播定稿之后，把短片的“世界设定”收敛为三大圣经，并在 **G4** 通过时将其视为“锁定版本”：

- **人物**：以后所有镜头里出现的数字人/逝者形象描述与说话气质，都必须以本步锁定的人物圣经为准。
- **场景**：以后所有镜头里的背景/空间，只能从本步锁定的场景库中选用（或返回本步新增并重审）。
- **道具**：关键道具必须按本步锁定的道具库一致呈现，避免前后不一。

并基于 **MV03 已定稿的 `voice_script` 片段** 给出试听用文案来源（`preview_source_scene_ids`），供家属判断「像不像」与“气质对不对”。

## 前置依赖

**MV01**、**MV03**（**G3 已通过**）。

## 人类闸门 G4

人物/场景/道具三大圣经与试听确认通过后，方可进入 **MV05**。未通过则局部修订后重跑 MV04（只重跑被点名部分）。

**局部循环**（必须支持点名范围）：可仅驳回

- 人物圣经：某条 `character_dna` 子项、某条 `voice_profile`、或某个情绪说话风格；
- 场景圣经：单个 `scene_id` 的描述/基调；
- 道具圣经：单个 `prop_id` 的外观/约束；
- 试听：更换试听文本片段或调整声线标签；

其余已认可部分冻结不动。

## 落地流程

1. 归纳人物圣经：外貌、体态、标志物、穿着倾向、说话气质（缺失则追问补全）。  
2. 归纳声线标签：语速、音高、音色、口音；并给出**不同情绪下的说话风格标签**（例如：平静/温暖/哽咽克制等）。  
3. 归纳场景圣经：从 MV03 的镜头描述中抽取去重，形成“允许出现的场景集合”（每场景 2–4 句稳定描述）。  
4. 归纳道具圣经：从 MV01 记忆与 MV03 镜头里抽取关键道具，写出外观、象征意义与出镜约束。  
5. 选取与成片相关的 `voice_script` 片段作为试听文案依据，在输出中标注 `preview_source_scene_ids`；`preview_audio_ref` 为占位符，表示后续会挂载一段试听资源（本子 Skill 不生成音频文件）。  
6. 产出 `lock_manifest`：声明三大圣经的锁定版本号与锁定闸门（G4），供 MV05 引用；若 MV05 需要新增场景/道具，必须回到 MV04 局部循环并重新锁定。

## 输出规范（仅输出合法 JSON）

```json
{
  "character_bible": {
    "character_id": "deceased_01",
    "display_name": "张建国",
    "character_dna": {
      "facial_features": "75-year-old Chinese man, silver hair combed back, silver-rimmed rectangular glasses, deep smile lines at the corners of eyes",
      "body_features": "about 170cm tall, slightly thin",
      "clothing_style": "often wears a dark grey Zhongshan suit, black cloth shoes",
      "mannerisms": "puts hands behind his back when talking"
    },
    "voice_profile": {
      "pace": "slow",
      "pitch": "low",
      "texture": "slightly_raspy",
      "accent": "Shandong_dialect_mild",
      "sample_audio_ref": "audio_01"
    },
    "voice_style_by_emotion": {
      "calm": "slow pace, low pitch, clear articulation, restrained emotion",
      "warm": "slightly softer texture, gentle smile in tone, small pauses",
      "choked_but_controlled": "shorter phrases, longer pauses, avoid dramatic sobbing"
    }
  },
  "scene_library": [
    {
      "scene_id": "scn_kitchen_morning",
      "name": "清晨厨房",
      "description": "Warm morning kitchen, steam, simple Chinese home, warm sunlight, calm domestic feeling",
      "allowed_time_periods": ["retirement", "2010s"]
    }
  ],
  "prop_library": [
    {
      "prop_id": "prop_clay_pot",
      "name": "砂锅",
      "description": "Old clay pot with subtle wear, used for porridge; should look familiar and lived-in, not new",
      "symbolism": "daily care and quiet love",
      "appearance_constraints": ["no modern neon colors", "keep realistic texture"]
    }
  ],
  "voice_preview": {
    "preview_audio_ref": "tts_preview_from_mv03_script.wav",
    "preview_source_scene_ids": ["scene_01", "scene_02"]
  },
  "lock_manifest": {
    "locked_gate": "G4",
    "character_bible_version": "1.0.0",
    "scene_library_version": "1.0.0",
    "prop_library_version": "1.0.0"
  }
}
```
