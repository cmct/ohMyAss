# 第三方组件与许可声明

本文件适用于 ohMyAss Noto v0.7.0 的源码、打包版 exe 和发布压缩包。

本项目自身代码以 MIT License 发布（见 `LICENSE`）。

下列第三方组件**不在 MIT 许可范围内**，仍遵循各自的上游许可。

---

## 1. 随程序内置的 GPL 组件

| 组件 | 版本 / 来源 | 许可 | 说明 |
|---|---|---|---|
| avs2bdnxml（x64 修改版） | 基于 [koroban/avs2bdnxml](https://github.com/koroban/avs2bdnxml)，OMA 修改 | GPL-3.0-or-later | 修改了 VfW 输入、x64 构建、active-ranges、静态关键帧优化等；完整修改版源码在 `bin64/source/avs2bdnxml-x64/` |
| AviSynth+ | 3.7.6 x64 | GPL-2.0 | 源码：https://github.com/AviSynth/AviSynthPlus/releases/tag/v3.7.6 |
| VSFilterMod | r5.3.1 x64（GDI） | GPL-3.0（以随附许可证为准） | 源码：https://github.com/Masaiki/VSFilterMod/releases/tag/r5.3.1 |
| zhconv | 1.4.3 | GPL-2.0-or-later | 随 exe 打包；完整源码在 `third_party/zhconv/` |

对应许可证全文：

- `bin64/LICENSE-avs2bdnxml.txt`
- `bin64/LICENSE-AviSynthPlus.txt`
- `bin64/LICENSE-VSFilterMod.txt`
- `third_party/zhconv/LICENSE`
- `bin64/source/avs2bdnxml-x64/COPYING`

### 源码获取

- **avs2bdnxml 修改版**：完整源码、补丁和构建脚本已随仓库提供：
  - `bin64/source/avs2bdnxml-x64/`
  - `bin64/avs2bdnxml-x64.patch`
  - `bin64/build-avs2bdnxml-x64.sh`
- **zhconv**：完整源码在 `third_party/zhconv/`。
- **AviSynth+ / VSFilterMod**：请使用上表中的精确上游版本获取源码；如需要物理介质副本，请按 `SOURCE.md` 中的书面要约联系维护者。

---

## 2. 运行时下载、不随发布包内置的组件

| 组件 | 版本 / 来源 | 许可 | 说明 |
|---|---|---|---|
| SUPer CLI | v0.3.0，[cubicibo/SUPer](https://github.com/cubicibo/SUPer) | GPL-3.0 | 首次使用图形字幕时由程序从官方 GitHub Release 下载，不随 exe 内置 |
| 7zr | [7-zip.org](https://www.7-zip.org/) | LGPL-2.1-or-later（7-Zip 许可） | 首次下载工具时获取 |
| 思源黑体 Medium | Source Han Sans SC Medium | SIL Open Font License 1.1 | 由程序下载并安装到当前用户字体目录 |
| 繁化姬 | [zhconvert.org](https://zhconvert.org) | 在线服务条款 | 繁体台湾化时联网调用，不随程序分发 |

---

## 3. 随 exe 打包的 Python 依赖

| 组件 | 版本 | 许可 |
|---|---|---|
| Pillow | 10.4.0 | HPND |
| tkinterdnd2 | 0.7.0 | MIT |
| isubrip | 2.7.4 | MIT |
| httpx2 | 2.16.2 | BSD-3-Clause |
| httpcore2 | 2.16.2 | BSD-3-Clause |
| h11 | 0.16.0 | MIT |
| anyio | 4.16.0 | MIT |
| certifi | 2023.5.7 | MPL-2.0 |
| idna | 3.20 | BSD-3-Clause |
| sniffio | 1.3.1 | MIT OR Apache-2.0 |
| pydantic | 2.14.4 | MIT |
| pydantic-core | 2.50.5 | MIT |
| annotated-types | 0.7.0 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| m3u8 | 6.0.0 | MIT |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| cffi | 2.1.1 | MIT-0 |
| pycparser | 3.12 | BSD-3-Clause |
| brotli | 1.2.1 | MIT |
| charset-normalizer | 3.1.0 | MIT |
| zlib | 1.3.1 | Zlib License |
| libpng | 1.6.44 | libpng License |

这些组件的许可证全文在 `third_party/LICENSES/`，zlib/libpng 的许可证在
`bin64/source/avs2bdnxml-x64/THIRD_PARTY_LICENSES/`。

---

## 4. 名称与商标

- `ohMyAss Noto` 的项目代码按 MIT 许可发布。
- `CMCT` 名称、标识和相关素材归其权利人所有；MIT 许可不授予商标权。
- `avs2bdnxml`、`AviSynth+`、`VSFilterMod`、`SUPer`、`Pillow` 等名称归各自项目/作者所有。
- 本项目对 avs2bdnxml 的修改版会明确标注为修改版，不代表上游作者背书。

---

## 5. 分发注意

如果你重新分发本项目的 exe 或压缩包：

1. 保留 `LICENSE`、本文件、`SOURCE.md` 和所有第三方许可证。
2. 保留 `bin64/source/` 和 `third_party/zhconv/` 中的对应源码。
3. 不要删除或修改第三方版权声明。
4. 如果你修改了 GPL 组件，必须继续以 GPL 提供修改后的完整源码。
5. 如果你把运行时下载的 SUPer 等组件重新打包进自己的发行版，需要自行履行对应 GPL 义务。

本文件不是法律意见；公开或商业分发前建议进行正式合规审查。
