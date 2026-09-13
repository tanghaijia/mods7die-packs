#!/usr/bin/env python3
"""配方仓库自检（只用标准库，CI 与本地都能跑）。

用法：

    python tools/verify.py .          # 校验当前仓库
    python tools/verify.py . --quiet  # 只输出结论

## 为什么不是直接调用应用的 mods7pack

`mods7pack verify`（在 mods7die 仓库里）用的是**应用自身的加载代码**，是最权威的检查。
但 mods7die 目前是私有仓库，而本仓库必须公开（用户的 app 要匿名拉取 raw 地址），
公开仓库的 GitHub Actions 检不出私有仓库，fork 来的 PR 也拿不到 secret。
所以这里用标准库复刻同一套**结构规则**，让 CI 随时能跑。

代价是规则有两份实现。约定：

- **权威判定永远是 `mods7pack verify`**（维护者合并前跑，或配置 MODS7DIE_TOKEN 后由
  `.github/workflows/validate.yml` 的第二个 job 跑），本脚本通过 = "结构上没问题"，
  不等于"应用一定加载得动"。
- 每条规则后面都标了它在 Rust 里的出处，改一边时对着改另一边。

## 检查内容

1. JSONC 能否解析（注释与尾随逗号）。
2. 每个对象**没有未知字段**（Rust 侧是 `deny_unknown_fields`，写错字段名会让整个源加载失败）。
3. `pack.jsonc` / `releases/*.jsonc` 的字段与取值（对应 `schema.rs::validate`）。
4. `overrides` 引用的组件 / 删除项 / 校验项真实存在，且覆盖后的配方仍然合法
   （对应 `schema.rs::ReleaseManifest::effective_manifest`——这是应用**加载时故意不校验**的部分，
   所以必须在仓库侧拦住）。
5. 索引与配方一致，磁盘上没有漏登记的文件（对应 `catalog.rs::load_catalog_from_index`）。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SUPPORTED_SCHEMA_MAJOR = 1

# schema.rs::PackTarget
TARGETS = {"game_root", "mods", "data"}
# schema.rs::InstallMode
MODES = {"tree", "file", "overlay", "nested_into", "delete"}
# schema.rs::ExtractTarget
EXTRACT_TARGETS = {"root"}

PACK_KEYS = {
    "schema_version", "id", "name", "homepage", "summary", "maintainers", "provides",
    "conflicts", "detect", "install", "expect", "dependencies", "legacy_conflicts",
    "deletions", "launch", "game_version", "version_policy", "version_sources",
    "version_authority", "optional_components",
}
DETECT_RULE_KEYS = {"root", "glob", "modinfo"}
MODINFO_RULE_KEYS = {"name", "display_name", "author"}
COMPONENT_KEYS = {"id", "target", "mode", "from", "into", "when"}
EXPECT_KEYS = {"root", "path"}
OPTIONAL_KEYS = {"id", "name", "detect", "install"}
LAUNCH_KEYS = {"eac"}
GAME_VERSION_KEYS = {"min", "max"}
VERSION_POLICY_KEYS = {"allow_unlisted_patch"}
RELEASE_KEYS = {
    "schema_version", "pack", "version", "game_version", "artifacts", "mirrors", "overrides",
}
ARTIFACT_KEYS = {"id", "url", "sha256", "size", "extract", "strip_components"}
MIRROR_KEYS = {"label", "url", "password", "sha256", "note"}
OVERRIDE_KEYS = {
    "component_patches", "install_add", "install_remove", "deletions_add", "deletions_remove",
    "expect_add", "expect_remove", "optional_components_add", "optional_components_remove",
}
COMPONENT_PATCH_KEYS = {"id", "target", "mode", "from", "into"}
INDEX_KEYS = {"schema_version", "generated_at", "packs"}
INDEX_PACK_KEYS = {"id", "name", "summary", "homepage", "path", "releases"}
INDEX_RELEASE_KEYS = {"version", "game_version", "path"}

SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class Failures:
    def __init__(self) -> None:
        self.items: list[str] = []

    def add(self, where: str, message: str) -> None:
        self.items.append(f"{where}: {message}")


def strip_jsonc(text: str) -> str:
    """去掉 `//` `/* */` 注释与尾随逗号（对应 jsonc.rs）。"""
    out: list[str] = []
    i = 0
    in_string = False
    while i < len(text):
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < len(text):
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < len(text):
            nxt = text[i + 1]
            if nxt == "/":
                while i < len(text) and text[i] != "\n":
                    i += 1
                continue
            if nxt == "*":
                end = text.find("*/", i + 2)
                i = len(text) if end == -1 else end + 2
                continue
        out.append(ch)
        i += 1
    # 尾随逗号：`,}` / `,]`
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def load_jsonc(path: Path, failures: Failures):
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as err:
        failures.add(str(path), f"无法读取：{err}")
        return None
    try:
        return json.loads(strip_jsonc(text))
    except json.JSONDecodeError as err:
        failures.add(str(path), f"JSONC 解析失败：{err}")
        return None


def check_keys(where: str, obj, allowed: set[str], failures: Failures) -> None:
    """未知字段一律报错（对应 Rust 的 deny_unknown_fields）。"""
    if not isinstance(obj, dict):
        failures.add(where, "期望是对象")
        return
    for key in obj:
        if key not in allowed:
            failures.add(where, f"未知字段 {key!r}（允许：{'、'.join(sorted(allowed))}）")


def is_valid_relative(path: str) -> bool:
    """对应 schema.rs::validate_relative_path。"""
    if not isinstance(path, str) or not path.strip():
        return False
    if path.startswith("/") or path.startswith("\\"):
        return False
    if len(path) >= 2 and path[1] == ":" and path[0].isalpha():
        return False
    return all(part != ".." for part in re.split(r"[\\/]", path))


def check_detect(where: str, detect, failures: Failures) -> None:
    if detect is None:
        return
    check_keys(where, detect, {"all", "any"}, failures)
    for group in ("all", "any"):
        for index, rule in enumerate(detect.get(group, []) or []):
            rule_where = f"{where}.{group}[{index}]"
            check_keys(rule_where, rule, DETECT_RULE_KEYS, failures)
            if rule.get("root") is not None and rule["root"] not in TARGETS:
                failures.add(rule_where, f"root 取值非法：{rule['root']}")
            modinfo = rule.get("modinfo")
            if modinfo is not None:
                check_keys(f"{rule_where}.modinfo", modinfo, MODINFO_RULE_KEYS, failures)
            if modinfo is None and not rule.get("glob"):
                failures.add(rule_where, "glob 与 modinfo 必须至少有一个")


def check_component(where: str, component, failures: Failures) -> None:
    check_keys(where, component, COMPONENT_KEYS, failures)
    if not str(component.get("id", "")).strip():
        failures.add(where, "id 不能为空")
    if component.get("target") not in TARGETS:
        failures.add(where, f"target 取值非法：{component.get('target')}")
    mode = component.get("mode")
    if mode not in MODES:
        failures.add(where, f"mode 取值非法：{mode}")
    from_path = component.get("from")
    if not is_valid_relative(from_path):
        failures.add(where, f"from 非法（不能为空/绝对路径/含 ..）：{from_path!r}")
    into = component.get("into")
    if mode == "nested_into" and not into:
        failures.add(where, "nested_into 必须提供 into")
    if into is not None and not is_valid_relative(into):
        failures.add(where, f"into 非法：{into!r}")


def check_components(where: str, components, failures: Failures) -> None:
    seen: set[str] = set()
    for index, component in enumerate(components or []):
        item_where = f"{where}[{index}]"
        check_component(item_where, component, failures)
        cid = component.get("id")
        if cid in seen:
            failures.add(item_where, f"组件 id 重复：{cid}")
        seen.add(cid)


def check_manifest(path: Path, manifest, failures: Failures):
    where = str(path)
    check_keys(where, manifest, PACK_KEYS, failures)
    if manifest.get("schema_version") != SUPPORTED_SCHEMA_MAJOR:
        failures.add(where, f"schema_version 必须是 {SUPPORTED_SCHEMA_MAJOR}")
        return None
    pack_id = manifest.get("id")
    if not isinstance(pack_id, str) or not pack_id.strip():
        failures.add(where, "id 不能为空")
    elif re.search(r"[/\\ ]", pack_id):
        failures.add(where, f"id 含非法字符：{pack_id}")
    if not str(manifest.get("name", "")).strip():
        failures.add(where, "name 不能为空")
    check_detect(f"{where}.detect", manifest.get("detect"), failures)
    check_components(f"{where}.install", manifest.get("install"), failures)
    for index, entry in enumerate(manifest.get("expect", []) or []):
        entry_where = f"{where}.expect[{index}]"
        check_keys(entry_where, entry, EXPECT_KEYS, failures)
        if entry.get("root") not in TARGETS:
            failures.add(entry_where, f"root 取值非法：{entry.get('root')}")
        if not is_valid_relative(entry.get("path")):
            failures.add(entry_where, f"path 非法：{entry.get('path')!r}")
    for index, deletion in enumerate(manifest.get("deletions", []) or []):
        if not is_valid_relative(deletion):
            failures.add(f"{where}.deletions[{index}]", f"path 非法：{deletion!r}")
    launch = manifest.get("launch")
    check_keys(f"{where}.launch", launch, LAUNCH_KEYS, failures)
    game_version = manifest.get("game_version")
    if game_version is not None:
        check_game_version(f"{where}.game_version", game_version, failures)
    policy = manifest.get("version_policy")
    if policy is not None:
        check_keys(f"{where}.version_policy", policy, VERSION_POLICY_KEYS, failures)
    seen: set[str] = set()
    for index, component in enumerate(manifest.get("optional_components", []) or []):
        item_where = f"{where}.optional_components[{index}]"
        check_keys(item_where, component, OPTIONAL_KEYS, failures)
        cid = component.get("id")
        if not str(cid or "").strip():
            failures.add(item_where, "id 不能为空")
        if cid in seen:
            failures.add(item_where, f"可选组件 id 重复：{cid}")
        seen.add(cid)
        if not str(component.get("name", "")).strip():
            failures.add(item_where, "name 不能为空")
        check_detect(f"{item_where}.detect", component.get("detect"), failures)
        check_components(f"{item_where}.install", component.get("install"), failures)
    return manifest


def check_game_version(where: str, game_version, failures: Failures) -> None:
    check_keys(where, game_version, GAME_VERSION_KEYS, failures)
    for key in ("min", "max"):
        if not str(game_version.get(key, "")).strip():
            failures.add(where, f"{key} 不能为空")


def check_release(path: Path, release, failures: Failures):
    where = str(path)
    check_keys(where, release, RELEASE_KEYS, failures)
    if release.get("schema_version") != SUPPORTED_SCHEMA_MAJOR:
        failures.add(where, f"schema_version 必须是 {SUPPORTED_SCHEMA_MAJOR}")
        return None
    if not str(release.get("pack", "")).strip():
        failures.add(where, "pack 不能为空")
    if not str(release.get("version", "")).strip():
        failures.add(where, "version 不能为空")
    check_game_version(f"{where}.game_version", release.get("game_version") or {}, failures)
    artifact_ids: set[str] = set()
    for index, artifact in enumerate(release.get("artifacts", []) or []):
        item_where = f"{where}.artifacts[{index}]"
        check_keys(item_where, artifact, ARTIFACT_KEYS, failures)
        aid = artifact.get("id")
        if not str(aid or "").strip():
            failures.add(item_where, "id 不能为空")
        if aid in artifact_ids:
            failures.add(item_where, f"artifact id 重复：{aid}")
        artifact_ids.add(aid)
        if not str(artifact.get("url", "")).strip():
            failures.add(item_where, "url 不能为空")
        sha = artifact.get("sha256")
        if sha is not None and not SHA256_RE.match(str(sha)):
            failures.add(item_where, "sha256 必须是 64 位十六进制")
        if artifact.get("extract", "root") not in EXTRACT_TARGETS:
            failures.add(item_where, f"extract 取值非法：{artifact.get('extract')}")
        strip = artifact.get("strip_components", 0)
        if not isinstance(strip, int) or strip < 0:
            failures.add(item_where, "strip_components 必须是非负整数")
    for index, mirror in enumerate(release.get("mirrors", []) or []):
        item_where = f"{where}.mirrors[{index}]"
        check_keys(item_where, mirror, MIRROR_KEYS, failures)
        for key in ("label", "url"):
            if not str(mirror.get(key, "")).strip():
                failures.add(item_where, f"{key} 不能为空")
    return release


def apply_overrides(where: str, manifest, overrides, failures: Failures):
    """应用差异覆盖并校验引用有效（对应 schema.rs::effective_manifest）。"""
    check_keys(f"{where}.overrides", overrides, OVERRIDE_KEYS, failures)
    install = [dict(component) for component in manifest.get("install", []) or []]
    deletions = list(manifest.get("deletions", []) or [])
    expect = [dict(entry) for entry in manifest.get("expect", []) or []]
    optional = [dict(component) for component in manifest.get("optional_components", []) or []]
    override_where = f"{where}.overrides"

    for index, patch in enumerate(overrides.get("component_patches", []) or []):
        item_where = f"{override_where}.component_patches[{index}]"
        check_keys(item_where, patch, COMPONENT_PATCH_KEYS, failures)
        target = next((c for c in install if c.get("id") == patch.get("id")), None)
        if target is None:
            failures.add(item_where, f"试图修改不存在的组件：{patch.get('id')}")
            continue
        for key in ("target", "mode", "from", "into"):
            if key in patch:
                target[key] = patch[key]

    for index, cid in enumerate(overrides.get("install_remove", []) or []):
        if not any(c.get("id") == cid for c in install):
            failures.add(f"{override_where}.install_remove[{index}]", f"试图移除不存在的组件：{cid}")
    install = [c for c in install if c.get("id") not in set(overrides.get("install_remove", []) or [])]

    existing_ids = {c.get("id") for c in install}
    for index, component in enumerate(overrides.get("install_add", []) or []):
        item_where = f"{override_where}.install_add[{index}]"
        if component.get("id") in existing_ids:
            failures.add(item_where, f"追加的组件 id 已存在：{component.get('id')}")
        existing_ids.add(component.get("id"))
        install.append(component)

    for index, item in enumerate(overrides.get("deletions_remove", []) or []):
        if item not in deletions:
            failures.add(f"{override_where}.deletions_remove[{index}]", f"试图移除不存在的删除项：{item}")
    deletions = [d for d in deletions if d not in set(overrides.get("deletions_remove", []) or [])]
    deletions += list(overrides.get("deletions_add", []) or [])

    for index, item in enumerate(overrides.get("expect_remove", []) or []):
        if not any(_normalize(entry.get("path")) == _normalize(item) for entry in expect):
            failures.add(f"{override_where}.expect_remove[{index}]", f"试图移除不存在的校验项：{item}")
    removed = {_normalize(item) for item in overrides.get("expect_remove", []) or []}
    expect = [entry for entry in expect if _normalize(entry.get("path")) not in removed]
    expect += [dict(entry) for entry in overrides.get("expect_add", []) or []]

    for index, cid in enumerate(overrides.get("optional_components_remove", []) or []):
        if not any(c.get("id") == cid for c in optional):
            failures.add(
                f"{override_where}.optional_components_remove[{index}]",
                f"试图移除不存在的可选组件：{cid}",
            )
    optional = [
        c for c in optional if c.get("id") not in set(overrides.get("optional_components_remove", []) or [])
    ]
    optional_ids = {c.get("id") for c in optional}
    for index, component in enumerate(overrides.get("optional_components_add", []) or []):
        item_where = f"{override_where}.optional_components_add[{index}]"
        if component.get("id") in optional_ids:
            failures.add(item_where, f"追加的可选组件 id 已存在：{component.get('id')}")
        optional_ids.add(component.get("id"))
        optional.append(component)

    # 覆盖之后配方仍必须合法
    check_components(f"{where}(覆盖后).install", install, failures)
    for index, entry in enumerate(expect):
        if entry.get("root") not in TARGETS:
            failures.add(f"{where}(覆盖后).expect[{index}]", f"root 取值非法：{entry.get('root')}")
        if not is_valid_relative(entry.get("path")):
            failures.add(f"{where}(覆盖后).expect[{index}]", f"path 非法：{entry.get('path')!r}")
    for index, deletion in enumerate(deletions):
        if not is_valid_relative(deletion):
            failures.add(f"{where}(覆盖后).deletions[{index}]", f"path 非法：{deletion!r}")


def _normalize(path) -> str:
    return str(path or "").replace("\\", "/")


def resolve_path(root: Path, relative: str) -> Path:
    return root / Path(*relative.replace("\\", "/").split("/"))


def main() -> int:
    args = [arg for arg in sys.argv[1:] if not arg.startswith("--")]
    quiet = "--quiet" in sys.argv[1:]
    root = Path(args[0] if args else ".").resolve()
    failures = Failures()

    index = load_jsonc(root / "index.json", failures)
    if index is None:
        report(root, failures, quiet)
        return 1
    check_keys("index.json", index, INDEX_KEYS, failures)
    if index.get("schema_version") != SUPPORTED_SCHEMA_MAJOR:
        failures.add("index.json", f"schema_version 必须是 {SUPPORTED_SCHEMA_MAJOR}")

    referenced: set[str] = set()
    indexed_packs: dict[str, dict] = {}
    for index_pos, entry in enumerate(index.get("packs", []) or []):
        where = f"index.json.packs[{index_pos}]"
        check_keys(where, entry, INDEX_PACK_KEYS, failures)
        pack_id = entry.get("id")
        if pack_id in indexed_packs:
            failures.add(where, f"索引里 id 重复：{pack_id}")
        indexed_packs[pack_id] = entry
        rel_pack_path = entry.get("path")
        if not isinstance(rel_pack_path, str):
            failures.add(where, "path 必须是字符串")
            continue
        referenced.add(_normalize(rel_pack_path))

        manifest = None
        if not resolve_path(root, rel_pack_path).is_file():
            failures.add(where, f"索引引用的文件不存在：{rel_pack_path}")
        else:
            manifest = check_manifest(Path(rel_pack_path), load_jsonc(resolve_path(root, rel_pack_path), failures), failures)
        if manifest is not None and manifest.get("id") != pack_id:
            failures.add(where, f"索引 id 是 {pack_id}，配方里是 {manifest.get('id')}")
        if manifest is not None:
            for key in ("name", "summary", "homepage"):
                if manifest.get(key) != entry.get(key):
                    failures.add(where, f"{key} 与配方不一致（索引 {entry.get(key)!r}，配方 {manifest.get(key)!r}）")

        indexed_releases: dict[str, dict] = {}
        for release_pos, release_entry in enumerate(entry.get("releases", []) or []):
            release_where = f"{where}.releases[{release_pos}]"
            check_keys(release_where, release_entry, INDEX_RELEASE_KEYS, failures)
            version = release_entry.get("version")
            if version in indexed_releases:
                failures.add(release_where, f"索引里版本重复：{version}")
            indexed_releases[version] = release_entry
            rel_release_path = release_entry.get("path")
            if not isinstance(rel_release_path, str):
                failures.add(release_where, "path 必须是字符串")
                continue
            referenced.add(_normalize(rel_release_path))
            if not resolve_path(root, rel_release_path).is_file():
                failures.add(release_where, f"索引引用的文件不存在：{rel_release_path}")
                continue
            release = check_release(
                Path(rel_release_path), load_jsonc(resolve_path(root, rel_release_path), failures), failures
            )
            if release is None:
                continue
            if release.get("pack") != pack_id:
                failures.add(release_where, f"配方里的 pack 是 {release.get('pack')}，索引里是 {pack_id}")
            if release.get("version") != version:
                failures.add(release_where, f"索引版本 {version} 与配方 {release.get('version')} 不一致")
            if (release.get("game_version") or {}) != (release_entry.get("game_version") or {}):
                failures.add(release_where, "游戏版本区间在索引与配方之间不一致")
            if release.get("overrides") is not None and manifest is not None:
                apply_overrides(str(rel_release_path), manifest, release["overrides"], failures)

        # 磁盘上的 release 文件必须全部登记
        releases_dir = resolve_path(root, rel_pack_path).parent / "releases"
        if releases_dir.is_dir():
            for file in sorted(releases_dir.glob("*.jsonc")):
                rel = file.relative_to(root).as_posix()
                if rel not in referenced:
                    failures.add(str(releases_dir), f"配方文件没有被索引登记：{rel}")

    # packs/ 下的目录必须都有 pack.jsonc 且被索引登记
    packs_dir = root / "packs"
    if not packs_dir.is_dir():
        failures.add("packs", "目录不存在")
    else:
        for child in sorted(packs_dir.iterdir()):
            if not child.is_dir():
                continue
            manifest_rel = f"packs/{child.name}/pack.jsonc"
            if not (child / "pack.jsonc").is_file():
                failures.add(str(child), "缺少 pack.jsonc")
            elif manifest_rel not in referenced:
                failures.add(str(child), f"配方没有被索引登记：{manifest_rel}")

    report(root, failures, quiet)
    return 1 if failures.items else 0


def report(root: Path, failures: Failures, quiet: bool) -> None:
    if failures.items:
        print(f"✗ {root} 校验失败（{len(failures.items)} 项）：", file=sys.stderr)
        for item in failures.items:
            print(f"  - {item}", file=sys.stderr)
        print(
            "\n修好后跑 `python tools/verify.py .` 复查；"
            "维护者还会用应用自身的 `mods7pack verify` 做权威校验。",
            file=sys.stderr,
        )
        return
    if not quiet:
        packs = len(list((root / "packs").glob("*/pack.jsonc"))) if (root / "packs").is_dir() else 0
        releases = len(list((root / "packs").glob("*/releases/*.jsonc"))) if (root / "packs").is_dir() else 0
        print(f"{root} 校验通过：{packs} 个配方，{releases} 个发行版")
    else:
        print("ok")


if __name__ == "__main__":
    sys.exit(main())
