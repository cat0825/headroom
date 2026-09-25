<div align="center">

# headroom

**I need a reset.**

AI 有使用额度，你的大脑也该有。

[English](README.md) · **简体中文**

一个本地优先的 **Codex 插件 / Skill**，给大脑也配个日限额。<br>
发一句话，扣一点虚构的脑力。

[快速开始](#快速开始) · [任务栏托盘](#desktop-orb) · [界面语言](#界面语言) · [Jev 与 Laya](#scoring) · [隐私](#隐私)

</div>

| 剩余 100%–70% | 剩余低于 70% 至 30% | 剩余低于 30% |
| :---: | :---: | :---: |
| <img src="skills/headroom/assets/brain-full.png" width="110" alt="戴耳机的狗"> | <img src="skills/headroom/assets/brain-declining.webp" width="110" alt="流泪的猫"> | <img src="skills/headroom/assets/brain-low.png" width="110" alt="咆哮的狗"> |
| 再问最后一个问题。 | 这好像不是一个小问题。 | 这问题有问题啊！ |

**纯属娱乐，不是科学测量。** 这里的脑力负荷 / cognitive load 是好玩的估计，不代表智力、健康、疲劳程度或生产力。归零也不会阻止你聊天。

## 聊天时会发生什么？

- **按你的习惯给额度。** 取之前七个完整自然日中，用户消息最多的一天，消息数乘以二。这个窗口没有历史消息时，暂用两点。
- **每条符合条件的消息扣一点。** 一轮直接互动得到 0–10 分；同一个 session/turn ID 重试只扣一次。
- **跨会话共用一个余额。** 本地共享账本驱动百分比、进度条和表情。显示「已用 12.50 / 334 点」，不额外显示剩余多少点。
- **刷新不扣点。** 面板每 10 秒自动刷新；刷新和切换界面语言都不扣点。不需要每发一句话就新建会话。
- **收进任务栏。** 一个小 H 托盘图标，点击才展开用量卡片。直接读取本地账本，不用浏览器标签页，也不占桌面位置。

自然日按 **Asia/Shanghai（UTC+8）** 划分。计算七天基准时会统计**所有**记录为 `userMessage` 的消息，包括自动任务；来源筛选只影响扣点，不影响额度基准。

## 快速开始

网页面板中，点击表情图片会先向上跳动，再原地播放一段有声音的视频。第一次点击播放「诶大狗」。两次图片点击间隔小于 3.5 秒时，从其余八段（大狗叫 1–5、叫叫、大狗叫叫叫哒哒哒、INDUSTRY BABY）中随机播放一段，不连续重复当前片段；间隔达到 3.5 秒则从诶大狗重新开始。「大狗叫叫叫哒哒哒」和「INDUSTRY BABY」只播放音频，当前脑力状态图片随实时音频强弱和节拍峰值上跳、旋转、缩放，结束或切换时复原（系统开启减少动态效果时关闭律动），其余七段播放视频；播完恢复当前脑力状态图片，不自动连播。播放中再点会停止当前片段，按同样的点击间隔规则选取片段；图片按钮获得焦点时按 Esc 可停止。余额刷新不打断播放，播放视频不扣脑力值。卡片右下角的小喇叭图标可随时切换声音开 / 静音，不打断播放；浏览器记住设置，静音时图片律动继续。

当前自动安装流程面向 **Windows + PowerShell 上的 Codex**。需要 Python 3.10+，以及本机 Codex 历史索引 `thread_history_1.sqlite`。Mock 和网页面板只用 Python 标准库。

```powershell
git clone https://github.com/llm-learner/headroom.git
cd headroom
python skills/headroom/scripts/headroom.py status
python -m pip install -r skills/headroom/requirements-desktop.txt
pythonw skills/headroom/scripts/headroom_desktop.py --lang zh
```

任务栏右侧会出现小 H 托盘图标，可能在隐藏图标的箭头里。点击展开用量卡片，悬停查看剩余百分比。这一步只读，不会开启自动扣点。如果需要在终端看启动报错，用 `python` 替换 `pythonw`。

### 开启自动扣点

在仓库根目录审查并执行：

```powershell
powershell -ExecutionPolicy Bypass -File skills/headroom/hooks/install_windows.ps1
```

在 Codex `/hooks` 中审查并信任 headroom 的 **SessionStart** 和 **UserPromptSubmit**。首次安装钩子后，新开一个会话验证激活。之后在同一个会话继续发消息就能扣点，**不需要反复新建会话**。

`SessionStart` 在 Windows 上启动托盘显示（其他平台启动网页面板）；`UserPromptSubmit` 在提交消息后异步评分，**不是等助手回复结束才评分**。数值会在评分完成、界面刷新后出现。安装器不会覆盖已存在的 `hooks.json`；有其他钩子时请合并两条定义，不要直接强制覆盖。

要把 `$headroom` 作为 Codex Skill 使用，可以通过本地插件市场安装这个仓库，或把 `skills/headroom` 复制到 Codex 的 skills 目录。仅安装插件 / Skill 不会自动启用或信任生命周期钩子。请保留钩子指向的源目录。

<a id="desktop-orb"></a>

## 任务栏托盘（Windows）

- **单击**小 H 图标展开 / 收起；卡片右上角的 `−` 和 Escape 也可以收起。悬停图标显示剩余百分比。
- 启动时加 `--show` 可直接展开卡片（先退出已有显示）。
- 卡片出现在托盘附近，限制在显示器的可用工作区内。Windows 可能先把图标放进**隐藏图标**箭头里，可以手动拖到外面常驻。
- **右键 → 退出 headroom**只关闭显示，不关闭扣点。下一次 `SessionStart` 可以重新打开它。
- 卡片显示精确的**剩余百分比**、已用 / 日限额、当前表情和刷新按钮。
- **点击表情**就能播放与网页版相同的片段和随音乐律动的动画。小喇叭按钮会记住静音状态；收起卡片或切换语言会停止播放。展开时不会自动出声，播放也不扣点。
- 点击卡片的 **EN / ZH** 按钮即时切换语言。启动参数是 `--lang en` 或 `--lang zh`，优先级为：命令行 → `HEADROOM_LANG` → 记住的语言 → 中文。修改启动参数前先退出已经运行的显示。
- 同一账本、同一个 Windows 登录会话只显示一个入口，多个 Codex 会话不会重复生成。每十秒在后台读取一次用量，刷新不调用评分器。

动态托盘卡片需要带 **Tk** 的 Python、**pystray**、**Pillow**、**pywebview** 和 **Microsoft Edge WebView2 Runtime**（Python 依赖见 `requirements-desktop.txt`）。它是原生小窗口，不打开浏览器标签页，也不依赖 8766 网页面板。内部只读服务只监听 `127.0.0.1` 的随机空闲端口，用来读取打包素材和用量，退出显示时关闭。仅把位置 / 语言 / 静音偏好保存在账本旁的 `desktop.json`。它跟随可信任的 Codex `SessionStart` 启动，**没有添加 Windows 登录自启**。

如果没有 WebView2，退出显示后可用 `--renderer tk` 启动原来的静态托盘卡片，不带播放器，也不监听端口。

如果仍想用可拖动的悬浮球，退出托盘显示后加 `--mode orb` 启动。默认是 `--mode tray`；也可以用 `HEADROOM_DESKTOP_MODE=tray|orb` 指定启动模式。悬浮球模式不需要 pystray，Pillow 仍为可选，没有对应图片支持时用文字表情代替。

如需选择以后会话启动什么界面，可在钩子的环境里设置 `HEADROOM_DISPLAY`：`desktop`（Windows 默认）、`web`、`both` 或 `off`。只改变显示，不影响扣点。网页版仍可手动使用：

```text
python skills/headroom/scripts/headroom_dashboard.py --lang zh
```

打开[中文网页面板](http://127.0.0.1:8766/?lang=zh)。已有服务时直接打开网址，不要在同一端口重复启动。

## 界面语言

| 用法 | 英文 | 中文 |
| --- | --- | --- |
| 立即切换正在运行的面板 | [`?lang=en`](http://127.0.0.1:8766/?lang=en) | [`?lang=zh`](http://127.0.0.1:8766/?lang=zh) |
| 启动服务时指定默认语言 | `--lang en` | `--lang zh` |
| 通过服务进程的环境变量设置默认语言 | `HEADROOM_LANG=en` | `HEADROOM_LANG=zh` |

优先级：**网址参数 → 命令行参数 → 环境变量 → 默认中文**。不支持的网址语言值会回退到服务默认语言。英文模式覆盖标签、加载 / 错误提示、时间格式和图片说明。语言只影响显示，不影响评分、账本或 UTC+8 重置时间。已运行的面板用网址参数即可切换；启动参数在下次启动服务时生效。

<a id="scoring"></a>

## Jev、Laya 和 Mock 评分

| 后端 | 当前状态 | 提示词发送到哪里 |
| --- | --- | --- |
| **Mock**（`mock`） | 已包含；默认使用、确定性的虚构评分 | 本机进程内，不联网 |
| **Laya**（`laya`） | 已包含适配器，需自行部署本地 Laya 服务 | 固定回环地址：`http://127.0.0.1:8765/predict` |
| **Jev API** | **尚未实现**，是后续接入方向 | 当前版本没有 Jev / 云端请求 |

最初的原型设想使用 Jev 风格的评分 API，因此 Mock 的 provider 名叫 `jev-mock-local`。**它不是真实 Jev 调用**；headroom 也不是 Jev 或 Laya 的官方产品。

### 使用本地部署的 Laya

单独运行兼容的 Laya 服务，再把以下内容放到 `$CODEX_HOME/headroom/config.json`（默认 `~/.codex/headroom/config.json`）：

```json
{"backend": "laya"}
```

钩子**每轮都会重新读取**这个文件，已有会话也生效。改成 `mock` 即可切回演示评分。设置了 `HEADROOM_BACKEND` 时以环境变量为准。配置错误或 Laya 不可用时**跳过扣点**，不会偷偷降级成假分数。这个配置不会启动 / 下载模型，也不改变 CLI 显式传入的 `--backend`。

适配器发送的 JSON 中，`state.body` 是当前提示词，`questions.brain_load` 是评分问题。返回值要求 `answers.brain_load.type` 为 `"score"`，且 `answers.brain_load.score` 是 **0–10 的有限数值**。仓库**不打包模型权重或模型服务**。客户端对这条回环请求绕过系统 / HTTP 代理。

## 隐私

**本地评分，本地账本，不需要 headroom 云账号。** 当前自带的后端不添加云端评分请求或遥测。

| 数据 | headroom 如何处理 |
| --- | --- |
| 过去的 Codex 对话 | 只查询消息类型 / 时间戳元数据来计数，不读取历史提示词正文 |
| 当前符合条件的提示词 | 在内存中传给 Mock 或配置的本地 Laya 服务 |
| 扣点账本 | 保存不透明事件 ID、本地日期、数值分数和 provider，不保存提示词 |
| 钩子诊断 | 只保留最近一次结果、时间、输入是否存在 / 长度以及评分元数据，不保存提示词或原始 session/turn ID |
| 托盘 / 桌面显示 | 读取本地历史元数据和账本；动态卡片使用临时回环服务和打包素材，不调用云端评分；只保存位置 / 语言 / 静音偏好 |
| 面板 | 仅监听 `127.0.0.1`；图片随仓库附带，不加载 CDN 或统计脚本 |

默认共享账本是 `$CODEX_HOME/headroom/ledger.sqlite3`（未设置时为 `~/.codex/headroom/ledger.sqlite3`），可用 `HEADROOM_STATE_PATH` 覆盖。不要公开运行时状态、诊断文件、Codex 历史或凭据。

上述说明针对 **headroom**，不改变 Codex 自身的数据处理方式。单独部署的模型服务可能有自己的日志和联网行为，需要自行配置。未来若接云端 Jev，必须显式选择启用，并单独说明哪些数据会离开本机。

## 哪些对话会扣点？

目标是只计算人主动参与的对话，不计算无人值守的工作。钩子会拒绝已知非交互来源和 plan 模式；手动评分 CLI 也会拒绝非 normal 模式或未确认来源。

**自动识别来源是尽力而为，不是精确保证。** 当前钩子载荷不一定能可靠标识 Goal、自动任务或子代理。为兼容正常对话，缺少来源字段时默认接受；`HEADROOM_HOOK_STRICT=1` 会拒绝这类消息，但也可能漏掉普通聊天。不要把这个娱乐性筛选当成审计级规则。

<details>
<summary>排障与开发</summary>

- 不扣点？检查 `/hooks` 的信任状态、选择的后端和本地 Laya 是否可用。账本旁的 `last-hook.json` 记录最近结果。`HEADROOM_DEBUG_PATH` 可改路径，设为空字符串则关闭诊断。
- Windows PowerShell 钩子里，被引号包围的可执行文件路径需要 `&` 调用。钩子按 UTF-8 解码，支持 GBK 系统上的中文和 emoji。
- 修改钩子命令后，需要重新审查该定义。已被钩子覆盖的轮次不要再手动执行 `turn`，否则不同事件 ID 可能导致重复扣点。
- 切换语言、打开面板、点刷新都只读。但在聊天里发一句「查余额」仍是用户消息，可能被钩子正常计费。

使用临时历史和账本运行回归检查：

```text
python -m unittest discover -s skills/headroom/tests -p "test_*.py" -v
node skills/headroom/tests/test_dashboard_mood.js
```

JavaScript 测试需要 Node.js 和 Python，可用 `HEADROOM_TEST_PYTHON` 指定 Python 路径。使用临时账本的生命周期冒烟测试必须设置 `HEADROOM_DISABLE_DASHBOARD=1`。HTTP 测试使用临时回环端口，不占用实际面板端口。

</details>

## Contributors

- [TOGET-H](https://github.com/TOGET-H)
- [cat0825](https://github.com/cat0825)
- [fuxiuht](https://github.com/fuxiuht)

欢迎参与贡献，请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。
