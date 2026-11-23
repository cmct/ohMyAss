ohMyAss Noto x64 渲染链
=======================

这个目录包含 x64 的 avs2bdnxml + AviSynth+ + VSFilterMod（GDI）。

当前只保留 SUPer 链：

  ASS
    -> avs2bdnxml + AviSynth+ + VSFilterMod（GDI）
    -> BDN XML + PNG
    -> SUPer
    -> SUP / PES + MUI

  使用 -a 1（自动裁剪）、-b 1（按图像拆分优化 PG buffer）。
  使用 -R active.txt：静态字幕只渲染开始/结束边界帧；带 \t、\move、
  \k、\fad、\fade、\rnd 的动画事件保留完整区间。
  兼容 SUPer 高级参数和 PES/MUI 输出。
  不再提供 avs2bdnxml 直接写 SUP 的普通链。

文件
----
avs2bdnxml.exe    x64，修改版，支持 BDN XML 输出（二进制仍保留 SUP/PGS writer）
AviSynth.dll      AviSynth+ 3.7.6 x64
VSFilterMod.dll   VSFilterMod x64（GDI 版，r5.3.1 Release）
source/           avs2bdnxml x64 修改版完整源码
                  source/avs2bdnxml-x64/README-OMA.md

SHA-256
-------
avs2bdnxml.exe   193264D0BBA6E79FC38EE175668D6B4E1EBF159B28EA67D3548EBA9C7527176C
AviSynth.dll     F319FC6893B3E658E3DEF976F52AD8CF6360DCCDD06FD14F121114E17B9F9CBE
VSFilterMod.dll  D11B4E3DF394A3FAF6940213A6083CFF13761B48908E182EC2BAA50AA1868BE7

来源
----
AviSynth+ 3.7.6:
https://github.com/AviSynth/AviSynthPlus/releases/tag/v3.7.6

VSFilterMod r5.3.1 x64（GDI）:
https://github.com/Masaiki/VSFilterMod/releases/tag/r5.3.1

avs2bdnxml 上游:
https://github.com/koroban/avs2bdnxml

avs2bdnxml x64 修改
--------------------
上游版本使用 VfW/AVIFile 读帧，依赖 32 位 AviSynth 和 vfw32。
这个构建改为：

- 使用 AviSynth+ C API，通过 AviSynth.dll 加载脚本；
- 读取 RGB32 帧并按 pitch 逐行复制；
- 去掉 VfW、DevIL 和 x86 SSE2 汇编依赖；
- 修复 x64 下 long 截断指针的对齐问题；
- 使用 w64devkit + 静态 libpng/libz 构建；
- 保留 avs2bdnxml 自带的 BDN XML / SUP(PGS) writer。

修改补丁：avs2bdnxml-x64.patch
构建脚本：build-avs2bdnxml-x64.sh

实测（2 小时 47 分、3322 个事件）：
- SUPer 链 BDN + SUPer：约 53 秒，118,593,722 字节，5098 ODS；
- AssRocket：119,698,311 字节，3572 ODS、19 个多对象 PCS。

《野兽之心》423 个事件、100 分钟、空白帧约 87%：
- SUPer 链：约 15 秒；
- 关键帧跳过前后 SUP 哈希完全一致。

@ 竖排字体
----------
使用 GDI 版 VSFilterMod，而不是 FreeType 版。
实测 @思源黑体 竖排样本与旧 32 位 VSFilterMod 输出尺寸一致，
像素最大差异为 2/255，没有 FreeType 版的竖排宽度大幅变化问题。

许可
----
avs2bdnxml: GPL-3.0-or-later，见 LICENSE-avs2bdnxml.txt
AviSynth+: GPL-2.0，见 LICENSE-AviSynthPlus.txt
VSFilterMod: GPL-2.0-or-later，见 LICENSE-VSFilterMod.txt

对外分发时需要同时遵守以上 GPL 许可证，并提供对应源码。
