# 微信小号新消息提醒

> 在 Windows 上只读地盯着一个登录的微信小号：有人发消息，就用 **Bark / ntfy / 邮件 / webhook** 推一条到你手机。
> **不点击、不切换会话、不发消息、不标记已读。**

这个项目适合「电脑上登着一个微信小号，只想第一时间知道有人找它」的场景。
它每 1.5 秒对微信窗口截一次图，只做 OCR 和像素比对；截图不会离开本机。

---

## 功能

- **只读**：不模拟鼠标键盘、不打开会话、不碰微信进程/内存/数据库。
- **省 CPU**：会话列表像素没变就跳过 OCR。
- **三重判定**：未读红点 + 预览文字实质变化 + 0.3 秒后该行像素稳定，才推送。
- **自适应缩放**：100% / 125% / 150% / 175% 等 DPI 自动换算坐标（窗口大小变了也不用改代码）。
- **抗 OCR 抖动**：
  - 名字去标点后做相似匹配，同一会话不会因为 OCR 换写法被当成新会话；
  - 自动清理名字末尾的公司标签/省略号；
  - 长名字把时间粘进名字行时，会把时间回收成 `time`；
  - 单字消息、单字名字有专门的低阈值重试。
- **掉线保护**：微信退出/登录界面出现时只推 **一条**「已掉线/界面异常」，程序原地等待，不重启、不多开窗口；重新登录自动恢复。
- **运行时自检**：每 10 秒写心跳；`tools/selfcheck.py` 可注册成 10 分钟计划任务，检查进程、心跳、窗口、黑屏、登录界面、日志异常。
- **可换渠道**：Bark（推荐）、ntfy、邮件 SMTP、Server 酱、任意 webhook。

## 工作原理

```
每 1.5 秒截一次微信窗口
        ↓ 列表像素没变就跳过（不跑 OCR）
OCR 识别左侧会话列表（名字 / 预览 / 时间 / 未读红点）
        ↓
① 该行有未读红点  ② 预览文字实质变化  ③ 0.3 秒后像素稳定
        ↓
Bark / ntfy / 邮件 / webhook 推送（失败重试 3 次，再失败进补推队列）
```

## 环境要求

- Windows 10 / 11
- 微信 **4.x**（按 4.1.15 的界面适配；3.9 老界面不适用）
- Python **3.10+**（安装时勾选 *Add Python to PATH*）
- 微信主窗口**不能最小化**（被其他窗口盖住没关系；最小化后截不到画面）

## 安装

### 方式一：下载 Release 压缩包

解压后进入项目目录，右键「在终端中打开」：

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1 -Autostart
```

它会：查 Python → 建 `.venv` → 装依赖（pywin32 / pillow / rapidocr-onnxruntime，约 100 MB）
→ 验证 OCR → 建桌面快捷方式 → 建开机自启 → 关闭插电自动睡眠。

不想要开机自启就去掉 `-Autostart`。

### 方式二：git clone

```powershell
git clone https://github.com/<your-name>/wechat-msg-notifier.git
cd wechat-msg-notifier
powershell -ExecutionPolicy Bypass -File install.ps1 -Autostart
```

## 配置（填推送渠道）

把示例配置复制成正式配置：

```powershell
Copy-Item notify-config.example.json notify-config.json
```

编辑 `notify-config.json`，至少填一个渠道：

```json
"channels": {
  "bark": "https://api.day.app/你的BarkKey"
}
```

Bark App 首页会给这个 URL；也可以填 `ntfy` / `email` / `serverchan` / `webhook`。

常用参数：

| 字段 | 默认 | 说明 |
|---|---|---|
| `poll_interval` | 1.5 | 轮询间隔秒。1.0 更快但更费 CPU |
| `exclude` | 大号/公众号/服务号/文件传输助手… | 不推的会话（按名字精确匹配） |
| `only_from` | [] | 白名单，只推这些人 |
| `quiet_hours` | [] | 免打扰，如 `["23:30","07:30"]` |
| `per_chat_cooldown` | 2 | 同一会话两次推送最小间隔秒 |
| `detail` | preview | `preview` 带消息内容；`brief` 只说“谁 + 时间” |
| `require_unread` | true | 必须有红点才推；关掉会更灵敏也更容易误报 |
| `push_unrecognized` | false | 名字完全认不出的会话是否也推 |

> 从**大号**或**文件传输助手**发给小号的消息默认不提醒（它们在 `exclude` 里）。
> 想收到自己的测试消息，把对应名字从 `exclude` 删掉。

### Bark（iPhone 推荐）

Bark 是 iOS 上的免费推送 App，不用注册；也支持自建服务端。

**1. 装 App、拿 URL**

App Store 搜索「Bark」安装。打开 App 首页，会看到一条形如

```
https://api.day.app/你的Key
```

的推送地址（每台设备一个 Key），点一下即可复制。

**2. 填进配置**

```json
"channels": {
  "bark": "https://api.day.app/你的Key",
  "bark_group": "微信",
  "bark_sound": "",
  "bark_level": "timeSensitive",
  "bark_ttl": 600
}
```

> 必须填**完整 URL**（`https://api.day.app/Key`）。只填 Key 不会工作。

**3. 测一条**

```powershell
.\.venv\Scripts\python.exe watch_notify.py --test
```

手机应弹出「测试推送」。收不到看下面的排查。

**Bark 可调参数**

| 字段 | 默认 | 作用 |
|---|---|---|
| `bark_group` | 微信 | 通知分组名，Bark App 里按组折叠 |
| `bark_sound` | 空 | 通知铃声，如 `alarm`、`bell`；留空用系统默认 |
| `bark_level` | timeSensitive | iOS 通知级别：`active` 普通、`timeSensitive` 穿透专注模式、`passive` 静默。收不到提醒时保留 `timeSensitive` |
| `bark_ttl` | 600 | Bark 服务器保留消息的秒数；`0` 表示不删除。App 本地历史仍保留（代码固定 `isArchive=1`） |

**手机端建议**

- 允许 Bark 通知、打开声音；把 Bark 排除在「专注模式」限制之外。
- 如果 **Bark App 历史里有消息、但手机不弹窗**：多半是专注模式或「静音投递」，
  `bark_level=timeSensitive` 可以穿透；仍不行再检查 iOS 通知权限。
- 隐私：iOS 设置 → 通知 → Bark → 显示预览 →「解锁时」或「永不」；
  或把配置里的 `detail` 改成 `brief`，推送只带「谁 + 时间」。
- Bark App 里可以给不同分组设不同铃声，`bark_group` 就是分组名。

**自建 Bark 服务端**

把 `channels.bark` 里的 `https://api.day.app` 换成你自己的域名，路径仍是 `/<Key>`：

```json
"bark": "https://bark.example.com/你的Key"
```

Key 在自建服务端的配置里生成。

**Bark 排查**

| 现象 | 检查 |
|---|---|
| 手机完全没反应 | URL 是否完整、Key 是否正确；iOS 通知权限；是否静音/专注模式 |
| 日志写「推送失败（bark）」 | 电脑能否访问 `api.day.app`；代理/防火墙；Bark 服务端是否正常 |
| 有通知但没声音 | `bark_sound` 是否填了不存在的铃声；手机静音开关 |
| 通知里看不到内容 | `detail` 是否为 `brief`；iOS 通知预览是否被隐藏 |
| 换手机后收不到 | 新设备 Key 不同，更新 `channels.bark` |

**Bark 安全**

Bark Key 等于「谁拿到都能给你推消息」。不要提交到 git
（`.gitignore` 已排除 `notify-config.json`）；怀疑泄露时在 Bark App 里重置 Key 并更新配置。

## 验证（4 步，缺一不可）

```powershell
# 1) 环境 / 窗口 / 会话列表
.\.venv\Scripts\python.exe tools\doctor.py

# 2) 看看每行会话认得对不对
.\.venv\Scripts\python.exe watch_notify.py --check

# 3) 手机能不能收到（会发一条“测试推送”）
.\.venv\Scripts\python.exe watch_notify.py --test

# 4) 让朋友给小号发一条真消息，几秒内手机应收到「微信：某某」
```

合格的 `doctor` 输出：没有 `[XX]`，能看到 `窗口 1342x971 缩放 1.50x` 和若干行会话。
`--check` 里每行都应该有名字/预览；`不确定` 越少越好。

启动常驻：

```powershell
.\.venv\Scripts\python.exe watch_notify.py
```

也可以双击 `notify.bat` 或桌面「微信消息提醒」。

## 24×7 值守设置

```powershell
powercfg /change standby-timeout-ac 0      # 不自动睡眠
powercfg /change hibernate-timeout-ac 0    # 不自动休眠
powercfg /change monitor-timeout-ac 10     # 屏幕可以关
# 笔记本合盖不休眠（插电/电池）
powercfg /setacvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /setdcvalueindex SCHEME_CURRENT SUB_BUTTONS LIDACTION 0
powercfg /setactive SCHEME_CURRENT
```

还需要人工确认：

- **不锁屏**：设置 → 账户 → 登录选项 →「需要登录」设为「从不」；关掉屏保。
  Windows 锁屏后普通窗口不再渲染，`PrintWindow` 会抓到黑屏，程序读不到列表。
- **微信不最小化**：窗口放着别最小化；被别的窗口盖住没关系。
- **别把电脑放进包里**：合盖运行注意散热。

## 自检

常驻进程每 10 秒写一次 `heartbeat.json`。独立自检脚本：

```powershell
.\.venv\Scripts\python.exe tools\selfcheck.py          # 人工看
.\.venv\Scripts\python.exe tools\selfcheck.py --notify # 有问题才推手机
```

注册成每 10 分钟的计划任务（只报异常，**不会自动重启、不会多开窗口**）：

```powershell
powershell -ExecutionPolicy Bypass -File tools\install_selfcheck.ps1
```

双击 `自检.bat` 也能跑。检查项：进程/端口、心跳新鲜度、窗口是否最小化、截图是否全黑、
能否认出会话、是否出现「登录/取消/正在进入」等登录界面文字、最近日志有无 ERROR/WARNING。

## 常见问题

**完全不推送**
看 `notify.log` 末尾：会写「窗口被最小化」「已掉线」「没有未读红点」等。
依次检查：微信是否登录、窗口是否最小化、锁屏是否关闭、Bark key 是否正确。

**公众号/服务号也推了**
它们应在 `exclude` 里。若名字 OCR 抖动导致漏排除，日志会写 `跳过 xxx：在排除名单里`。

**单字消息/单字名字读不出**
本版已做专门重试；仍失败时，给对方加个 2~3 字备注（如「张三」）最有效。

**同一条消息推了两次**
通常是 OCR 把括号/省略号读成了不同字符。本版会把 `【】「」〔〕（）` 归一化后再比相似度。

**微信退出后程序一直弹窗/重启**
本版不会自动重启；掉线只推一条，然后原地等待。重新登录后自动恢复。

**提示「推送失败（bark）」**
检查代理/网络；Bark key 是否正确；也可以临时改用邮件渠道验证。

## 隐私与风险

- 截图与 OCR **全部在本机完成**，不上传截图、不连接微信服务器。
- `notify-config.json` 里是你的推送 key，**不要提交到 git**（已在 `.gitignore` 中）。
- 本工具属于界面自动化，是微信用户协议不鼓励的用法；建议只在小号、低频使用，风险自负。
- 程序只读，不会替你发消息，也不会标记已读。

## 目录结构

```
watch_notify.py        主程序（只读提醒）
notify.bat             双击启动
自检.bat               双击自检
notify-config.example.json  配置模板（复制为 notify-config.json）
install.ps1            一键安装（-Autostart 带开机自启）
config.json            转发模式的配置（默认不用）
run.py                 转发模式入口（会模拟鼠标键盘，默认不用）
wfbot/
  watch.py             截图、OCR、判定
  sessionlist.py       会话列表解析
  ocr.py               RapidOCR 封装
  winapi.py            窗口 / 截图 / DPI
  notify.py            推送渠道
  forwarder.py         转发模式（可选）
tools/
  doctor.py            部署自检
  selfcheck.py         运行时自检
  install_selfcheck.ps1 注册自检计划任务
  status.ps1 / stop.ps1 状态 / 停止
部署与迁移.md           详细部署与迁移手册
CHANGELOG.md           版本更迭
```

## 版本

当前版本见 `VERSION`；更迭记录见 [CHANGELOG.md](CHANGELOG.md)。

## License

MIT，见 [LICENSE](LICENSE)。
