# 主页内容编辑器

用中文表单修改主页内容，自动生成可直接发布到 GitHub Pages 的 `index.html`。只需要 Python 3.9 或更新版本，无需安装其他依赖。

## 开始使用

macOS 可以双击仓库里的 **`编辑主页.command`**。也可以在仓库目录运行：

```bash
python3 edit_homepage.py
```

浏览器会自动打开 <http://127.0.0.1:8765/editor>。保持终端窗口开启，使用完毕后按 `Ctrl+C` 关闭编辑器。

1. 在左侧选择基本资料、简介、项目、论文、早期工作、分享设置或页脚。
2. 在表单中修改文字、链接和图片；项目、论文等支持新增、删除及调整顺序。简介等富文本支持加粗、斜体和插入链接。
3. 右侧自动预览修改后的主页，也可以切换手机宽度。
4. 点击「保存」。工具会将内容存入 `content.json`，同时更新 `index.html`。
5. 按你原来的方式提交并推送仓库，GitHub Pages 部署后线上主页就会更新。

**预览不会写入主页，保存只更新本地文件。** 离开页面前请保存；工具会在有未保存修改时提醒。多开窗口或其他程序修改了源文件时，保存会提示冲突，避免覆盖已有修改。

## 图片与链接

- 图片可以填写仓库中的相对路径，例如 `images/profile.jpg`，也可以填写完整的 `https://` 图片网址。
- 「上传图片」支持 PNG、JPEG、GIF、WebP，每张不超过 10 MB。上传后立即写入 `images/uploads/`，点击保存后才会在主页中引用它。发布时记得一并提交这些图片；未使用的上传文件可自行删除。
- 图片说明用于无障碍阅读和图片加载失败时的替代文字；尺寸填写图片自身的像素宽高。
- 论文链接可以填写 `papers/example.pdf` 或完整网址；个人链接也支持 `mailto:`、`tel:`。
- SEO 和分享设置独立于页面正文，修改姓名、研究方向后可以一并更新这些字段。

## 文件说明

| 文件 | 用途 |
| --- | --- |
| `content.json` | 主页内容的唯一数据源，由编辑器管理 |
| `templates/homepage.html` | 页面布局、样式和分享标签模板 |
| `index.html` | 自动生成的完整静态主页，提交后由 GitHub Pages 展示 |
| `edit_homepage.py` | 本地服务、校验及生成工具 |
| `tools/editor.*` | 中文编辑器界面 |

日常更新使用编辑器即可。不要单独修改生成后的 `index.html`，否则下次保存时会被重新生成；调整布局或样式时修改模板。

## 其他命令

```bash
# 不自动打开浏览器
python3 edit_homepage.py --no-browser

# 默认端口被占用时选择其他端口
python3 edit_homepage.py --port 8766

# 从内容文件重新生成静态主页
python3 edit_homepage.py --build

# 检查数据有效性，以及生成的主页是否与源文件一致
python3 edit_homepage.py --check

# 运行回归测试（使用临时目录，不修改真实主页）
python3 -m unittest discover -s tests -v
```

编辑器只监听本机 `127.0.0.1`，无需登录或配置后台。线上主页保持完整静态 HTML，访客不需要运行 Python 或 JavaScript 来阅读内容。
