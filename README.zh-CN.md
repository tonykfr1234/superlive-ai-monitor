<p align="center">
  <a href="README.md">English</a> · <strong>简体中文</strong> · <a href="README.zh-TW.md">繁體中文</a> · <a href="README.ja.md">日本語</a> · <a href="README.ko.md">한국어</a><br>
  <a href="README.de.md">Deutsch</a> · <a href="README.es.md">Español</a> · <a href="README.fr.md">Français</a> · <a href="README.pt-BR.md">Português (Brasil)</a> · <a href="README.vi.md">Tiếng Việt</a> · <a href="README.id.md">Bahasa Indonesia</a> · <a href="README.it.md">Italiano</a>
</p>

<p align="center"><img src="assets/superlive-ai.png" width="112" alt="SuperLive AI icon"></p>
<h1 align="center">截取监控画面进行人物与车辆移动检测</h1>
<p align="center">SuperLive AI Monitor · 支持 MuMu 与逍遥（MEmu）安卓模拟器</p>
<p align="center">
  <img src="https://img.shields.io/badge/platform-Windows-0078D6" alt="Windows">
  <img src="https://img.shields.io/badge/Python-3.12-3776AB" alt="Python 3.12">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT 许可证"></a>
</p>
<p align="center"><a href="#quick-start">快速开始</a> · <a href="#workflow">工作流程</a> · <a href="#features">功能</a> · <a href="#demo">演示</a> · <a href="#faq">常见问题</a> · <a href="#documentation">文档</a></p>

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\installer\INSTALL_FROM_REPO.ps1
```

---

新版图标与高画质录像：本地保留采集原尺寸，以 H.264 CRF 18 录制；Telegram 优先发送同一份高画质原片，仅在超过上传限制时建立临时压缩副本，成功后删除副本。

SuperLive AI Monitor 为 Windows 上 MuMu 中的 SuperLive 实时画面添加人物、车辆检测与事件录像。YOLO 检测所选多边形内的人物或车辆活动，程序录制视频，并通过 Telegram 发送已结束的视频，无需摄像头的 RTSP 地址。

这是非官方社区项目，与 SuperLive 或 MuMu 厂商无隶属关系。**12 种语言仅用于文档翻译；程序预览界面目前使用繁体中文。**

**2026-10-21 更新：** 人物或车辆（汽车、摩托车、巴士、卡车）移动均可触发。120 秒静默与 120–300 秒录像规则不变，每日 CSV 仍为静默例外。Telegram 区分人、车与实际车牌；未确认就标记未确认。车牌采用全画面及重叠区域扫描，OCR 程序异常时自动恢复。界面可用滚轮、捲轴及 PageUp／PageDown 查看最多七天的辨识记录，包含原片补辨识、未识别、未确认与失败结果。

默认展开符合本机名单、两码容错内的已确认结果；其他记录收折保留。点击视频文件名可在 Windows 文件资源管理器中选中原始视频。

识别状态、画质和批次进度集中在底部按钮区。默认让监控画面使用完整宽度；点击“車牌紀錄”展开或收起下方记录列表。

<a id="quick-start"></a>
## 快速开始

1. 安装 MuMu 和 SuperLive，登录并打开目标摄像头的实时画面，保持 Windows 用户会话登录。
2. 克隆或下载仓库，执行 `installer\INSTALL_FROM_REPO.bat`，或运行上方 PowerShell 命令。
3. 执行 `installer\SET_TELEGRAM.bat`，在本机配置自己的 Bot 和 Chat；仓库不包含凭证。
4. 保持实时画面打开，运行 `installer\VERIFY_ALL.bat` 检查安装状态；此检查不等于验证 Telegram 视频发送成功。
5. 运行 `%USERPROFILE%\Downloads\SuperLive_AI\Start_AI_Monitor.bat`；停止时运行同目录中的 `Stop_AI_Monitor.bat`。

**更新已有安装：** 执行 `installer\UPDATE_FROM_REPO.bat`。更新器先备份并停止程序，保留本机 Telegram、检测区域和存储设置。完成后保持停止状态，请确认画面后手动启动；不会创建开机启动项、计划任务或 Windows 服务。

<a id="workflow"></a>
## 工作流程

1. 多边形内出现新的人物或车辆，或已有目标再次移动时，开始录像并保留 Windows 通知和本机证据；不发送 Telegram 开始照片。
2. 每段录像的**前 120 秒继续检测，但不自动切段、不发起新的 Telegram 发送**。
3. 录像满 120 秒后、未满 300 秒时，检测到人物或车辆移动就立即结束旧片段并开始新片段；新片段重新进入 120 秒静默期。
4. 连续 **120 秒没有人物或车辆移动**则结束录像并继续等待；每次移动都会重设无移动计时。单段仍以 **5 分钟**为上限。
5. 已结束的视频按实际时长保存并加入持久化后台队列，等当前静默期结束后才可通过 Telegram 发送。
6. 因无移动而结束后，等待再次移动或新人进入才重新录像；同一个人静止不动不会立即重复触发。

**自动 Telegram 发送已结束的视频、已确认车牌及每日 CSV 报表。** 静默期也会暂停旧视频和失败重试的新发送，因此连续切段可能推迟整个队列。静默开始前已发出的网络请求仍可能在静默期间送达，程序无法撤回。

手动停止或采集故障可保留不足 120 秒的短片，不会补帧伪造额外时长；这些片段仍需等待原静默期限及任何新片段的静默期结束。

启动后首次看到人物或车辆也算新活动。没有目标或目标全部静止都属于无移动。选择区域、画面不可用或检测结果未知时会重设观察区间，恢复后重新累计有效观察时间，不把故障当成无人移动。

<a id="features"></a>
## 功能

| 功能 | 行为 |
| --- | --- |
| 多边形检测 | 支持凹多边形，并兼容旧矩形设置；人物或车辆框必须与区域有正面积重叠 |
| 事件录像 | 正常片段为 120–300 秒；满 120 秒后移动立即切段，连续 120 秒无移动则结束并等待 |
| 画面分离 | 排除 MuMu 标题和菜单栏；录像保留完整摄像头画面，预览按钮不进入视频 |
| Telegram | 自动发送已结束的 H.264 MP4、已确认车牌及每日 CSV；每段前 120 秒暂停整个队列的新发送与重试 |
| 空间管理 | 默认目录 `C:\SuperLive_Recordings`，目标 10 FPS；保留活动文件及待发送视频 |
| 故障恢复 | 看门狗协助恢复进程或采集故障，CPU 也可执行推理 |

<a id="demo"></a>
## 演示

以下是 **36 秒无声合成演示**：使用真实预览渲染器、合成场景、预设检测框和模拟录像状态，并压缩 120 秒静默与等待过程，展示移动后切段。它不包含真实摄像头画面，也不代表 YOLO 检测准确率。

![多边形选择、活动录像与重新等待的合成演示](docs/media/superlive-demo.gif)

[播放或下载 MP4 演示](docs/media/superlive-demo.mp4)

| 多边形选择 | 活动录像 | 无移动后重新等待 |
| --- | --- | --- |
| ![选择多边形顶点](docs/media/polygon-selection.png) | ![区域内活动录像](docs/media/activity-recording.png) | ![等待再次移动](docs/media/inactivity-rearm.png) |

## 最近更新

- 检测区域支持多边形；仅接触边界或落在凹形缺口内不会触发。
- 正常片段至少 120 秒、最多 300 秒；满 120 秒后移动即切段，每段重新进入 Telegram 静默期。
- 移除自动开始照片，Telegram 发送已结束的视频、已确认车牌及每日 CSV；保留连续两分钟无移动结束的规则。
- 补充可直接查看的合成演示及多语言使用文档。

<a id="controls"></a>
## 多边形操作

| 操作 | 作用 |
| --- | --- |
| R／选择区域 | 开始绘制多边形 |
| 鼠标左键 | 按边界顺序添加顶点 |
| 鼠标右键／Backspace | 删除最后一个顶点 |
| Enter／保存 | 闭合并保存有效多边形 |
| Esc／取消 | 保留原来的区域 |
| C／全画面 | 清除区域限制 |
| Q／停止 | 停止监控并结束当前录像 |

至少选择三个不同顶点，边不可交叉。使用快捷键时需聚焦预览窗口。坐标相对于裁切后的摄像头画面保存，画面内的时间戳和频道标签会保留。

<a id="faq"></a>
## 常见问题

**怎样判断移动？** 根据人物或车辆检测框的位置和大小变化，忽略小幅抖动并累计缓慢位移。仅手脚移动但检测框不变时可能无法识别；遮挡、镜头移动及检测误差也会影响结果，这不是姿态或物理运动传感器。

**只录制多边形内部吗？** 不是。多边形决定哪些人物或车辆可触发事件，视频保存完整裁切后的摄像头画面。

**视频结束了，为什么还没收到？** 当前片段的前 120 秒会推迟所有新的 Telegram 发送，包括旧片段和重试。不断切段会延长等待；网络故障也会使视频继续留在队列。静默开始前已发出的请求仍可能送达。

**虚拟机能用 GPU 加速吗？** 支持 CPU 推理；VMware 图形加速不等于 CUDA。默认每 60 秒检查磁盘：整盘使用率达到 80% 时删除最旧的已关闭录像，直到降至 78%，活动及待发送文件受保护。详见存储文档。

<a id="docs"></a><a id="documentation"></a>
## 文档与安全

[英文指南](docs/USER_GUIDE.md) · [繁体中文指南](docs/USER_GUIDE.zh-TW.md) · [录像与存储](docs/RECORDING.md) · [架构](docs/ARCHITECTURE.md) · [硬件](docs/HARDWARE.md) · [VMware](docs/VMWARE.md)

运行代码位于 `src/`，不含凭证的模板位于 `config/`，部署工具位于 `installer/`。不要提交真实 Token、Chat ID、密码、本机配置、事件图片或录像。提交前运行 `python .\scripts\security_scan.py .`，并阅读 [安全说明](SECURITY.md)。

## 许可证

本项目使用 [MIT 许可证](LICENSE)。

## Daily CSV · 23:00 Asia/Taipei

每天 23:00（台湾时间）发送当天 00:00（含）至 23:00（不含）的触发记录 CSV，并与视频一起保存。每日 CSV 不受录像静默期限制，23:00 照常发送。23:00 后的事件不纳入，不补发午夜完整版。零触发也发送报表；断线或关机后恢复时补发。
