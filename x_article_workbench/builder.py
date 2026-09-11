from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from html import escape
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Iterable, List, Optional
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET
import zipfile

try:
    import markdown
except ModuleNotFoundError as exc:  # pragma: no cover - exercised by installation state
    raise SystemExit(
        "缺少依赖 Markdown。请在项目目录运行：python3 -m pip install -e ."
    ) from exc


GENERATOR = "x-article-workbench"
VERSION = "1.0.1"
SAFE_LINK_SCHEMES = {"", "http", "https", "mailto"}
SAFE_IMAGE_SCHEMES = {"", "http", "https"}
COMMON_X_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
TEMPLATE_PATH = Path(__file__).with_name("templates") / "workbench.html"


class BuildError(RuntimeError):
    pass


@dataclass
class Asset:
    source: str
    kind: str
    body_index: Optional[int]
    name: Optional[str]
    alt: str
    placement: str
    remote: bool
    sha256: Optional[str]
    warning: Optional[str]


def compact_text(element: ET.Element) -> str:
    return re.sub(r"\s+", "", "".join(element.itertext()))


def safe_markdown(source: str) -> str:
    # Markdown syntax still works because `>` is retained for blockquotes. Raw HTML
    # becomes visible text and cannot enter the generated document as executable DOM.
    escaped = source.replace("&", "&amp;").replace("<", "&lt;")
    return markdown.markdown(escaped, extensions=["fenced_code", "tables"])


def parse_document(source: str) -> ET.Element:
    rendered = safe_markdown(source)
    try:
        return ET.fromstring("<article>" + rendered + "</article>")
    except ET.ParseError as exc:
        raise BuildError(f"Markdown 渲染结果无法解析：{exc}") from exc


def is_remote(value: str) -> bool:
    return value.startswith("//") or urlsplit(value).scheme.lower() in {"http", "https"}


def validate_urls(root: ET.Element) -> None:
    for link in root.findall(".//a"):
        href = link.attrib.get("href", "")
        scheme = urlsplit(href).scheme.lower()
        if scheme not in SAFE_LINK_SCHEMES:
            raise BuildError(f"链接使用了不安全的协议：{href}")
        if not scheme and href and not href.startswith("#"):
            raise BuildError(f"X 文章不能可靠使用相对链接：{href}")
        link.set("rel", "noopener noreferrer")
    for image in root.findall(".//img"):
        src = image.attrib.get("src", "")
        scheme = urlsplit(src).scheme.lower()
        if scheme not in SAFE_IMAGE_SCHEMES:
            raise BuildError(f"图片使用了不安全的协议：{src}")


def sole_image(element: ET.Element) -> Optional[ET.Element]:
    children = list(element)
    if element.tag == "p" and len(children) == 1 and children[0].tag == "img":
        if not (element.text or "").strip() and not (children[0].tail or "").strip():
            return children[0]
    return None


def choose_cover(root: ET.Element, title_node: Optional[ET.Element], mode: str) -> Optional[ET.Element]:
    images = root.findall(".//img")
    if not images or mode == "none":
        return None
    if mode == "first":
        return images[0]
    if title_node is None:
        return None
    children = list(root)
    start = children.index(title_node) + 1
    for element in children[start:]:
        if element.tag == "hr":
            continue
        image = sole_image(element)
        if image is not None:
            return image
        if compact_text(element):
            return None
    return None


def filename_part(value: str) -> str:
    stem = Path(unquote(urlsplit(value).path)).stem
    stem = re.sub(r"[^\w.-]+", "-", stem, flags=re.UNICODE).strip("-._")
    return stem[:48] or "image"


def local_image_path(source_file: Path, value: str) -> Path:
    parsed = urlsplit(value)
    raw = unquote(parsed.path)
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = source_file.parent / candidate
    return candidate.resolve()


def previous_text(root: ET.Element, target: ET.Element) -> str:
    previous = "文章开头"
    for element in list(root):
        if element is target or target in list(element.iter()):
            break
        if element.tag not in {"img", "hr"}:
            text = re.sub(r"\s+", " ", "".join(element.itertext())).strip()
            if text:
                previous = ("…" + text[-60:]) if len(text) > 60 else text
    return previous


def copy_assets(
    root: ET.Element,
    source_file: Path,
    images_dir: Path,
    cover: Optional[ET.Element],
    allow_outside_images: bool,
) -> List[Asset]:
    assets: List[Asset] = []
    body_number = 0
    for serial, image in enumerate(root.findall(".//img")):
        value = image.attrib.get("src", "").strip()
        if not value:
            raise BuildError("发现没有路径的 Markdown 图片。")
        kind = "cover" if image is cover else "body"
        if kind == "body":
            body_number += 1
        index = None if kind == "cover" else body_number
        image.set("data-xaw-id", str(serial))
        alt = image.attrib.get("alt", "").strip() or ("封面" if kind == "cover" else f"正文图 {body_number}")
        placement = "上传到 X 文章的封面位置。" if kind == "cover" else f"放在【插入图 {body_number}】处；前文为“{previous_text(root, image)}”。"
        remote = is_remote(value)
        name: Optional[str] = None
        digest: Optional[str] = None
        warning: Optional[str] = None
        if remote:
            warning = "远程图片未下载，工作台不会自动请求第三方服务器。"
        else:
            source_path = local_image_path(source_file, value)
            source_root = source_file.parent.resolve()
            if not allow_outside_images:
                try:
                    source_path.relative_to(source_root)
                except ValueError as exc:
                    raise BuildError(
                        f"图片位于文章目录之外：{source_path}。确认可信后使用 --allow-outside-images。"
                    ) from exc
            if not source_path.is_file():
                raise BuildError(f"找不到本地图片：{source_path}")
            suffix = source_path.suffix.lower() or ".bin"
            prefix = "00-cover" if kind == "cover" else f"{body_number:02d}"
            name = f"{prefix}-{filename_part(value)}{suffix}"
            destination = images_dir / name
            shutil.copy2(source_path, destination)
            if destination.read_bytes() != source_path.read_bytes():
                raise BuildError(f"图片复制校验失败：{source_path}")
            digest = hashlib.sha256(destination.read_bytes()).hexdigest()
            if suffix not in COMMON_X_IMAGE_EXTENSIONS:
                warning = f"{suffix} 可能需要转换为 X 支持的图片格式。"
        assets.append(Asset(value, kind, index, name, alt, placement, remote, digest, warning))
    return assets


def parent_map(root: ET.Element):
    return {child: parent for parent in root.iter() for child in parent}


def remove_keep_tail(parent: ET.Element, child: ET.Element) -> None:
    children = list(parent)
    index = children.index(child)
    tail = child.tail or ""
    parent.remove(child)
    if tail:
        if index:
            previous = list(parent)[index - 1]
            previous.tail = (previous.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail


def replace_image(root: ET.Element, image: ET.Element, marker: Optional[ET.Element]) -> None:
    parents = parent_map(root)
    parent = parents[image]
    children = list(parent)
    index = children.index(image)
    tail = image.tail
    if marker is None:
        remove_keep_tail(parent, image)
        return
    marker.tail = tail
    parent.remove(image)
    parent.insert(index, marker)


def append_text(element: ET.Element, value: str) -> None:
    if not value:
        return
    children = list(element)
    if children:
        children[-1].tail = (children[-1].tail or "") + value
    else:
        element.text = (element.text or "") + value


def list_item_without_nested(item: ET.Element) -> tuple[ET.Element, List[ET.Element]]:
    clone = deepcopy(item)
    nested = [child for child in list(clone) if child.tag in {"ul", "ol"}]
    for child in nested:
        remove_keep_tail(clone, child)
    return clone, nested


def paragraph_from_item(item: ET.Element, prefix: str) -> ET.Element:
    paragraph = ET.Element("p", {"class": "list-line", "data-prefix": prefix})
    paragraph.text = prefix + (item.text or "")
    for child in list(item):
        item.remove(child)
        if child.tag == "p":
            append_text(paragraph, child.text or "")
            for inline in list(child):
                child.remove(inline)
                paragraph.append(inline)
            append_text(paragraph, child.tail or "")
        else:
            paragraph.append(child)
    return paragraph


def flatten_list(element: ET.Element, depth: int = 0) -> Iterable[tuple[ET.Element, str]]:
    ordered = element.tag == "ol"
    start_value = int(element.attrib.get("start", "1")) if element.attrib.get("start", "1").isdigit() else 1
    for offset, item in enumerate([c for c in list(element) if c.tag == "li"]):
        own, nested = list_item_without_nested(item)
        source_text = compact_text(own)
        bullet = f"{start_value + offset}、" if ordered else "• "
        prefix = "　" * depth + bullet
        yield paragraph_from_item(own, prefix), source_text
        for child in nested:
            yield from flatten_list(child, depth + 1)


def strip_markers_for_comparison(element: ET.Element) -> str:
    clone = deepcopy(element)
    for class_name in ("image-marker", "table-separator"):
        for marker in list(clone.findall(f".//*[@class='{class_name}']")):
            replace_image(clone, marker, None)
    return compact_text(clone)


def table_rows(table: ET.Element) -> List[ET.Element]:
    rows: List[ET.Element] = []
    for row in table.findall(".//tr"):
        cells = [cell for cell in list(row) if cell.tag in {"th", "td"}]
        if not cells:
            continue
        paragraph = ET.Element("p", {"class": "table-line"})
        for index, cell in enumerate(cells):
            if index:
                separator = ET.Element("span", {"class": "table-separator"})
                separator.text = " ｜ "
                paragraph.append(separator)
            append_text(paragraph, cell.text or "")
            for child in list(cell):
                paragraph.append(deepcopy(child))
        if any(cell.tag == "th" for cell in cells):
            strong = ET.Element("strong")
            strong.text = paragraph.text
            paragraph.text = None
            for child in list(paragraph):
                paragraph.remove(child)
                strong.append(child)
            paragraph.append(strong)
        rows.append(paragraph)
    return rows


def transform_body(root: ET.Element, title_node: Optional[ET.Element], cover: Optional[ET.Element], assets: List[Asset]) -> tuple[ET.Element, int]:
    body = ET.Element("article")
    asset_by_id = {str(index): asset for index, asset in enumerate(assets)}
    expected: List[str] = []
    actual: List[str] = []
    for original in list(root):
        if original is title_node or original.tag == "hr":
            continue
        clone = deepcopy(original)
        for image in list(clone.findall(".//img")):
            asset = asset_by_id[image.attrib["data-xaw-id"]]
            marker = None
            if asset.kind == "body":
                marker = ET.Element("span", {"class": "image-marker", "id": f"image-{asset.body_index}"})
                marker.text = f"【插入图 {asset.body_index}】"
            replace_image(clone, image, marker)
        if original.tag in {"ul", "ol"}:
            for paragraph, source_text in flatten_list(clone):
                prefix = paragraph.attrib.pop("data-prefix")
                expected.append(source_text)
                value = compact_text(paragraph)
                actual.append(value[len(re.sub(r"\s+", "", prefix)):])
                body.append(paragraph)
            continue
        if original.tag == "table":
            original_rows = table_rows(original)
            output_rows = table_rows(clone)
            if len(original_rows) != len(output_rows):
                raise BuildError("表格转换失败：行数发生变化。")
            for source_row, output_row in zip(original_rows, output_rows):
                expected.append(strip_markers_for_comparison(source_row))
                actual.append(strip_markers_for_comparison(output_row))
                body.append(output_row)
            continue
        source_clone = deepcopy(original)
        for image in list(source_clone.findall(".//img")):
            replace_image(source_clone, image, None)
        source_text = compact_text(source_clone)
        if source_text:
            expected.append(source_text)
        for code in clone.findall(".//code"):
            code.tag = "span"
        if clone.tag == "h1":
            clone.tag = "h2"
        elif clone.tag in {"h4", "h5", "h6"}:
            clone.tag = "h3"
        if clone.tag == "p" and len(list(clone)) == 1 and list(clone)[0].tag == "em" and not (clone.text or "").strip():
            clone.set("class", "caption")
        rendered_text = strip_markers_for_comparison(clone)
        if rendered_text:
            actual.append(rendered_text)
        if compact_text(clone) or clone.findall(".//*[@class='image-marker']") or clone.attrib.get("class") == "image-marker":
            body.append(clone)
    if expected != actual:
        for index, pair in enumerate(zip(expected, actual)):
            if pair[0] != pair[1]:
                raise BuildError(
                    f"正文完整性校验失败，第 {index + 1} 个文本块发生变化："
                    f"源={pair[0][:80]!r}，输出={pair[1][:80]!r}。"
                )
        raise BuildError(f"正文完整性校验失败：源文本块 {len(expected)}，输出文本块 {len(actual)}。")
    return body, len(expected)


def plain_inline(element: ET.Element) -> str:
    if element.attrib.get("class") == "image-marker":
        return element.text or ""
    value = element.text or ""
    for child in element:
        value += plain_inline(child)
        value += child.tail or ""
    if element.tag == "a":
        href = element.attrib.get("href", "")
        if href and href not in value:
            value += f"（{href}）"
    return value


def serialize_body(body: ET.Element) -> str:
    return "\n".join(ET.tostring(element, encoding="unicode", method="html") for element in body)


def render_cards(assets: List[Asset]) -> str:
    cards = []
    for asset in assets:
        label = "封面" if asset.kind == "cover" else f"图 {asset.body_index}"
        if asset.remote:
            media = f'<div class="remote-placeholder">远程图片未自动加载<br>{escape(asset.source)}</div>'
            action = f'<a class="image-action" href="{escape(asset.source, quote=True)}" target="_blank" rel="noopener noreferrer">打开图源 ↗</a>'
        else:
            href = "images/" + escape(asset.name or "", quote=True)
            media = f'<a href="{href}" download><img src="{href}" alt="{escape(asset.alt, quote=True)}" loading="lazy"></a>'
            action = f'<a class="image-action" href="{href}" download>下载{label}</a>'
        jump = "" if asset.kind == "cover" else f'<a class="position-link" href="#image-{asset.body_index}">查看位置 ↗</a>'
        warning = f'<p class="warning">{escape(asset.warning)}</p>' if asset.warning else ""
        cards.append(
            f'<section class="image-card">{media}<div><b>{label} · {escape(asset.alt)}</b>'
            f'<p>{escape(asset.placement)}</p>{warning}{action}{jump}</div></section>'
        )
    if not cards:
        return '<p class="side-note">这篇文章没有引用图片。</p>'
    return "\n".join(cards)


def fill_template(values: dict[str, str]) -> str:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    tokens = set(re.findall(r"{{([A-Z_]+)}}", template))
    if tokens != set(values):
        raise BuildError(f"模板字段不一致：需要 {sorted(tokens)}，收到 {sorted(values)}")
    return re.sub(r"{{([A-Z_]+)}}", lambda match: values[match.group(1)], template)


def verify_existing_output(output: Path, force: bool) -> None:
    if not output.exists():
        return
    if not force:
        raise BuildError(f"输出目录已存在：{output}。确认后加 --force 重建。")
    manifest = output / "manifest.json"
    if not manifest.is_file():
        raise BuildError(f"拒绝覆盖未标记为本工具生成的目录：{output}")
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildError(f"无法验证旧输出目录：{output}") from exc
    if data.get("generator") != GENERATOR:
        raise BuildError(f"拒绝覆盖其他程序生成的目录：{output}")


def make_zip(output: Path, force: bool) -> Path:
    archive = output.with_suffix(".zip")
    if archive.exists() and not force:
        raise BuildError(f"压缩包已存在：{archive}。确认后加 --force 重建。")
    temporary = archive.with_name("." + archive.name + ".tmp")
    if temporary.exists():
        temporary.unlink()
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as bundle:
        for file in sorted(output.rglob("*")):
            if file.is_file():
                bundle.write(file, Path(output.name) / file.relative_to(output))
    temporary.replace(archive)
    return archive


def build(source_file: Path, output: Path, cover_mode: str = "auto", title_override: Optional[str] = None, force: bool = False, create_zip: bool = True, allow_outside_images: bool = False) -> dict:
    source_file = source_file.expanduser().resolve()
    output = output.expanduser().resolve()
    if not source_file.is_file():
        raise BuildError(f"找不到 Markdown 文件：{source_file}")
    if source_file.suffix.lower() not in {".md", ".markdown", ".txt"}:
        raise BuildError("输入文件应为 .md、.markdown 或 .txt。")
    verify_existing_output(output, force)
    archive = output.with_suffix(".zip")
    if create_zip and archive.exists() and not force:
        raise BuildError(f"压缩包已存在：{archive}。确认后加 --force 重建。")
    source_bytes = source_file.read_bytes()
    try:
        source = source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BuildError("输入文件不是 UTF-8 编码。") from exc
    root = parse_document(source)
    validate_urls(root)
    title_node = next((element for element in list(root) if element.tag == "h1"), None)
    title = (title_override or ("".join(title_node.itertext()).strip() if title_node is not None else source_file.stem)).strip()
    if not title:
        raise BuildError("文章标题为空。请添加一级标题或使用 --title。")
    cover = choose_cover(root, title_node, cover_mode)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=str(output.parent)))
    try:
        images_dir = temp_root / "images"
        images_dir.mkdir()
        assets = copy_assets(root, source_file, images_dir, cover, allow_outside_images)
        body, text_blocks = transform_body(root, title_node, cover, assets)
        body_html = serialize_body(body)
        plain = "\n\n".join(plain_inline(element).strip() for element in body if plain_inline(element).strip()) + "\n"
        build_time = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        cover_count = sum(asset.kind == "cover" for asset in assets)
        body_count = sum(asset.kind == "body" for asset in assets)
        page = fill_template({
            "DOCUMENT_TITLE": escape(title + " · X 文章复制台"),
            "TITLE_HTML": escape(title),
            "BODY_HTML": body_html,
            "IMAGE_COUNT": str(body_count),
            "COVER_COUNT": str(cover_count),
            "CARDS_HTML": render_cards(assets),
            "PLAIN_JSON": json.dumps(plain, ensure_ascii=False).replace("<", "\\u003c").replace("&", "\\u0026"),
            "BUILD_TIME": escape(build_time),
            "SOURCE_NAME": escape(source_file.name),
        })
        (temp_root / "index.html").write_text(page, encoding="utf-8")
        (temp_root / "标题.txt").write_text(title + "\n", encoding="utf-8")
        (temp_root / "正文-纯文字备用.txt").write_text(plain, encoding="utf-8")
        warnings = [asset.warning for asset in assets if asset.warning]
        manifest = {
            "generator": GENERATOR,
            "version": VERSION,
            "source": source_file.name,
            "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "built_at": build_time,
            "title": title,
            "text_blocks_verified": text_blocks,
            "headings": len(body.findall(".//h2")) + len(body.findall(".//h3")),
            "bold_spans": len(body.findall(".//strong")),
            "source_links": len(body.findall(".//a")),
            "cover_images": cover_count,
            "body_images": body_count,
            "warnings": warnings,
            "images": [asdict(asset) for asset in assets],
        }
        (temp_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if output.exists():
            shutil.rmtree(output)
        temp_root.replace(output)
        if create_zip:
            manifest["archive"] = str(make_zip(output, force))
        return manifest
    except Exception:
        if temp_root.exists():
            shutil.rmtree(temp_root)
        raise


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description="把 Markdown 转换为可复制到 X Articles 的离线工作台。")
    command.add_argument("source", type=Path, help="Markdown 文件")
    command.add_argument("--out", type=Path, required=True, help="输出目录")
    command.add_argument("--cover", choices=["auto", "first", "none"], default="auto", help="封面识别方式；默认 auto")
    command.add_argument("--title", help="覆盖 Markdown 中的一级标题")
    command.add_argument("--force", action="store_true", help="仅覆盖由本工具生成的既有目录和压缩包")
    command.add_argument("--no-zip", action="store_true", help="不生成同名 ZIP")
    command.add_argument("--allow-outside-images", action="store_true", help="允许读取 Markdown 所在目录之外的本地图片")
    return command


def main(argv: Optional[List[str]] = None) -> None:
    args = parser().parse_args(argv)
    try:
        result = build(args.source, args.out, args.cover, args.title, args.force, not args.no_zip, args.allow_outside_images)
    except BuildError as exc:
        print(f"构建失败：{exc}", file=sys.stderr)
        raise SystemExit(2)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
