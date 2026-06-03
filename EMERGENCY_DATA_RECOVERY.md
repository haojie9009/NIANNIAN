# 🚨 紧急数据安全说明（必读）

## 这次为什么数据丢了？

最大嫌疑：**`render.yaml` 里有 `disk:` 段，Dashboard 又手动加过 Disk**。两边同时存在时，Render 每次 deploy 都可能：
- 重新创建一块全新的空盘并挂到 `/var/data`
- 原来那块盘被解挂、变孤儿，数据看起来"消失"

**已修复**：本次提交把 `render.yaml` 的 `disk:` 段注释掉了，统一以 Dashboard 上那块为准。

---

## 现在加了哪 4 道保险？

### ① 启动诊断（看 Render Logs 就知道有没有问题）
每次启动会打印：
```
[storage] DATA_DIR = /var/data
[storage] NIAN_DATA_DIR env = /var/data
[storage] DATA_DIR exists = True
[storage] 启动清点：3 用户，5 个纪念对象
```
**如果数字突然变 0**，会高亮打印 `⚠️⚠️⚠️ [DATA LOSS DETECTED]`，并在数据目录写 `DATA_LOSS_WARNING.txt`。

### ② 启动自动快照
每次 deploy 启动时，自动把 `/var/data` 整个打成 zip，存到 `/var/data/_backups/snapshot_YYYYMMDD_HHMMSS.zip`，**保留最近 10 份**。

### ③ 管理员接口（仅 owner）
```
GET  /api/admin/data/inspect                       # 看数据健康状况
POST /api/admin/data/snapshot                      # 手动快照
GET  /api/admin/data/snapshot/{name}/download      # 下载到本地电脑（终极保险！）
POST /api/admin/data/snapshot/{name}/restore       # 从快照恢复
```

**强烈建议每周下载一次快照到自己电脑**。命令示例（拿到 owner token 后）：
```bash
curl -H "Authorization: Bearer YOUR_TOKEN" https://你的域名/api/admin/data/inspect
curl -X POST -H "Authorization: Bearer YOUR_TOKEN" https://你的域名/api/admin/data/snapshot
curl -O -H "Authorization: Bearer YOUR_TOKEN" https://你的域名/api/admin/data/snapshot/snapshot_xxx.zip/download
```

### ④ 移除 render.yaml/disk 冲突（见上）

---

## 终极防丢方案：启用阿里云 OSS（5 分钟）

本地 Disk 再怎么保险，硬件故障一来都是空。**真正的零丢失方案是 OSS 双写**：每次保存数据时，同步推一份到阿里云对象存储。

### 启用步骤
1. 阿里云控制台 → 对象存储 OSS → 创建 Bucket（华东 / 华南任意，标准存储）
2. 创建 AccessKey（最小权限：只能读写这个 Bucket）
3. Render Dashboard → Environment 加 5 个变量：
   ```
   OSS_ENABLE           = 1
   OSS_ACCESS_KEY_ID    = LTAI...
   OSS_ACCESS_KEY_SECRET= xxxx
   OSS_BUCKET           = niannian-prod
   OSS_ENDPOINT         = oss-cn-hangzhou.aliyuncs.com
   ```
4. Save Changes → Render 自动重启 → Logs 应该看到 `[oss] enabled, bootstrap pulling from OSS...`

启用后任何情况下数据都丢不了：本地盘没了，下次启动会自动从 OSS 拉回来。

---

## 如果再发现数据丢了怎么办？

1. **第一时间不要再 push**（避免覆盖快照）
2. 用 owner token 调用 `/api/admin/data/inspect`，看 `snapshots` 列表
3. 找最近一个有效快照：`/api/admin/data/snapshot/snapshot_xxx.zip/restore`
4. 数据回来了

如果 `snapshots` 列表也是空的，说明 Disk 整个被换了 → 这就是为什么必须配 OSS。
