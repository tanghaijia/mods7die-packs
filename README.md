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

需要一个 mods7die 的检出：

```powershell
cargo run --release --manifest-path ..\mods7die\Cargo.toml -p mods7day --bin mods7pack -- verify .
cargo run --release --manifest-path ..\mods7die\Cargo.toml -p mods7day --bin mods7pack -- index . --check
```

- `verify`：索引引用的文件是否都在、配方是否合法、`overrides` 是否引用了真实存在的组件、
  磁盘上有没有**没被索引登记**的配方文件（漏登记 = 应用永远看不到它）、索引是否最新。
- `index .`：由 `packs/**` 重新生成 `index.json`（改完配方后跑一次）。
- `index . --check`：CI 用，确认 `index.json` 没被落下。

这两个命令和应用加载配方用的是**同一份代码**，所以"本地通过"就等于"应用能加载"。

## CI

`.github/workflows/validate.yml` 会检出本仓库与 mods7die，跑上面两条命令。

> 注意：`mods7pack` 命令目前在 mods7die 的 `community-mod` 分支上，workflow 里的 `ref` 也指向
> 它。合并进默认分支后，把 workflow 里的 `ref:` 那一行删掉即可。

## 待核实（欢迎第一个 PR）

- **亡灵遗产的版本号**：条目写的是 `2.7.24`（来自社区文档，未核对），而实测同一份安装里
  ModInfo 写 `2.7.01`、运行日志写 `2.7.17`。需要手上有官方下载包的人确认 tag 与文件名。
- **文件哈希**：`artifacts[].sha256` 全是 `null`。填上之后应用会在解压前硬校验，对不上直接拒绝
  ——这是唯一能拦住"文件被换过"的手段。代价是每次安装要多读一遍压缩包。
- **网盘镜像**：`mirrors` 为空。国内流传的整合版大多在网盘，把镜像填进来能省掉用户到处找。

## 版权与许可

- 本仓库**只包含配方数据**，不包含任何模组二进制。模组本体的版权属于其作者，下载与安装请
  遵守作者与发布平台的要求。
- 本仓库自身的许可**尚未指定**（纯数据建议 CC0-1.0 或 MIT，由维护者决定后补上 `LICENSE`）。
