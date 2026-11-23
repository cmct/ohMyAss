# ohMyAss Noto x64 测试分支

这个目录是 OMA 的 x64 渲染链测试分支，不是正式发布目录。

## 当前架构

x64 渲染组件：

```text
bin64/
  avs2bdnxml.exe    x64，修改版，支持 BDN XML 输出
  AviSynth.dll      AviSynth+ 3.7.6 x64
  VSFilterMod.dll   VSFilterMod x64（GDI 版，r5.3.1）
```

不再使用 `vsfmrender.exe`。

x64 实际使用的 `bin64\avs2bdnxml.exe` 是随程序内置的修改版（2.09，带 active-ranges / 静态关键帧优化）。
设置里的「检查更新」只更新 SUPer 与官方 ass2bdnxml，不会替换这个内置文件。

## SUPer 链

图形字幕只保留这一条链：

```text
ASS
  -> x64 avs2bdnxml + AviSynth+ + VSFilterMod（GDI）
  -> BDN XML + PNG
  -> x64 SUPer
  -> SUP / PES + MUI
```

- 普通字幕直出 SUP 的链路已移除，不再保留代码和界面入口
- 使用 `-a 1` 自动裁剪
- 使用 `-b 1` 按图像拆分优化 PG buffer
- 使用 active-ranges：静态字幕只渲染开始/结束边界帧，跳过中间相同帧
- 带 `\t`、`\move`、`\k`、`\fad`、`\fade`、`\rnd` 的动画事件保留完整区间
- 保留 SUPer 高级参数
- 支持 PES/MUI
- 输出 `bdn.xml`、PNG、`<字幕名>.sup`，可选 PES/MUI

## 界面

图形字幕页面：

```text
渲染链：SUPer（BDN + SUP）
[ ] 同时生成 SUP
[ ] 同时生成 PES
[ ] 仅保留 SUP / PES
```

- 不再显示「特效字幕（SUPer）」开关
- 不再有普通链选项
- 「仅保留 SUP / PES」会在生成成功后删除中间的 `bdn.xml` 和 PNG

## @ 竖排字体

x64 使用 **GDI 版 VSFilterMod**，不是 FreeType 版。

实测 `@思源黑体 Medium` 竖排样本：

| 版本 | 输出尺寸 |
|---|---|
| 旧 32 位 VSFilterMod | 244×25 |
| x64 GDI VSFilterMod | 244×25 |
| FreeType 版（已弃用） | 446×56 |

x86 与 x64 GDI 的像素最大差异为 2/255，没有 FreeType 版的竖排宽度大幅变化问题。

因此 @ 竖排字体不需要再强制回退 x86；x64 GDI 链和旧版基本一致。

## SUP 转 PES

图形字幕页新增「SUP 转 PES」按钮：

- 选择已有 `.sup`，支持多选
- 直接封装为同名 `.pes` + `.pes.mui`
- 不重新渲染、不重新编码，保留原 SUP 的画面和调色板
- 输出到每个 SUP 所在目录
- 时间戳使用 SUPer 相同的 10 分钟 mux offset

## 流媒体字幕页面

新增页面「流媒体字幕」，集成 `atv_subs.py`：

- 输入 IMDB 编号、片名、Apple TV / iTunes 链接
- 全地区、全语言探测
- 只列出、只查上架、仅 JustWatch 地区
- 语言 / 地区 / 代理 / DSID / 并发 / 超时
- Forced / SDH / 精确去重开关
- 后台运行、实时日志、取消、打开输出目录
- 依赖：`isubrip >= 2.7.4`、`httpx2`

## 已验证

- 59 个单元测试通过
- `pyflakes` 无告警
- `vulture --min-confidence 70` 无告警
- 字体检测：GDI 枚举补全本地化族名，`宋体` / `SimSun`、`Cronos Pro Subhead` 不再误报缺失
- 小样本 SUPer 链实测：BDN + SUP 成功，PES/MUI 成功
- SUP 转 PES：与 SUPer 直接生成的 PES/MUI 逐字节一致
- 14 条含 `\blur2` / `\fsvp` 的样本：
  - SUPer 链 BDN + SUPer 成功
  - PES/MUI 成功
- 2 小时 47 分、3322 个事件：
  - SUPer 链 BDN + SUPer：约 53 秒，118,593,722 字节，5098 ODS
  - 对比 AssRocket：119,698,311 字节、3572 ODS、19 个多对象 PCS
- 《野兽之心》423 个事件、100 分钟、约 87% 空白帧：
  - SUPer 链约 15 秒
  - 关键帧跳过前后 SUP 哈希完全一致
- `ffprobe` 识别为 `hdmv_pgs_subtitle`，1920×1080

## 与旧 x86 链的差异

- x64 使用 AviSynth+ 3.7.6 和 GDI VSFilterMod r5.3.1。
- 只保留 SUPer 链，BDN/PNG 中间文件始终生成。
- 需要 PES/MUI 时使用同一套 SUPer 链。
- 普通链的 SUP 由 avs2bdnxml 自带 writer 生成，这部分已移除。

## 回退

如果 x64 BDN 渲染组件有问题：

1. 把 `bin64` 改名为 `bin64.off`。
2. 重启 OMA，程序会自动使用 `bin\avs2bdnxml.exe` 的 32 位链。
3. 旧的 `%LOCALAPPDATA%\ohMyAssNoto\tools\vsfilter\` 不受影响。

## 许可

- `avs2bdnxml`：GPL-3.0-or-later
- `AviSynth+`：GPL-2.0
- `VSFilterMod`：GPL-2.0-or-later

许可证、来源和 SHA-256 见 `bin64/README-X64.txt`。
对外分发时需要同时遵守以上许可证并提供对应源码。
