# 第三方组件源码说明

本文件说明 ohMyAss Noto v0.7.0 中第三方组件的对应源码位置，以及 GPL 组件的源码获取方式。

## 1. 随仓库提供的完整源码

### avs2bdnxml x64 修改版

- 上游：https://github.com/koroban/avs2bdnxml
- 上游版本：2.09
- 修改版完整源码：`bin64/source/avs2bdnxml-x64/`
- 修改补丁：`bin64/avs2bdnxml-x64.patch`
- 构建脚本：`bin64/build-avs2bdnxml-x64.sh`
- 许可证：`bin64/source/avs2bdnxml-x64/COPYING`（GPL-3.0）

修改内容主要包括：

- 从 VfW/AVIFile 改为 AviSynth+ C API；
- 支持 x64 构建；
- 增加 active-ranges；
- 增加静态字幕关键帧优化；
- 修复 x64 指针截断等问题。

### zhconv

- 上游：https://github.com/gumblex/zhconv
- 版本：1.4.3
- 完整源码：`third_party/zhconv/`
- 许可证：`third_party/zhconv/LICENSE`（GPL-2.0-or-later）

## 2. 上游源码位置

以下二进制随程序内置，但源码不直接放在本仓库中；请按精确版本从上游获取：

| 组件 | 版本 | 源码 |
|---|---|---|
| AviSynth+ | 3.7.6 | https://github.com/AviSynth/AviSynthPlus/releases/tag/v3.7.6 |
| VSFilterMod | r5.3.1 | https://github.com/Masaiki/VSFilterMod/releases/tag/r5.3.1 |
| zlib | 1.3.1 | https://zlib.net/ |
| libpng | 1.6.44 | https://github.com/pnggroup/libpng/releases/tag/v1.6.44 |

运行时下载、不随发布包内置：

| 组件 | 版本 | 源码 |
|---|---|---|
| SUPer | v0.3.0 | https://github.com/cubicibo/SUPer/tree/v0.3.0 |
| 7zr | 7-Zip | https://www.7-zip.org/ |
| 思源黑体 Medium | Source Han Sans | https://github.com/adobe-fonts/source-han-sans |

## 3. GPL 源码书面要约

对于随本发布版分发的 GPL 组件（avs2bdnxml 修改版、AviSynth+、VSFilterMod、zhconv），
自本版本发布之日起三年内，维护者愿意向任何第三方提供对应的完整源码：

- 获取方式：通过 https://github.com/cmct/ohMyAss/issues 提交申请；
- 提供形式：GitHub 仓库访问、源码压缩包，或按需提供物理介质；
- 费用：不超过实际执行源码分发所需的成本；
- 内容：对应二进制的完整源码、修改记录、构建脚本和许可证文本。

本仓库已经直接提供 avs2bdnxml 修改版和 zhconv 的完整源码；AviSynth+ 和
VSFilterMod 使用上游精确版本的源码，未做本地修改。

## 4. 重新构建 avs2bdnxml x64

构建脚本需要：

- w64devkit（gcc、ar）
- AviSynth+ 3.7.6 x64 头文件
- zlib 1.3.1 源码
- libpng 1.6.44 源码

示例：

```sh
./bin64/build-avs2bdnxml-x64.sh \
  ./bin64/source/avs2bdnxml-x64 \
  /path/to/avisynth-include \
  /path/to/zlib-1.3.1 \
  /path/to/libpng-1.6.44
```

构建产物为 `avs2bdnxml.exe`，当前发布版 SHA-256：

```text
193264D0BBA6E79FC38EE175668D6B4E1EBF159B28EA67D3548EBA9C7527176C
```

## 5. 说明

本文件不是法律意见。若你要公开或商业分发本项目的修改版，请保留本文件、
`LICENSE`、`THIRD_PARTY_NOTICES.md` 及所有第三方许可证，并自行确认合规。
