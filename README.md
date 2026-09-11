# X Article Workbench

把 Markdown 长文转换成一个可离线打开的 X Articles 发布工作台。它会把标题与正文分开，复制富文本时保留标题层级、加粗、引用和链接；正文图片会变成明确的插入标记，并被复制到按顺序编号的图片目录。

整个过程只读取本地 Markdown 与它引用的图片，不修改源文件，也不会把文章上传到任何服务器。

## 解决的问题

X Articles 编辑器不会把 `# 标题`、`**加粗**` 等 Markdown 源码自动解释成排版。直接复制 `.md` 文件通常只会得到原始符号。本项目先把 Markdown 安全地渲染成富文本，再同时写入 `text/html` 和 `text/plain` 两种剪贴板数据。

图片无法随着本地 Markdown 引用稳定上传到 X，因此工作台使用一条更可靠的流程：整体复制文字，按 `【插入图 N】` 和右侧图片清单逐张上传。

## 安装

```bash
git clone https://github.com/Yuriloll/x-article-workbench.git
cd x-article-workbench
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -e .
```

Windows PowerShell 激活虚拟环境时使用：

```powershell
.venv\Scripts\Activate.ps1
```

## 生成工作台

```bash
x-article-workbench /绝对路径/文章.md --out dist/my-article
```

也可以直接运行脚本：

```bash
python3 scripts/build_workbench.py /绝对路径/文章.md --out dist/my-article
```

生成后打开 `dist/my-article/index.html`：

1. 点击“复制标题”，粘贴到 X 的标题框。
2. 点击“复制正文（含格式）”，在正文编辑区正常粘贴。不要使用“粘贴为纯文本”。
3. 上传 `images/00-cover-*` 作为封面。
4. 按 `【插入图 1】`、`【插入图 2】` 的位置上传对应编号图片，然后删除标记。

同名 ZIP 会生成在输出目录旁边，方便移动整套发布材料。

## 封面识别

默认 `--cover auto`：如果第一张图片紧跟在第一个一级标题后面，它会被识别为封面，不进入正文。

```bash
# 强制把全文第一张图作为封面
x-article-workbench article.md --out dist/article --cover first

# 所有图片都进入正文
x-article-workbench article.md --out dist/article --cover none
```

重建已经生成的目录时使用 `--force`。为了防止误删，工具只会覆盖包含有效 `manifest.json` 且确认由本项目生成的目录。

## 在其他项目中使用

工具接受任意位置的 Markdown，所以无需把文章复制进仓库：

```bash
/path/to/x-article-workbench/.venv/bin/x-article-workbench \
  /path/to/another-project/article.md \
  --out /path/to/another-project/x-publish
```

图片路径按 Markdown 文件所在目录解析。例如 `![图](assets/chart.png)` 会读取文章旁边的 `assets/chart.png`。缺图时构建会停止并给出完整路径，避免发布包悄悄少图。

默认只允许读取 Markdown 所在目录及其子目录内的图片，防止一份外来的 Markdown 偷偷打包其他本地文件。确实需要引用共享素材目录时，检查路径后添加 `--allow-outside-images`。

## 在其他 Codex 会话中使用

本仓库本身也是一个 Codex Skill。运行：

```bash
bash scripts/install_skill.sh
```

重新打开一个 Codex 会话后，可以直接说：

```text
使用 $x-article-workbench，把 /path/to/article.md 做成 X 文章复制工作台，输出到 /path/to/x-publish。
```

如果不安装 Skill，也可以在新会话中给出本仓库路径和上面的生成命令。

## 输出内容

- `index.html`：离线发布工作台。
- `标题.txt`：单独的标题。
- `正文-纯文字备用.txt`：完全不依赖富文本的备用正文。
- `images/`：封面与正文图片，按发布顺序编号。
- `manifest.json`：源文件哈希、图片哈希、计数、警告和构建信息。
- `OUTPUT.zip`：同名完整发布包。

## 安全与边界

- Markdown 内的原始 HTML 会显示成文字，不会作为页面代码执行。
- `javascript:`、`data:` 等危险链接或图片协议会让构建失败。
- 相对超链接会被拒绝，因为粘贴到 X 后无法得到可靠的网址；正文图片仍支持相对路径。
- 默认拒绝读取文章目录之外的图片，包括指向外部文件的符号链接。
- 远程图片不会被自动加载或下载，防止打开工作台时向第三方泄露访问记录。工作台只提供“打开图源”链接。
- 默认拒绝覆盖已有目录；即使使用 `--force`，也不会删除未被本项目标记的目录。
- 构建与复制不会替你发布文章。

## 开发与验证

```bash
python3 -m unittest discover -s tests -v
python3 /path/to/skill-creator/scripts/quick_validate.py .
```

MIT License
