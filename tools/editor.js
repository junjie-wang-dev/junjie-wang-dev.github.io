"use strict";

(() => {
  const sections = [
    { id: "profile", label: "个人信息", icon: "◎", eyebrow: "PROFILE", description: "介绍你是谁，以及在哪里可以找到你。" },
    { id: "bio", label: "个人简介", icon: "≡", eyebrow: "ABOUT YOU", description: "用几个段落介绍你的经历与研究兴趣。" },
    { id: "projects", label: "精选项目", icon: "◇", eyebrow: "SELECTED PROJECTS", description: "展示项目、截图，以及你希望分享的细节。" },
    { id: "publications", label: "发表论文", icon: "▤", eyebrow: "PUBLICATIONS", description: "管理论文、作者和相关链接；用箭头调整展示顺序。" },
    { id: "earlierWork", label: "早期研究", icon: "◷", eyebrow: "EARLIER WORK", description: "补充更早的研究工作与发表成果。" },
    { id: "meta", label: "页面与分享", icon: "↗", eyebrow: "PAGE & SHARING", description: "设置浏览器标题、搜索摘要与社交平台分享信息。" },
    { id: "footer", label: "页脚", icon: "─", eyebrow: "FOOTER", description: "修改主页底部的更新时间与补充说明。" },
  ];
  const state = { content: null, revision: null, active: "profile", dirty: false, saving: false, changes: 0, previewSequence: 0, previewTimer: null, previewController: null, expanded: new Map() };
  const $ = (selector) => document.querySelector(selector);
  const form = $("#content-form");
  let nextFieldId = 0;

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function button(text, className, action, label) {
    const node = element("button", className, text);
    node.type = "button";
    if (label) { node.title = label; node.setAttribute("aria-label", label); }
    node.addEventListener("click", (event) => { event.preventDefault(); action(event); });
    return node;
  }

  function get(path) { return path.reduce((value, key) => value[key], state.content); }
  function set(path, value) { const parent = get(path.slice(0, -1)); parent[path[path.length - 1]] = value; changed(); }
  function clone(value) { return JSON.parse(JSON.stringify(value)); }
  function reconcile(target, source) {
    // Keep live form/list references valid when the server normalizes saved values.
    for (const [key, value] of Object.entries(source)) {
      if (value && typeof value === "object" && target[key] && typeof target[key] === "object") reconcile(target[key], value);
      else target[key] = value;
    }
    if (Array.isArray(target)) target.length = source.length;
  }

  function updateSaveStatus() {
    const status = $("#save-status");
    status.textContent = state.saving ? "正在保存…" : state.dirty ? "有未保存的修改" : "全部已保存";
    status.classList.toggle("dirty", state.dirty);
    $("#save-button").disabled = !state.content || state.saving || !state.dirty;
  }

  function changed() {
    state.dirty = true;
    state.changes += 1;
    updateSaveStatus();
    updateNav();
    schedulePreview();
  }

  function notice(message, { error = false, reload = false, backup = false } = {}) {
    const box = $("#feedback");
    box.replaceChildren(element("span", "notice-text", message));
    box.classList.toggle("error", error);
    const actions = element("span", "notice-actions");
    if (backup) actions.append(button("导出草稿", "text-button", exportDraft));
    if (reload) actions.append(button("重新载入", "text-button", () => loadContent(true)));
    actions.append(button("关闭", "text-button", () => { box.hidden = true; }));
    box.append(actions);
    box.hidden = false;
  }

  async function responseError(response) {
    try { const value = await response.json(); return new Error(value.error || `请求失败（${response.status}）`); }
    catch { return new Error(`请求失败（${response.status}）`); }
  }

  async function loadContent(confirmDiscard = false) {
    if (state.saving) { notice("正在保存，请稍后再重新载入。"); return; }
    if (confirmDiscard && state.dirty && !window.confirm("当前有未保存的修改。重新载入会丢弃这些修改，是否继续？")) return;
    form.inert = true;
    try {
      const response = await fetch("/api/content", { cache: "no-store" });
      if (!response.ok) throw await responseError(response);
      const result = await response.json();
      state.content = result.content;
      state.revision = result.revision;
      state.dirty = false;
      state.changes += 1;
      state.expanded.clear();
      $("#feedback").hidden = true;
      $("#export-draft").disabled = false;
      $("#refresh-preview").disabled = false;
      form.setAttribute("aria-busy", "false");
      updateSaveStatus();
      updateNav();
      renderActive();
      schedulePreview(0);
    } catch (error) {
      $("#save-status").textContent = "读取失败";
      notice(`无法读取主页内容：${error.message}。请确认本地编辑服务仍在运行。`, { error: true, reload: true });
    } finally { form.inert = false; }
  }

  async function saveContent() {
    if (!state.content || state.saving || !state.dirty) return;
    if (!form.reportValidity()) return;
    state.saving = true;
    const savedChanges = state.changes;
    const snapshot = clone(state.content);
    updateSaveStatus();
    try {
      const response = await fetch("/api/save", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ content: snapshot, revision: state.revision }) });
      if (!response.ok) {
        const error = await responseError(response);
        if (response.status === 409) {
          notice(`${error.message}。当前草稿已保留；可先导出备份，再重新载入最新内容。`, { error: true, reload: true, backup: true });
          return;
        }
        throw error;
      }
      const result = await response.json();
      state.revision = result.revision;
      if (state.changes === savedChanges) {
        if (result.content) reconcile(state.content, result.content);
        state.dirty = false;
      }
      notice(state.dirty ? "已保存刚才的内容；你随后所做的修改仍待保存。" : "已保存到本地，主页文件已更新。发布时按原方式提交并推送 GitHub。" );
      schedulePreview(0);
    } catch (error) {
      notice(`保存失败：${error.message}。当前草稿已保留。`, { error: true, backup: true });
    } finally {
      state.saving = false;
      updateSaveStatus();
    }
  }

  function schedulePreview(delay = 450) {
    if (!state.content) return;
    clearTimeout(state.previewTimer);
    if (state.previewController) state.previewController.abort();
    const sequence = ++state.previewSequence;
    $("#preview-status").textContent = "正在更新…";
    state.previewTimer = setTimeout(() => requestPreview(sequence), delay);
  }

  async function requestPreview(sequence) {
    const controller = new AbortController();
    state.previewController = controller;
    try {
      const response = await fetch("/api/preview", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ content: state.content }), signal: controller.signal });
      if (!response.ok) throw await responseError(response);
      const html = await response.text();
      if (sequence !== state.previewSequence) return;
      $("#preview-frame").srcdoc = html;
      $("#preview-status").textContent = "已更新";
      $("#preview-status").removeAttribute("title");
    } catch (error) {
      if (error.name === "AbortError" || sequence !== state.previewSequence) return;
      $("#preview-status").textContent = "请补全内容后预览";
      $("#preview-status").title = error.message;
    }
  }

  function exportDraft() {
    if (!state.content) return;
    const blob = new Blob([JSON.stringify(state.content, null, 2) + "\n"], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = element("a");
    link.href = url;
    link.download = "homepage-content-backup.json";
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function updateNav() {
    const nav = $("#section-nav");
    if (!nav.children.length) {
      sections.forEach((section) => {
        const node = button("", "nav-item", () => {
          state.active = section.id;
          updateNav();
          renderActive();
          form.scrollTop = 0;
          document.body.dataset.view = "editor";
        });
        node.dataset.section = section.id;
        const icon = element("span", "nav-icon", section.icon);
        icon.setAttribute("aria-hidden", "true");
        node.append(icon, element("span", "", section.label));
        nav.append(node);
      });
    }
    nav.querySelectorAll(".nav-item").forEach((node) => {
      if (node.dataset.section === state.active) node.setAttribute("aria-current", "page");
      else node.removeAttribute("aria-current");
      node.querySelector(".nav-count")?.remove();
      const items = state.content?.[node.dataset.section]?.items;
      if (items) node.append(element("span", "nav-count", String(items.length)));
    });
  }

  function field(parent, label, path, options = {}) {
    const wrapper = element("div", "field");
    const input = element(options.multiline ? "textarea" : "input");
    const id = `field-${++nextFieldId}`;
    input.id = id;
    input.dataset.editorPath = path.join(".");
    if (!options.multiline) input.type = options.number ? "number" : "text";
    if (options.multiline) input.rows = options.rows || 3;
    if (options.number) { input.min = "1"; input.max = "10000"; input.step = "1"; }
    if (options.url) { input.inputMode = "url"; input.spellcheck = false; }
    if (options.required) input.required = true;
    if (options.placeholder) input.placeholder = options.placeholder;
    input.value = get(path) ?? "";
    const labelNode = element("label", "field-label", label);
    labelNode.htmlFor = id;
    wrapper.append(labelNode, input);
    if (options.hint) { const hint = element("span", "field-hint", options.hint); hint.id = `${id}-hint`; wrapper.append(hint); input.setAttribute("aria-describedby", hint.id); }
    input.addEventListener("input", () => { set(path, options.number && input.value !== "" ? Number(input.value) : input.value); options.onInput?.(input.value); });
    parent.append(wrapper);
    return input;
  }

  function safeLink(url) {
    const value = url.trim();
    if (!value || /[\u0000-\u0020\u007f]/.test(value)) return false;
    if (/^[a-z][a-z\d+.-]*:/i.test(value)) return /^(https?:|mailto:|tel:)/i.test(value);
    return !value.startsWith("//") && !value.includes("\\");
  }

  function cleanRichHTML(value) {
    const template = document.createElement("template");
    template.innerHTML = value;
    const output = element("div");
    function walk(source, target) {
      for (const node of source.childNodes) {
        if (node.nodeType === Node.TEXT_NODE) { target.append(document.createTextNode(node.textContent)); continue; }
        if (node.nodeType !== Node.ELEMENT_NODE) continue;
        const tag = node.tagName.toLowerCase();
        if (["script", "style", "iframe", "object", "template"].includes(tag)) continue;
        const normalized = tag === "b" ? "strong" : tag === "i" ? "em" : tag;
        if (["strong", "em", "a", "br"].includes(normalized)) {
          if (normalized === "a" && !safeLink(node.getAttribute("href") || "")) { walk(node, target); continue; }
          const copy = element(normalized);
          if (normalized === "a") copy.setAttribute("href", node.getAttribute("href").trim());
          walk(node, copy);
          target.append(copy);
        } else {
          const block = ["div", "p"].includes(tag);
          if (block && target.childNodes.length && target.lastChild.nodeName !== "BR") target.append(element("br"));
          walk(node, target);
          if (block && node.nextSibling && target.lastChild?.nodeName !== "BR") target.append(element("br"));
        }
      }
    }
    walk(template.content, output);
    return output.innerHTML;
  }

  function plainRichText(value) {
    const template = document.createElement("template");
    template.innerHTML = cleanRichHTML(value || "");
    return template.content.textContent;
  }

  function richField(parent, label, path, options = {}) {
    const wrapper = element("div", "field");
    const id = `field-${++nextFieldId}`;
    const labelNode = element("span", "field-label", label);
    labelNode.id = `${id}-label`;
    const wrap = element("div", "rich-wrap");
    const toolbar = element("div", "rich-toolbar");
    toolbar.setAttribute("role", "group");
    toolbar.setAttribute("aria-label", `${label}格式`);
    const editor = element("div", "rich-editor");
    editor.id = id;
    editor.contentEditable = "true";
    editor.setAttribute("role", "textbox");
    editor.setAttribute("aria-multiline", "true");
    editor.setAttribute("aria-labelledby", labelNode.id);
    editor.dataset.placeholder = options.placeholder || "在这里输入内容…";
    editor.dataset.editorPath = path.join(".");
    editor.innerHTML = cleanRichHTML(get(path) || "");
    function format(command) {
      editor.focus();
      document.execCommand(command, false, null);
      set(path, cleanRichHTML(editor.innerHTML));
    }
    const bold = button("B", "format-button", () => format("bold"), "加粗");
    bold.style.fontWeight = "750";
    const italic = button("I", "format-button", () => format("italic"), "斜体");
    italic.style.fontStyle = "italic";
    const link = button("链接", "format-button link-format", () => {
      const selection = window.getSelection();
      if (!selection.rangeCount || selection.isCollapsed || !editor.contains(selection.anchorNode) || !editor.contains(selection.focusNode)) {
        notice("请先选中要添加链接的文字，再点击「链接」。");
        return;
      }
      const range = selection.getRangeAt(0).cloneRange();
      const url = window.prompt("输入链接地址（支持 HTTPS 地址、站内路径、mailto:）：");
      if (url === null) return;
      if (!safeLink(url)) { notice("链接地址无效，请输入 HTTPS 地址、站内路径或 mailto: 地址。", { error: true }); return; }
      editor.focus();
      selection.removeAllRanges();
      selection.addRange(range);
      document.execCommand("createLink", false, url.trim());
      set(path, cleanRichHTML(editor.innerHTML));
    }, "为选中文字添加链接");
    const unlink = button("取消链接", "format-button link-format", () => format("unlink"), "取消选中文字的链接");
    for (const node of [bold, italic, link, unlink]) { node.addEventListener("mousedown", (event) => event.preventDefault()); toolbar.append(node); }
    toolbar.append(element("span", "toolbar-hint", "选中文字设置格式"));
    editor.addEventListener("input", () => set(path, cleanRichHTML(editor.innerHTML)));
    editor.addEventListener("click", (event) => { if (event.target.closest("a")) event.preventDefault(); });
    editor.addEventListener("paste", (event) => {
      event.preventDefault();
      const text = event.clipboardData?.getData("text/plain") || "";
      document.execCommand("insertText", false, text);
      set(path, cleanRichHTML(editor.innerHTML));
    });
    editor.addEventListener("beforeinput", (event) => {
      if (event.inputType === "insertParagraph") { event.preventDefault(); document.execCommand("insertLineBreak", false, null); }
    });
    wrap.append(toolbar, editor);
    wrapper.append(labelNode, wrap);
    if (options.hint) wrapper.append(element("span", "field-hint", options.hint));
    parent.append(wrapper);
    return editor;
  }

  function group(parent, title, hint) {
    const section = element("section", "field-section");
    section.append(element("h3", "", title));
    if (hint) section.append(element("p", "", hint));
    parent.append(section);
    return section;
  }

  function repeatable(parent, path, options) {
    const list = get(path);
    const section = element("section", "list-section");
    const heading = element("div", "list-heading");
    const text = element("div");
    const title = element("h3", "", options.title);
    title.append(element("span", "list-count", `${list.length} 项`));
    text.append(title);
    if (options.hint) text.append(element("p", "", options.hint));
    heading.append(text, button(`＋ ${options.addLabel || "添加"}`, "add-button", () => {
      const index = list.length;
      list.push(clone(options.create()));
      state.expanded.set([...path, index].join("."), true);
      changed();
      renderActive();
      const newCard = form.querySelector(`[data-item-path="${[...path, index].join(".")}"]`);
      newCard?.scrollIntoView({ block: "nearest" });
      newCard?.querySelector("input, textarea, [contenteditable]")?.focus({ preventScroll: true });
    }));
    section.append(heading);
    if (!list.length) section.append(element("div", "empty-list", options.empty || "还没有内容，点击上方按钮添加。"));
    list.forEach((item, index) => {
      const itemPath = [...path, index];
      const key = itemPath.join(".");
      const details = element("details", "item-card");
      details.dataset.itemPath = key;
      details.open = state.expanded.has(key) ? state.expanded.get(key) : list.length <= 2 || index === 0;
      details.addEventListener("toggle", () => state.expanded.set(key, details.open));
      const summary = element("summary", "item-summary");
      const name = options.itemTitle?.(item) || item?.title || item?.label || (typeof item === "string" ? plainRichText(item) : "") || options.itemLabel || "新条目";
      summary.append(element("span", "item-number", String(index + 1).padStart(2, "0")), element("span", "item-title", name));
      const actions = element("span", "item-actions");
      for (const [delta, symbol, label] of [[-1, "↑", "上移"], [1, "↓", "下移"]]) {
        const move = button(symbol, "icon-button", () => {
          [list[index], list[index + delta]] = [list[index + delta], list[index]];
          changed(); renderActive();
        }, `${label}第 ${index + 1} 项`);
        move.disabled = index + delta < 0 || index + delta >= list.length;
        actions.append(move);
      }
      actions.append(button("×", "icon-button danger", () => {
        if (!window.confirm(`确定删除「${name.slice(0, 50)}」吗？保存后此条目将从主页移除。`)) return;
        list.splice(index, 1);
        changed(); renderActive();
      }, `删除第 ${index + 1} 项`));
      summary.append(actions);
      const body = element("div", "item-body");
      options.render(body, itemPath);
      details.append(summary, body);
      section.append(details);
    });
    parent.append(section);
  }

  function links(parent, path, title = "相关链接") {
    repeatable(parent, path, { title, addLabel: "添加链接", create: () => ({ label: "", url: "" }), itemLabel: "新链接", render: (body, itemPath) => {
      field(body, "显示文字", [...itemPath, "label"], { placeholder: "例如：GitHub、[paper]、[doi]" });
      field(body, "链接地址", [...itemPath, "url"], { url: true, placeholder: "https://… 或 papers/example.pdf" });
    } });
  }

  function richList(parent, path, title, addLabel) {
    repeatable(parent, path, { title, addLabel, create: () => "", itemLabel: "新段落", render: (body, itemPath) => richField(body, "内容", itemPath) });
  }

  function imageFields(parent, path, { portrait = false } = {}) {
    const image = get(path);
    const uploadBox = element("div", "image-box");
    const thumb = element("img", "image-thumb");
    thumb.alt = "当前图片预览";
    thumb.hidden = !image.src;
    if (image.src) thumb.src = image.src;
    thumb.addEventListener("error", () => { thumb.hidden = true; });
    thumb.addEventListener("load", () => { thumb.hidden = false; });
    const actions = element("div", "image-upload-actions");
    const file = element("input");
    file.type = "file";
    file.accept = "image/png,image/jpeg,image/gif,image/webp";
    file.hidden = true;
    const uploadButton = button("上传图片", "button secondary", () => file.click());
    actions.append(uploadButton, element("p", "", "PNG、JPG、GIF 或 WebP，最大 10 MB。图片保存在项目中。"), file);
    uploadBox.append(thumb, actions);
    parent.append(uploadBox);
    const source = field(parent, "图片路径或地址", [...path, "src"], { url: true, placeholder: portrait ? "images/profile.jpg" : "images/example.png", hint: "可使用已有图片路径或上传新图片；留空则不显示图片。" });
    source.addEventListener("change", () => { thumb.hidden = !source.value; if (source.value) thumb.src = source.value; });
    field(parent, "图片说明（替代文字）", [...path, "alt"], { placeholder: "简要描述图片，供读屏与图片加载失败时使用" });
    const dimensions = element("div", "field-grid");
    field(dimensions, "原始宽度（像素）", [...path, "width"], { number: true });
    field(dimensions, "原始高度（像素）", [...path, "height"], { number: true });
    parent.append(dimensions);
    file.addEventListener("change", async () => {
      const selected = file.files[0];
      if (!selected) return;
      if (selected.size > 10 * 1024 * 1024) { notice("图片超过 10 MB，请压缩后上传。", { error: true }); file.value = ""; return; }
      uploadButton.disabled = true;
      uploadButton.textContent = "正在上传…";
      try {
        const data = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result.split(",")[1]); reader.onerror = () => reject(new Error("无法读取图片")); reader.readAsDataURL(selected); });
        const response = await fetch("/api/upload", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: selected.name, data }) });
        if (!response.ok) throw await responseError(response);
        const result = await response.json();
        if (get(path) !== image) throw new Error("上传期间条目已改变，请在目标条目重新选择图片");
        set([...path, "src"], result.src);
        source.value = result.src;
        thumb.src = result.src;
        thumb.hidden = false;
        const uploadedImage = new Image();
        uploadedImage.onload = () => {
          // Use dimensions only while this field still refers to the same uploaded image.
          try { if (get(path) !== image || image.src !== result.src) return; }
          catch { return; }
          set([...path, "width"], uploadedImage.naturalWidth);
          set([...path, "height"], uploadedImage.naturalHeight);
          for (const key of ["width", "height"]) {
            const input = parent.querySelector(`[data-editor-path="${[...path, key].join(".")}"]`);
            if (input) input.value = get([...path, key]);
          }
        };
        uploadedImage.src = result.src;
        notice("图片已上传，点击「保存修改」后会用于主页。");
      } catch (error) { notice(`上传失败：${error.message}`, { error: true }); }
      finally { uploadButton.disabled = false; uploadButton.textContent = "上传图片"; file.value = ""; }
    });
  }

  function renderProfile(root) {
    const basic = group(root, "基本资料", "这些信息显示在主页顶部。");
    field(basic, "姓名", ["profile", "name"], { required: true });
    field(basic, "身份 / 研究方向", ["profile", "role"], { multiline: true, rows: 2 });
    field(basic, "学位 / 所属机构", ["profile", "affiliation"]);
    const image = group(root, "个人头像", "保留现有版式，替换你希望展示的照片。");
    imageFields(image, ["profile", "image"], { portrait: true });
    repeatable(root, ["profile", "contacts"], { title: "联系信息", addLabel: "添加信息", create: () => ({ label: "", value: "" }), itemLabel: "新联系信息", render: (body, path) => {
      field(body, "名称", [...path, "label"], { placeholder: "例如：Email、Based" });
      field(body, "内容", [...path, "value"]);
    } });
    links(root, ["profile", "links"], "个人主页链接");
  }

  function renderProjects(root) {
    field(root, "章节标题", ["projects", "title"]);
    repeatable(root, ["projects", "items"], { title: "项目列表", addLabel: "添加项目", create: () => ({ title: "", tagline: "", links: [], image: { src: "", alt: "", width: 1800, height: 1061 }, imageUrl: "", caption: "", paragraphs: [], features: [] }), itemLabel: "新项目", render: (body, path) => {
      field(body, "项目名称", [...path, "title"]);
      field(body, "项目摘要", [...path, "tagline"], { multiline: true, rows: 4 });
      links(body, [...path, "links"]);
      const image = group(body, "项目图片", "可添加截图或项目示意图。");
      imageFields(image, [...path, "image"]);
      field(image, "点击图片后的链接", [...path, "imageUrl"], { url: true, placeholder: "https://…（可留空）" });
      field(image, "图片下方说明", [...path, "caption"], { multiline: true, rows: 2 });
      richList(body, [...path, "paragraphs"], "项目介绍", "添加段落");
      richList(body, [...path, "features"], "项目特点", "添加特点");
    } });
  }

  function renderPublications(root, section) {
    field(root, "章节标题", [section, "title"]);
    repeatable(root, [section, "items"], { title: "论文列表", addLabel: "添加论文", create: () => ({ title: "", authors: "", venue: "", description: "", image: { src: "", alt: "", width: 640, height: 360 }, links: [] }), itemLabel: "新论文", render: (body, path) => {
      field(body, "论文标题", [...path, "title"], { multiline: true, rows: 2 });
      richField(body, "作者", [...path, "authors"], { hint: "可以选中你的姓名，点击 B 加粗。" });
      field(body, "期刊 / 会议 / 年份", [...path, "venue"]);
      field(body, "论文介绍", [...path, "description"], { multiline: true, rows: 4 });
      links(body, [...path, "links"], "论文链接");
      const image = group(body, "论文配图", "例如方法概览、结果图或论文封面。");
      imageFields(image, [...path, "image"]);
    } });
  }

  function renderMeta(root) {
    const page = group(root, "页面信息", "用于浏览器标签与搜索引擎摘要。");
    field(page, "浏览器标签标题", ["meta", "title"]);
    field(page, "页面描述", ["meta", "description"], { multiline: true });
    const social = group(root, "分享卡片", "把主页链接分享至社交平台时展示的信息。");
    field(social, "分享标题", ["meta", "socialTitle"]);
    field(social, "分享描述", ["meta", "socialDescription"], { multiline: true });
    field(social, "分享图片地址", ["meta", "socialImage"], { url: true, hint: "推荐填写可公开访问的完整 HTTPS 图片地址。" });
    field(social, "X / Twitter 分享描述", ["meta", "twitterDescription"], { multiline: true });
  }

  function renderActive() {
    if (!state.content) return;
    const section = sections.find((item) => item.id === state.active);
    $("#section-title").textContent = section.label;
    $("#section-eyebrow").textContent = section.eyebrow;
    $("#section-description").textContent = section.description;
    const root = document.createDocumentFragment();
    if (section.id === "profile") renderProfile(root);
    else if (section.id === "bio") { field(root, "章节标题", ["bio", "title"]); richList(root, ["bio", "paragraphs"], "简介段落", "添加段落"); }
    else if (section.id === "projects") renderProjects(root);
    else if (["publications", "earlierWork"].includes(section.id)) renderPublications(root, section.id);
    else if (section.id === "meta") renderMeta(root);
    else if (section.id === "footer") field(root, "页脚文字", ["footer", "text"], { multiline: true, rows: 4, hint: "例如：Last updated: October 2026 · Built with GitHub Pages" });
    form.replaceChildren(root);
    $("#field-count").textContent = "";
  }

  $("#save-button").addEventListener("click", saveContent);
  $("#export-draft").addEventListener("click", exportDraft);
  $("#refresh-preview").addEventListener("click", () => schedulePreview(0));
  $("#show-preview").addEventListener("click", () => { document.body.dataset.view = "preview"; });
  $("#back-to-editor").addEventListener("click", () => { document.body.dataset.view = "editor"; });
  form.addEventListener("submit", (event) => event.preventDefault());
  document.querySelectorAll("[data-preview-mode]").forEach((node) => node.addEventListener("click", () => {
    $("#preview-viewport").dataset.mode = node.dataset.previewMode;
    document.querySelectorAll("[data-preview-mode]").forEach((item) => item.setAttribute("aria-pressed", String(item === node)));
  }));
  $("#preview-frame").addEventListener("load", () => {
    try { $("#preview-frame").contentDocument.addEventListener("click", (event) => { if (event.target.closest("a")) event.preventDefault(); }); }
    catch { /* Sandbox or browser policies may prevent inspection; the frame remains isolated. */ }
  });
  window.addEventListener("beforeunload", (event) => { if (state.dirty) { event.preventDefault(); event.returnValue = ""; } });
  document.addEventListener("keydown", (event) => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); saveContent(); } });
  updateNav();
  loadContent();
})();
