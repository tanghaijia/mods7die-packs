# mods7die 在线配方仓库

这是 [mods7die](https://github.com/tanghaijia/mods7die) 的大型模组**配方**（recipe）索引仓库。
应用从这里读取"某个大型模组该怎么装"，并在解压后据此落地文件。

**这个仓库不存放任何模组文件**，只有几百字节的配方数据与作者页面的链接。

> **English:** Recipe index for large 7 Days to Die overhauls (Undead Legacy, EFT Overhaul, …).
> Pure data — no binaries. The app fetches `index.json` and the `packs/**` files it references.

## 在应用里启用

配方来源 → 添加下面这个地址（**必须以 `index.json` 结尾**，相对路径是相对它解析的）：

```
https://raw.githubusercontent.com/tanghaijia/mods7die-packs/main/index.json
```

也可以 `git clone` 到本地后添加**本地目录源**，改一条配方就能立刻在应用里试。
## 仓库结构

```
index.json                                  # 索引，由 packs/** 生成，不要手改
packs/<pack-id>/pack.jsonc                  # 系列级配方：识别规则、安装映射、启动要求
packs/<pack-id>/releases/<版本>.jsonc       # 版本级：文件清单、游戏版本区间、可选差异覆盖
docs/配方格式.md                             # 字段速查与模板
tools/verify.py                             # 结构校验（只用标准库，CI 与本地都跑它）
.github/workflows/validate.yml              # CI
LICENSE                                     # MIT
```

三层的分工是刻意的：**安装逻辑住在"系列"上**，版本条目只描述"哪个版本、哪些文件、适配哪个
游戏版本"。所以同一系列的新补丁通常只需要新增一个几行的条目文件，安装逻辑一个字都不用动。
某个补丁真的改了布局时，把它写成自己条目里的 `overrides` 差异，而不是去改那份唯一的
`pack.jsonc`（一改，老版本用户就再也装不了了）。

## 怎么贡献

### 新增一个补丁（最常见）

1. 复制 `packs/<id>/releases/` 里最接近的一份，改名成新版本号，改 `version` / `game_version` /
   `artifacts`。
2. 跑一次校验（见下），提交 PR。

### 新增一个大型模组

照 `docs/配方格式.md` 的模板写 `pack.jsonc` + 一个 release 文件。前提是你能拿到这个模组的
安装包，看清楚解压后是什么结构。

### 不会用 git 也能贡献

应用里装了未登记的版本时，安装计划旁边有「**复制上报信息**」按钮：它把配方 id、配方条目版本、
包内声明的版本、识别结论、每个压缩包的文件名/大小/哈希组装成一段文本。把那段文字发到
[Issues](https://github.com/tanghaijia/mods7die-packs/issues) 即可，不需要懂仓库结构。

也可以在应用里把配方导出成 `.pack` 文件发给别人侧载使用。

## 本地校验

**两条命令，力度不同：**

```powershell
# 1) 结构校验：只用 Python 标准库，秒级，任何时候都能跑
python tools/verify.py .

# 2) 权威校验：用应用自身的加载代码，需要 mods7die 的检出
cargo run --release --manifest-path ..\mods7die\Cargo.toml -p mods7day --bin mods7pack -- verify .
cargo run --release --manifest-path ..\mods7die\Cargo.toml -p mods7day --bin mods7pack -- index . --check
```

`mods7pack verify` 查：索引引用的文件是否都在、配方是否合法、`overrides` 是否引用了真实存在的
组件、磁盘上有没有**没被索引登记**的配方文件（漏登记 = 应用永远看不到它）、索引是否最新。
`mods7pack index .` 由 `packs/**` 重新生成 `index.json`（改完配方后跑一次）。

`python tools/verify.py` 覆盖同样的结构规则，**权威判定仍然是 `mods7pack`**：前者通过只说明
"结构上没问题"。两份实现的分工与同步约定写在 `tools/verify.py` 顶部。

## CI

`.github/workflows/validate.yml` 有一个 job、三步：

1. **结构校验**（`python tools/verify.py .`）：永远会跑，秒级完成，fork 来的 PR 也能跑。
2. **权威校验**（`mods7pack verify` + `index --check`）：仅在配置了仓库 secret
   `MODS7DIE_TOKEN` 时运行。
3. 没配 token 时打一条 notice 说明只跑了结构校验。

为什么权威校验要绕这么一圈：**mods7die 目前是私有仓库，而本仓库必须公开**（用户的 app 要匿名
拉取 raw 地址）。公开仓库的 Actions 检不出私有仓库，fork 来的 PR 也拿不到 secret，所以：

- 想让它自动跑：建一个 fine-grained PAT（只需 `Contents: Read` 访问 mods7die），存成仓库
  secret `MODS7DIE_TOKEN`。
- 或者把 mods7die 改成公开仓库，然后去掉 workflow 里的 token 那一段。
- 或者维持现状：**合并前维护者本地跑一次 `mods7pack verify`**（这是权威判定），CI 负责拦住
  结构性错误。

workflow 里的 `APP_REF` 现在是 `community-mod`（`mods7pack` 命令所在的分支），合并进默认分支后
改成默认分支名。

## 待核实（欢迎第一个 PR）

- **亡灵遗产的版本号**：条目写的是 `2.7.24`（来自社区文档，未核对），而实测同一份安装里
  ModInfo 写 `2.7.01`、运行日志写 `2.7.17`。需要手上有官方下载包的人确认 tag 与文件名。
- **文件哈希**：`artifacts[].sha256` 全是 `null`。填上之后应用会在解压前硬校验，对不上直接拒绝
  ——这是唯一能拦住"文件被换过"的手段。代价是每次安装要多读一遍压缩包。
- **网盘镜像**：`mirrors` 为空。国内流传的整合版大多在网盘，把镜像填进来能省掉用户到处找。

## 版权与许可

- 本仓库**只包含配方数据**，不包含任何模组二进制。模组本体的版权属于其作者，下载与安装请
  遵守作者与发布平台的要求。
- 本仓库自身以 [MIT](LICENSE) 释出。
- 「配方」描述的是"文件放哪里"这类事实性映射；如果你作为模组作者不希望自己的模组被收录，
  开个 issue 即可移除。
