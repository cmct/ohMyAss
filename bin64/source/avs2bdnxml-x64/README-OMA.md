# avs2bdnxml x64 修改版源码

本目录是 ohMyAss Noto 使用的 avs2bdnxml x64 修改版完整源码。

- 上游：https://github.com/koroban/avs2bdnxml
- 上游版本：2.09
- 修改补丁：`../../avs2bdnxml-x64.patch`
- 构建脚本：`../../build-avs2bdnxml-x64.sh`
- 许可证：`COPYING`（GPL-3.0）

## 修改内容

- 从 VfW/AVIFile 改为 AviSynth+ C API
- 支持 x64 构建
- 增加 active-ranges
- 增加静态字幕关键帧优化
- 修复 x64 指针截断等问题

## 构建

需要 w64devkit、AviSynth+ 3.7.6 x64 头文件、zlib 1.3.1、libpng 1.6.44。

```sh
../../build-avs2bdnxml-x64.sh . /path/to/avisynth-include \
  /path/to/zlib-1.3.1 /path/to/libpng-1.6.44
```

zlib / libpng 的许可证见 `THIRD_PARTY_LICENSES/`。
