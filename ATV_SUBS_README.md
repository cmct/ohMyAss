# Apple TV 全地区 / 全语言字幕下载器

> 已集成到 ohMyAss Noto 的「流媒体字幕」页面（数据来源：Apple TV Store / iTunes）。

输入 **IMDB 编号**（或影片名 / Apple TV 链接），自动去 Apple TV Store（iTunes 购买/租赁）的**全部上架地区**抓取字幕，合并所有语言、所有版本（普通 / Forced / SDH-CC），按内容去重后统一保存为 SRT。

这个脚本相当于把以前的流程自动化了：

```
IMDB 编号 → JustWatch 查 Apple TV 上架地区 → 逐个地区抓 HLS 字幕清单 → 下载 → 去重 → SRT
```

JustWatch 只通过 API 查询，不打开网页，所以 **justwatch-search 网页打不开也不影响使用**。

---

## 重要说明：能下载哪些流媒体？

- ✅ **Apple TV Store / iTunes**（购买、租赁）：可以下载。脚本会自动遍历 171 个 Apple TV 店面（美国、英国、加拿大、日本、台湾、香港、德国、法国、巴西……），合并所有地区独有的语言版本。
- ❌ **Netflix、Disney+、Max、Prime Video、Hulu、Paramount+ 等**：这些平台的字幕受 DRM 保护，没有公开的字幕文件接口，无法像 Apple 这样直接下载。
- ❌ **Apple TV+ 原创内容**（订阅流媒体，不是商店购买/租赁）：isubrip 目前不支持，脚本会提示。
- ❌ **剧集（TV Show）**：当前只支持电影。isubrip 的 Apple TV 剧集抓取尚未实现。

脚本仍会用 JustWatch 列出该片在 **所有流媒体平台** 的上架情况（国家数、租金/购买/订阅、JustWatch 能提供的字幕语言），并保存到 `subtitles_report.json`，方便你查片源。

> 所以“下载所有流媒体的字幕”实际上只能做到 **Apple TV Store 的全部地区、全部语言**。以前 justwatch-search + isubrip 的工作流也是这样的。

---

## 安装

需要 Python 3.10+（这台电脑已经是 3.11）。

```bat
python -m pip install -U "isubrip>=2.7.4"
```

或者直接双击 `安装或更新依赖.bat`。

> `isubrip` 负责解析 Apple 的 HLS 清单、把 WebVTT 转成 SRT；
> 本脚本负责 JustWatch 查询、多地区合并、去重和命名。

---

## 使用

### 最简单：双击批处理

- `下载字幕.bat`：输入 IMDB 编号或片名，自动下载到 `E:\Subtitles\iTunes`（如果该目录存在），否则下载到脚本目录下的 `downloads`。
- `列出字幕.bat`：只列出可下载的语言和地区，不下载。

### 命令行

```bat
:: 用 IMDB 编号（推荐，最准确）
python atv_subs.py tt15398776

:: 用片名（结果多时会让你选序号）
python atv_subs.py "Oppenheimer"

:: 只列出，不下载
python atv_subs.py tt15398776 --list

:: 指定输出目录
python atv_subs.py tt15398776 -o "E:\Subtitles\Oppenheimer"

:: 只要英文、中文、法文
python atv_subs.py tt15398776 --lang en,zh,fr

:: 只探测部分地区（更快）
python atv_subs.py tt15398776 --countries US,GB,CA,AU,JP,TW,HK

:: 只查流媒体上架情况，不下载
python atv_subs.py tt15398776 --services-only

:: 直接给 Apple TV 链接
python atv_subs.py "https://tv.apple.com/us/movie/inception/umc.cmc.6loas01ow0w4lkatxxloz7a6e"

:: 走代理
python atv_subs.py tt15398776 --proxy http://127.0.0.1:7890
```

### 参数

| 参数 | 说明 |
|---|---|
| `-o, --output` | 输出目录。默认：`当前目录\片名 (年份)\` |
| `--lang` | 只下载指定语言，逗号分隔。`zh` 会同时匹配普通话 `cmn` 和粤语 `yue` |
| `--countries` | 只探测指定 Apple TV 地区，例如 `US,GB,JP` |
| `--list` | 只列字幕轨，不下载 |
| `--services-only` | 只查 JustWatch 流媒体上架情况 |
| `--justwatch-countries` | 只探测 JustWatch 记录的地区（更快，可能漏掉冷门地区） |
| `--probe-jobs` | 探测地区并发数，默认 8 |
| `--download-jobs` | 同时下载的字幕条数，默认 3 |
| `--timeout` | HTTP 超时秒数，默认 30 |
| `--proxy` | HTTP(S) 代理 |
| `--dsid` | Apple 账号 DSID（仅下载已购买/已租借内容时需要） |
| `--exact-dedup` | 只对字节完全相同的文件去重（默认会忽略 `<i>` 标签拆分等排版差异） |
| `--no-forced` | 不下载 Forced 字幕 |
| `--no-sdh` | 不下载 SDH / CC 字幕 |
| `--debug` | 显示 isubrip 调试日志 |

---

## 输出命名

默认格式：

```
片名.年份.iT.WEB.语言.类型.srt
```

例如：

```
Oppenheimer.2023.iT.WEB.en.sdh.srt
Oppenheimer.2023.iT.WEB.pt-BR.srt
Oppenheimer.2023.iT.WEB.pt-PT.srt
Oppenheimer.2023.iT.WEB.es-419.srt
Oppenheimer.2023.iT.WEB.es-ES.srt
Oppenheimer.2023.iT.WEB.fr-CA.srt
Oppenheimer.2023.iT.WEB.fr-FR.srt
Oppenheimer.2023.iT.WEB.fr-FR.forced.srt
```

- `sdh`：SDH / CC（含环境音、说话人标记），Apple 的 `public.accessibility.*` 轨。
- `forced`：Forced（只翻译外语对白/屏显）。
- 同一种语言在多个地区有**不同内容**时，会自动加地区后缀，例如 `en-US.srt`、`en-GB.srt`、`es-419.US.srt`。
- 同一种语言在不同地区内容**几乎一样**（只有 `<i>` 标签换行等差异）时，默认合并成一个文件，避免重复。
- 如需保留这种排版差异，加 `--exact-dedup`。

每次下载还会生成 `subtitles_report.json`，包含：

- 每条字幕的来源地区、语言名称、是否 SDH/Forced；
- 文件的 SHA-256；
- JustWatch 上所有流媒体平台的上架国家、租金/购买/订阅方式、JustWatch 提供的字幕语言。

---

## 去重逻辑

1. **清单层**：同一个 `STABLE-RENDITION-ID`（Apple 的字幕轨 ID）只下载一次，不管它出现在多少个地区、多少个 CDN 上。
2. **内容层**：下载后按“时间轴 + 纯文本”生成指纹：
   - 忽略 SRT 序号；
   - 忽略 CRLF/LF 差异；
   - 忽略 `<i>`、`<font>` 等标签；
   - 合并空白。
3. 指纹相同的字幕合并为一条，并合并它们的上架地区列表。
4. 指纹不同但语言相同的字幕会保留，并用地区后缀区分。

---

## 常见问题

### 1. 为什么英文字幕只有一条 / 为什么没有英文？

Apple 对不同语言、不同地区的发行版本本来就不一样。比如《盗梦空间》在美国/英国商店就没有英文字幕轨（原声是英语），这不是脚本漏抓。换其他片子（如《奥本海默》）就能看到 `en.sdh`。

### 2. 为什么只有 8 条字幕？isubrip 以前也这样

旧版 isubrip 只抓一个地区。这个脚本默认会探测全部 171 个 Apple TV 店面，能合并出其他地区独有的语言（例如日本区独有日文 Forced、德国区独有德文 Forced、巴西区独有 pt-BR Forced）。

如果只想快速跑，加 `--justwatch-countries` 或 `--countries US,GB,...`。

### 3. 需要 Apple 账号吗？

下载商店里公开的字幕通常不需要。少数已购买/已租借内容可能需要 DSID：

```bat
python atv_subs.py tt15398776 --dsid 1234567890
```

DSID 不是密码，但属于账号信息，不要公开分享。

### 4. 出现 403 / 401 / “manifest unavailable”

- 先确认该片在对应地区确实是 Apple TV Store（购买/租赁）上架，而不是只有 Apple TV+；
- 试试 `--countries US` 或换代理；
- 如果是已购买内容，配置 `--dsid`；
- Apple 接口偶尔会变，先升级 isubrip：`python -m pip install -U "isubrip>=2.7.4"`。

### 5. 支持剧集吗？

暂不支持。脚本会明确提示 `MOVIE` 以外的条目。iSubRip 目前也没有实现 Apple TV 剧集的抓取。

### 6. 会不会下载到错误的电影？

推荐直接输入 **IMDB 编号**。用片名搜索时如果结果有多个，脚本会让你选序号。非交互环境（例如计划任务）会自动选第一个，建议不要用片名。

---

## 文件说明

| 文件 | 作用 |
|---|---|
| `atv_subs.py` | 主程序 |
| `下载字幕.bat` | 交互式下载 |
| `列出字幕.bat` | 交互式列出 |
| `安装或更新依赖.bat` | 安装/升级 isubrip |
| `requirements.txt` | Python 依赖 |

---

## 免责声明

本工具仅用于个人备份、学习和字幕研究。请遵守 Apple、JustWatch 及各流媒体平台的服务条款，不要传播或商用下载的字幕。字幕版权归原权利人所有。
