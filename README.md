# ohMyAss Noto

面向字幕组的 **ASS 字幕批处理工具**（Windows 桌面应用），覆盖「文本字幕规范化 → HDR 映射 → 繁体台湾化 → 图形字幕 SUP 生成」的完整流程。

![ohMyAss Noto](ohMyAss%20Noto.png)

> 当前版本：**v0.7.0** · 平台：Windows 10 / 11（x64）

---

## 功能模块

| 模块 | 说明 |
|---|---|
| **文本字幕** | 读取 `.ass` / `.srt`，按 CMCT 样式规范重排，处理标点、断句、行内标签、双语对齐，可选 HDR 颜色映射与繁体台湾化，输出多语种字幕并生成问题报告 |
| **问题修复** | 打开「文本字幕」生成的问题数据（JSON），逐条修改时间轴和文本；保存时自动同步全部语言字幕并重新分析，支持撤回和右键删除字幕条 |
| **图形字幕** | 由 ASS 生成 BDN XML + PNG，再编码为 SUP / PES + MUI；支持已有 SUP 直接转 PES + MUI |
| **流媒体字幕** | 按 IMDB / 片名 / Apple TV 链接抓取 Apple TV Store / iTunes 的全地区、全语言字幕 |
| **设置** | 外部工具版本检查与在线更新、思源黑体安装、应用更新、许可与源码查看 |

### 文本字幕

- **输入格式**：`.ass`、`.srt`，自动识别 UTF-8 / UTF-16 / GBK 编码
- **多语种输出**：简英双语、简体中文、繁体中文、英文
- **HDR 处理**：按 ssaHdrify 的映射算法把 SDR 样式颜色转换为 BT.2020 + PQ，亮度默认 100 nit，可调 1–1000
- **繁体台湾化**：调用[繁化姬](https://zhconvert.org)在线接口，并生成「普通繁体 → 台湾化」逐条对比报告
- **黑边检测**：载入电影截图自动识别上下 / 中黑边比例
- **问题报告**：生成 HTML 报告，标出超长行、疑似单语行、格式异常等需要人工确认的条目

### 图形字幕

- 当前只保留 **SUPer 链**：

```text
ASS
  -> x64 avs2bdnxml + AviSynth+ + VSFilterMod（GDI）
  -> BDN XML + PNG
  -> x64 SUPer
  -> SUP / PES + MUI
```

- 支持 **1080p / 1080i / 720p / 576i / 480i**，帧率支持 **23.976 / 24 / 25 / 29.97 / 50 / 59.94**
- 静态字幕只渲染开始/结束边界帧，支持 active-ranges 和关键帧跳过
- 支持 `@` 竖排字体
- 可选量化器：libimagequant（质量最好）、Qtzr（质量高、体积大）、HexTree（均衡）、Pillow（最快）
- 支持同时生成 SUP、PES + MUI
- **SUP 转 PES**：把已有 `.sup` 直接封装为同名 `.pes` + `.pes.mui`，不重新渲染、不重新编码

### 流媒体字幕

- 输入 IMDB 编号、片名或 Apple TV / iTunes 链接
- 全地区、全语言探测与下载
- 支持语言 / 地区 / 代理 / DSID / 并发 / 超时
- 支持只列出、只查上架、仅 JustWatch 地区
- 支持 Forced / SDH / 精确去重
- 后台运行、实时日志、取消、打开输出目录

---

## v0.7.0 更新

- **图形字幕只保留 SUPer 链**：移除普通字幕直出 SUP 的代码路径和界面入口
- **新增 SUP 转 PES**：已有 SUP 可直接生成 PES + MUI，输出与 SUPer 的 PES/MUI 逐字节一致
- **最近文件记录**：文本字幕、图形字幕、黑边检测、问题修复、流媒体字幕的文件选择器会记住上次目录
- **字体检测修复**：支持 `宋体` / `SimSun`、`Cronos Pro Subhead` 等本地化族名，不再误报缺失
- **流媒体字幕页面**：由「Apple TV 字幕」改名为「流媒体字幕」，图标改为小电视
- **版权与许可**：新增 `THIRD_PARTY_NOTICES.md`、`SOURCE.md`、`third_party/` 和 `bin64/source/`，补齐 GPL 源码与许可证说明
- **工具更新说明**：x64 实际使用的 `bin64\avs2bdnxml.exe` 是内置修改版，「检查更新」不会替换它

---

## 环境要求

| 使用方式 | 依赖 |
|---|---|
| 运行打包版 `.exe` | 无需安装 Python |
| 源码运行 | Python 3.11+（含 tkinter）、`Pillow`、`zhconv`、`isubrip`、`httpx2`，可选 `tkinterdnd2` |

`tkinterdnd2` 仅用于拖拽文件，缺失时自动降级，不影响其他功能。

---

## 快速开始

### 方式一：下载发布版

从 [Releases](https://github.com/cmct/ohMyAss/releases) 下载最新的发布版，双击运行。

### 方式二：从源码运行

```powershell
pip install pillow zhconv tkinterdnd2 "isubrip>=2.7.4" httpx2

# 入口文件名带空格，必须加引号
python "ohMyAss Noto.py"
```

---

## 使用流程

### 1. 文本字幕

1. 选择字幕文件（可拖入）
2. 按需勾选 **HDR 高动态**（并填亮度）、**中黑边视频**、**繁体台湾化（繁化姬）**
3. 填写字幕信息：

   | 字段 | 必填 | 说明 |
   |---|---|---|
   | 片名 | 是 | 同时用于生成输出文件夹名，不能包含非法字符 |
   | 字幕组 | 是 | 写入 `Script Updated By` |
   | 原作者 / 翻译 / 校对 / 时间轴 | 否 | 写入 ASS Script Info 的对应字段 |

4. 点击 **开始生成**，完成后可直接 **打开输出文件夹**

### 2. 问题修复（可选）

处理完成后如报告里有需要人工确认的条目，进入 **问题修复**，选择生成的 JSON 数据文件，逐条修改后重新生成。

### 3. 图形字幕

选择 ASS 与输出文件夹 → 设置分辨率、帧率、语言代码 → 按需勾选 SUP / PES → 生成。

- x64 的 `avs2bdnxml.exe`、`AviSynth.dll`、`VSFilterMod.dll` 已内置。
- 首次使用 SUPer 时会联网从官方 GitHub Release 下载。
- 设置里的「检查更新」更新 SUPer 和官方 ass2bdnxml，但**不会替换** `bin64\avs2bdnxml.exe` 内置修改版。

### 4. SUP 转 PES

图形字幕页点击 **SUP 转 PES**，选择一个或多个 `.sup`：

```text
<字幕名>.sup
  -> <字幕名>.pes
  -> <字幕名>.pes.mui
```

输出到 SUP 所在目录，不重新渲染、不重新编码。

### 5. 流媒体字幕

输入 IMDB 编号、片名或 Apple TV / iTunes 链接，选择输出目录和筛选条件后开始下载。

---

## 输出说明

文本字幕输出目录默认为「源字幕所在目录 / 片名」，也可手动指定。

双语片源典型输出：

```text
1-简英双语-zh.ass        简英双语
2-简体中文-zh.ass        简体中文
3-繁体中文-zh.ass        繁体中文（可选台湾化）
4-英文-en.ass            英文
```

HDR 模式额外产出 SDR 对照版本（如 `1-简英双语-SDR-zh.ass`），并生成：

- `字幕问题报告.html` — 需要人工确认的条目
- `繁体台湾化对比.html` — 开启台湾化时生成

图形字幕输出：

```text
bdn.xml
PNG 图片
<字幕名>.sup
<字幕名>.pes / <字幕名>.pes.mui（可选）
```

---

## 目录结构

```text
ohMyAss_Noto.py          入口：单实例检查、主窗口启动
app_info.py              版本号与资源路径
config_store.py          配置、错误日志、路径工具
download_utils.py        下载与发布页解析
theme_ui.py              主题令牌、DPI、字体、图标、通用控件
reports.py               HTML 报告与问题数据解析
subtitle_core.py         ASS/SRT 解析、HDR、术语、文本规则、生成流程
subtitle_outputs.py      输出同步、删除、备份
fonts.py                 字体注册与思源黑体安装
bdn_sup.py               BDN/SUP/PES 生成、SUP 转 PES、VSFilterMod 渲染
atv_subs.py              流媒体字幕下载核心
app_update.py            软件自身在线更新
app_ui.py                主窗口与面板组装
ui_widgets.py            拖放与占位输入框
ui_blackbar.py           黑边检测窗口
ui_style.py              样式编辑器与预览
ui_fixer.py              术语表与问题修复面板
ui_bdn.py                图形字幕面板与任务队列
ui_atv.py                流媒体字幕面板
tests/                   单元测试
bin/                     32 位回退运行时
bin64/                   x64 运行时 + avs2bdnxml 修改版源码
third_party/             zhconv 源码与 Python 依赖许可证
```

---

## 源码结构与测试

源码按职责拆分，依赖方向为「基础工具 → 字幕核心 → 渲染/更新 → UI」。

运行单元测试：

```powershell
python -m unittest discover -s tests -v
```

### 打包（Windows）

```powershell
python -m PyInstaller --noconfirm --clean --distpath dist --workpath build "ohMyAss Noto.spec"
```

`ohMyAss Noto.spec` 已包含 `bin`、`bin64`、图标、许可与源码说明。

---

## 本地数据位置

| 内容 | 路径 |
|---|---|
| 界面配置、上次填写的片名等 | `%USERPROFILE%\ohMyAssNoto.json` |
| 错误日志（异常兜底记录） | `%USERPROFILE%\ohMyAssNoto_error.log` |
| 自动下载的外部工具 | `%LOCALAPPDATA%\ohMyAssNoto\tools` |
| 自动备份 | `%LOCALAPPDATA%\ohMyAssNoto\backups` |

---

## 外部依赖与许可声明

本仓库 `bin/`、`bin64/` 和 `third_party/` 下包含第三方组件，**版权归各自作者所有**，遵循其原始许可，**不在本项目 MIT 许可范围内**。

完整的组件清单、版本、许可证和源码位置见：

- [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)
- [`SOURCE.md`](SOURCE.md)

重点组件：

| 组件 | 许可 |
|---|---|
| avs2bdnxml x64 修改版 | GPL-3.0-or-later |
| AviSynth+ 3.7.6 | GPL-2.0 |
| VSFilterMod r5.3.1 | GPL-3.0 |
| zhconv 1.4.3 | GPL-2.0-or-later |
| SUPer v0.3.0（运行时下载） | GPL-3.0 |
| Pillow | HPND |
| tkinterdnd2 | MIT |
| isubrip | MIT |
| httpx2 / httpcore2 | BSD-3-Clause |

- avs2bdnxml 修改版的完整源码在 `bin64/source/avs2bdnxml-x64/`。
- zhconv 的完整源码在 `third_party/zhconv/`。
- AviSynth+ / VSFilterMod 使用上游精确版本源码，书面源码要约见 `SOURCE.md`。
- 本项目对 avs2bdnxml 的修改版会明确标注为修改版，不代表上游作者背书。

## 许可

本项目自身代码以 [MIT License](LICENSE) 发布，可自由使用、修改、分发，保留版权声明即可。

⚠️ `bin/`、`bin64/`、`third_party/` 下的第三方组件不在 MIT 许可范围内。若你二次分发，请保留 `THIRD_PARTY_NOTICES.md`、`SOURCE.md` 和对应源码/许可证，并自行确认合规。

---

## 已知限制

- 仅支持 Windows（依赖 Win32 注册表、`os.startfile`、`ctypes.windll`）
- 繁体台湾化需要联网访问繁化姬接口
- 图形字幕首次使用需要联网下载 SUPer
- BDN 渲染按时间轴切片，长片耗时较长，建议预留磁盘空间
- 流媒体字幕只支持 Apple TV Store / iTunes 购买或租赁的字幕，不支持 Apple TV+ 原创、Netflix、Disney+ 等平台

---

## 更新日志

### v0.7.0

- 图形字幕只保留 SUPer 链，移除普通字幕直出 SUP 的代码路径
- 新增 SUP 转 PES + MUI，支持多选、直接封装、不重新编码
- 新增最近文件目录记录
- 修复宋体 / SimSun、Cronos Pro Subhead 等字体误报缺失
- Apple TV 页面改名「流媒体字幕」，图标改为小电视
- 补齐第三方许可、GPL 源码和源码要约
- 图形字幕页新增 SUP 转 PES 按钮
- 修正预期输出文件判断和 BDN/SUP 校验

### v0.6.1

- 问题修复页支持直接编辑生成后的主字幕，保存时同步全部语言并重新分析
- 新增撤回、右键删除字幕条、自动备份
- 图形字幕队列支持右键移除、生成中锁定输出目录
- 修复主页设置卡片间距
- 修复 `Dialogue` 匹配误伤样式名

### v0.6.0

- 新增术语表、样式编辑与预览、图形字幕队列、问题报告与问题修复页
- 新增 HDR 映射、繁体台湾化、黑边检测
- 新增软件自身在线更新
- 文件名统一为 `ohMyAss Noto`，不再带版本号

---

## 反馈

问题与建议请提交 [Issues](https://github.com/cmct/ohMyAss/issues)。
