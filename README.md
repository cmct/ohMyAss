# ohMyAss Noto

面向字幕组的 **Windows ASS 字幕批处理工具**，覆盖文本字幕规范化、HDR 映射、繁体台湾化、图形字幕 SUP 生成和流媒体字幕下载。

![ohMyAss Noto](ohMyAss%20Noto.png)

> Windows 10 / 11 x64 · 最新版本见 [Releases](https://github.com/cmct/ohMyAss/releases)

## 功能

| 模块 | 功能 |
|---|---|
| **文本字幕** | 批量处理 `.ass` / `.srt`：样式规范化、标点与断句、双语对齐、HDR 颜色映射、繁体台湾化、黑边检测、问题报告 |
| **问题修复** | 打开生成的问题数据，逐条修改文本和时间轴；保存时同步全部语言字幕并重新分析，支持撤回和删除 |
| **图形字幕** | ASS → BDN XML + PNG → SUPer → SUP / PES + MUI；支持批量队列、分辨率/帧率、高级 SUPer 参数 |
| **SUP 转 PES** | 已有 `.sup` 直接生成同名 `.pes` + `.pes.mui`，不重新渲染、不重新编码 |
| **流媒体字幕** | 按 IMDB、片名或 Apple TV / iTunes 链接，抓取 Apple TV Store 的全地区、全语言字幕 |
| **设置** | 工具检查与更新、字体安装、软件更新、许可与源码查看 |

### 文本字幕

- 输入 `.ass` / `.srt`，自动识别 UTF-8 / UTF-16 / GBK
- 输出简英双语、简体中文、繁体中文、英文
- HDR 模式按 BT.2020 + PQ 映射，亮度可调
- 繁体台湾化调用繁化姬，并生成对比报告
- 自动生成字幕问题报告，标记超长行、疑似单语行和格式异常

### 图形字幕

```text
ASS -> BDN XML + PNG -> SUPer -> SUP / PES + MUI
```

- 只保留 SUPer 链
- 支持 1080p / 1080i / 720p / 576i / 480i
- 支持 23.976 / 24 / 25 / 29.97 / 50 / 59.94
- 支持 `@` 竖排字体、静态关键帧跳过、active-ranges
- 支持同时生成 SUP、PES + MUI
- x64 的 avs2bdnxml、AviSynth+、VSFilterMod 已内置

### 流媒体字幕

- 支持 Apple TV Store / iTunes 购买或租赁字幕
- 全地区、全语言探测与下载
- 支持语言 / 地区 / 代理 / DSID / 并发 / 超时筛选
- 支持只列出、只查上架、仅 JustWatch 地区
- 支持 Forced / SDH / 精确去重
- 不支持 Apple TV+ 原创、Netflix、Disney+ 等平台，也不支持剧集

## 快速开始

### 下载发布版

从 [Releases](https://github.com/cmct/ohMyAss/releases) 下载最新的 `ohMyAss Noto.exe`，双击运行，无需安装 Python。

### 从源码运行

```powershell
pip install -r requirements.txt
python ohMyAss_Noto.py
```

图形字幕首次使用时会从官方 GitHub Release 下载 SUPer。  
设置里的「检查更新」更新 SUPer 和官方 ass2bdnxml，但不会替换内置的 `bin64\avs2bdnxml.exe` 修改版。

## 许可与版权

本项目自身代码以 [MIT License](LICENSE) 发布。

`bin/`、`bin64/`、`third_party/` 下的第三方组件**不在 MIT 许可范围内**，仍遵循各自的上游许可：

| 组件 | 许可 |
|---|---|
| avs2bdnxml x64 修改版 | GPL-3.0-or-later |
| AviSynth+ 3.7.6 | GPL-2.0 |
| VSFilterMod r5.3.1 | GPL-3.0 |
| zhconv 1.4.3 | GPL-2.0-or-later |
| SUPer v0.3.0（运行时下载） | GPL-3.0 |
| Pillow | HPND |
| tkinterdnd2 / isubrip / m3u8 等 | MIT / BSD 等 |

- avs2bdnxml 修改版的完整源码在 `bin64/source/avs2bdnxml-x64/`
- zhconv 完整源码在 `third_party/zhconv/`
- AviSynth+ / VSFilterMod 使用上游精确版本源码，书面源码要约见 `SOURCE.md`
- 完整组件清单、版本和许可证见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)
- 源码位置和 GPL 源码获取方式见 [`SOURCE.md`](SOURCE.md)

重新分发本项目时，请保留 `LICENSE`、`THIRD_PARTY_NOTICES.md`、`SOURCE.md`、对应第三方许可证和源码。

`CMCT` 名称、标识和相关素材归其权利人所有；MIT 许可不授予商标权。

## 反馈

问题与建议请提交 [Issues](https://github.com/cmct/ohMyAss/issues)。
