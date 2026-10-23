'use strict';

(() => {
  const byId = (id) => document.getElementById(id);
  const ui = {
    search: byId('docs-search'), searchResults: byId('search-results'), searchList: byId('search-list'),
    searchSummary: byId('search-summary'), reading: byId('reading-view'), nav: byId('docs-nav'),
    sidebar: byId('docs-sidebar'), main: byId('docs-main'), menu: byId('menu-toggle'), backdrop: byId('nav-backdrop'),
    title: byId('page-title'), description: byId('page-description'), section: byId('page-section'),
    article: byId('article-content'), toc: byId('docs-toc'), tocLinks: byId('toc-links'),
    status: byId('load-status'), footer: byId('article-footer'), pagination: byId('page-pagination'),
    copyLink: byId('copy-page-link'), toast: byId('docs-toast'), version: byId('docs-version')
  };
  const mobile = window.matchMedia('(max-width: 700px)');
  let catalog = [];
  let version = '';
  let currentPage = null;
  let currentPageId = '';
  let initialized = false;
  let offline = false;
  let requestNumber = 0;
  let requestController = null;
  let headingObserver = null;
  let toastTimer;
  const pageCache = new Map();

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function routeHref(pageId, headingId = '') {
    return '#/' + encodeURIComponent(pageId) + (headingId ? '/' + encodeURIComponent(headingId) : '');
  }

  function readRoute() {
    const raw = location.hash.startsWith('#/') ? location.hash.slice(2).split('/') : [];
    try {
      return { page: decodeURIComponent(raw[0] || catalog[0]?.id || 'overview'), heading: decodeURIComponent(raw.slice(1).join('/') || '') };
    } catch {
      return { page: '', heading: '' };
    }
  }

  function toast(message) {
    clearTimeout(toastTimer);
    ui.toast.textContent = message;
    ui.toast.hidden = false;
    toastTimer = setTimeout(() => { ui.toast.hidden = true; }, 2500);
  }

  async function copyText(value) {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(value);
      } else {
        const previousFocus = document.activeElement;
        const field = node('textarea', 'sr-only');
        field.value = value;
        field.setAttribute('aria-label', '待复制内容');
        document.body.append(field);
        field.select();
        const copied = document.execCommand('copy');
        field.remove();
        previousFocus?.focus({ preventScroll: true });
        if (!copied) throw new Error('Clipboard unavailable');
      }
      return true;
    } catch {
      toast('无法访问剪贴板，请选择内容后手动复制。');
      return false;
    }
  }

  function setMenu(open, returnFocus = false) {
    const visible = open && mobile.matches;
    document.body.classList.toggle('nav-open', visible);
    ui.menu.setAttribute('aria-expanded', String(visible));
    ui.menu.setAttribute('aria-label', visible ? '关闭文档导航' : '打开文档导航');
    ui.backdrop.hidden = !visible;
    ui.sidebar.inert = mobile.matches && !visible;
    ui.main.inert = visible;
    if (visible) (ui.nav.querySelector('[aria-current="page"]') || ui.nav.querySelector('a'))?.focus();
    else if (returnFocus) ui.menu.focus();
  }

  function renderNavigation() {
    ui.nav.replaceChildren();
    const sections = new Map();
    catalog.forEach((page) => {
      const label = page.section || '文档';
      if (!sections.has(label)) {
        const group = node('section', 'nav-section');
        group.append(node('h2', '', label));
        ui.nav.append(group);
        sections.set(label, group);
      }
      const link = node('a', 'nav-link', page.title);
      link.href = routeHref(page.id);
      link.dataset.pageId = page.id;
      sections.get(label).append(link);
    });
  }

  function markNavigation(id) {
    ui.nav.querySelectorAll('a').forEach((link) => {
      if (link.dataset.pageId === id) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });
  }

  function setSearchVisibility(show) {
    ui.searchResults.hidden = !show;
    ui.reading.hidden = show;
    ui.toc.hidden = show || !currentPage;
  }

  function clearSearch() {
    ui.search.value = '';
    setSearchVisibility(false);
    document.title = currentPage ? `${currentPage.title} · Chaos 文档${version ? ` · ${version}` : ''}` : 'Chaos · 文档中心';
  }

  function renderError(message, retry) {
    ui.status.hidden = false;
    ui.status.className = 'error-message';
    ui.status.replaceChildren(node('span', '', message));
    if (retry) {
      const button = node('button', '', '重新载入');
      button.type = 'button';
      button.addEventListener('click', retry);
      ui.status.append(button);
    }
    ui.article.replaceChildren();
    ui.footer.hidden = true;
    ui.toc.hidden = true;
    ui.copyLink.disabled = true;
  }

  async function fetchJSON(url, signal) {
    const response = await fetch(url, { signal, headers: { Accept: 'application/json' } });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  function setCurrentHeading(id) {
    ui.tocLinks.querySelectorAll('a').forEach((link) => {
      if (link.dataset.headingId === id) link.setAttribute('aria-current', 'location');
      else link.removeAttribute('aria-current');
    });
  }

  function renderTOC(page) {
    headingObserver?.disconnect();
    ui.tocLinks.replaceChildren();
    const headings = (page.headings || []).filter((heading) => heading.level <= 4 && heading.id);
    const visibleHeadings = headings.filter((heading) => heading.level !== 1);
    const entries = visibleHeadings.length ? visibleHeadings : headings;
    entries.forEach((heading) => {
      const link = node('a', 'toc-link', heading.title);
      link.href = routeHref(page.id, heading.id);
      link.dataset.headingId = heading.id;
      link.dataset.level = String(heading.level);
      ui.tocLinks.append(link);
    });
    if (!entries.length) ui.tocLinks.append(node('p', 'muted', '本页暂无分节'));
    ui.toc.hidden = false;
    if ('IntersectionObserver' in window) {
      headingObserver = new IntersectionObserver((changes) => {
        const visible = changes.filter((entry) => entry.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible[0]) setCurrentHeading(visible[0].target.id);
      }, { rootMargin: '-140px 0px -60% 0px', threshold: 0 });
      entries.forEach((heading) => {
        const target = document.getElementById(heading.id);
        if (target) headingObserver.observe(target);
      });
    }
  }

  function enhanceArticle(page) {
    const first = ui.article.firstElementChild;
    if (first?.tagName === 'H1') {
      ui.title.id = first.id || 'page-title';
      first.remove();
    } else {
      ui.title.id = 'page-title';
    }
    ui.article.querySelectorAll('pre.chaos-code').forEach((pre) => {
      // Tokens were rendered by the same language analyzer during the live
      // response or static export. Reading offline never calls a language API.
      window.ChaosCode?.enhance(pre, {wrap:true});
    });
    ui.article.querySelectorAll('table').forEach((table) => {
      const wrapper = node('div', 'table-wrap');
      wrapper.tabIndex = 0;
      wrapper.setAttribute('role', 'region');
      wrapper.setAttribute('aria-label', '可横向滚动的参考表格');
      table.before(wrapper);
      wrapper.append(table);
    });
    ui.article.querySelectorAll('h1[id], h2[id], h3[id], h4[id], h5[id], h6[id]').forEach((heading) => {
      const link = node('a', 'heading-anchor', '#');
      link.href = routeHref(page.id, heading.id);
      link.setAttribute('aria-label', `链接到：${heading.textContent}`);
      heading.append(link);
    });
    ui.article.querySelectorAll('a[href^="http:"] , a[href^="https:"]').forEach((link) => {
      link.rel = 'noopener noreferrer';
    });
  }

  function renderPagination(page) {
    ui.pagination.replaceChildren();
    const index = catalog.findIndex((item) => item.id === page.id);
    const previous = catalog[index - 1];
    const next = catalog[index + 1];
    if (previous) {
      const link = node('a');
      link.href = routeHref(previous.id);
      link.append(node('small', '', '← 上一篇'), node('span', '', previous.title));
      ui.pagination.append(link);
    } else {
      ui.pagination.append(node('span'));
    }
    if (next) {
      const link = node('a');
      link.href = routeHref(next.id);
      link.append(node('small', '', '下一篇 →'), node('span', '', next.title));
      ui.pagination.append(link);
    }
  }

  function scrollToRoute(heading, focus) {
    const target = heading ? document.getElementById(heading) : ui.title;
    if (target) {
      if (heading) {
        target.scrollIntoView({ behavior: 'auto', block: 'start' });
        setCurrentHeading(heading);
      } else {
        window.scrollTo({ top: 0, behavior: 'auto' });
      }
      if (focus) {
        if (!target.hasAttribute('tabindex')) target.tabIndex = -1;
        target.focus({ preventScroll: true });
      }
    } else {
      window.scrollTo({ top: 0, behavior: 'auto' });
      toast('本节标题已变更，已打开对应文档。');
    }
  }

  async function renderRoute() {
    if (!catalog.length) return;
    const route = readRoute();
    const metadata = catalog.find((page) => page.id === route.page);
    const sequence = ++requestNumber;
    requestController?.abort();
    clearSearch();
    setMenu(false);
    if (!metadata) {
      currentPage = null;
      currentPageId = '';
      ui.title.textContent = '找不到这篇文档';
      ui.description.textContent = '链接可能已变更，请从左侧目录选择一篇文档。';
      document.title = '文档未找到 · Chaos 文档';
      markNavigation('');
      renderError('这篇文档不在当前版本的目录中。');
      return;
    }
    if (currentPage && currentPageId === metadata.id) {
      scrollToRoute(route.heading, initialized);
      return;
    }
    const shouldFocus = initialized;
    currentPage = null;
    currentPageId = '';
    headingObserver?.disconnect();
    markNavigation(metadata.id);
    ui.title.textContent = metadata.title;
    ui.description.textContent = metadata.description || '';
    ui.section.textContent = metadata.section || '文档';
    ui.status.className = 'loading-message';
    ui.status.textContent = '正在载入正文…';
    ui.status.hidden = false;
    ui.article.replaceChildren();
    ui.footer.hidden = true;
    ui.toc.hidden = true;
    ui.copyLink.disabled = true;
    try {
      let page = pageCache.get(metadata.id);
      if (!page) {
        if (offline) throw new Error('Missing offline page');
        requestController = new AbortController();
        page = await fetchJSON(`/api/docs/${encodeURIComponent(metadata.id)}`, requestController.signal);
        if (typeof page.html !== 'string') throw new Error('Invalid page response');
        pageCache.set(metadata.id, page);
      }
      if (sequence !== requestNumber) return;
      currentPage = { ...metadata, ...page };
      currentPageId = metadata.id;
      ui.title.textContent = currentPage.title;
      ui.description.textContent = currentPage.description || '';
      ui.section.textContent = currentPage.section || '文档';
      document.title = `${currentPage.title} · Chaos 文档${version ? ` · ${version}` : ''}`;
      // HTML is rendered and sanitized by the documentation service (raw Markdown HTML is disabled).
      ui.article.innerHTML = currentPage.html;
      enhanceArticle(currentPage);
      renderTOC(currentPage);
      renderPagination(currentPage);
      byId('source-note').textContent = [metadata.path ? `来源 ${metadata.path}` : '', version ? `Chaos ${version}` : '', offline ? '离线副本' : '随软件版本发布'].filter(Boolean).join(' · ');
      ui.footer.hidden = false;
      ui.status.hidden = true;
      ui.copyLink.disabled = false;
      initialized = true;
      if (ui.search.value.trim()) searchDocs();
      if (sequence === requestNumber && !ui.search.value.trim()) scrollToRoute(route.heading, shouldFocus);
    } catch (error) {
      if (sequence !== requestNumber || error.name === 'AbortError') return;
      currentPage = null;
      currentPageId = '';
      renderError(offline ? '离线文档内容缺失，请重新导出文档。' : '文档暂时无法载入，请确认 Chaos 服务正在运行。', renderRoute);
    }
  }

  function highlight(text, query) {
    const fragment = document.createDocumentFragment();
    const terms = query.toLocaleLowerCase().split(/\s+/).filter(Boolean);
    const normalized = text.toLocaleLowerCase();
    let position = 0;
    while (position < text.length) {
      let next = -1;
      let length = 0;
      terms.forEach((term) => {
        const index = normalized.indexOf(term, position);
        if (index >= 0 && (next < 0 || index < next)) { next = index; length = term.length; }
      });
      if (next < 0) { fragment.append(document.createTextNode(text.slice(position))); break; }
      fragment.append(document.createTextNode(text.slice(position, next)), node('mark', '', text.slice(next, next + length)));
      position = next + length;
    }
    return fragment;
  }

  function searchDocs() {
    const query = ui.search.value.trim();
    if (!query) { clearSearch(); return; }
    const enteringSearch = ui.searchResults.hidden;
    setSearchVisibility(true);
    if (enteringSearch) window.scrollTo({ top: 0, behavior: 'auto' });
    document.title = `搜索：${query} · Chaos 文档`;
    const terms = query.toLocaleLowerCase().split(/\s+/).filter(Boolean);
    const matches = catalog.map((page, index) => {
      const title = String(page.title || '').toLocaleLowerCase();
      const content = [page.title, page.description, page.section, page.searchText || page.text || '', ...(page.headings || []).map((heading) => heading.title)].join(' ').toLocaleLowerCase();
      const score = terms.every((term) => content.includes(term)) ? 1 + terms.reduce((sum, term) => sum + (title.includes(term) ? 10 : 0), 0) : 0;
      return { page, score, index };
    }).filter((match) => match.score).sort((a, b) => b.score - a.score || a.index - b.index);
    ui.searchSummary.textContent = `“${query}” · 找到 ${matches.length} 篇文档`;
    ui.searchList.replaceChildren();
    matches.forEach(({ page }) => {
      const result = node('a', 'search-result');
      result.href = routeHref(page.id);
      result.append(node('div', 'result-section', page.section || '文档'));
      const title = node('h2');
      title.append(highlight(page.title, query));
      const source = String(page.searchText || page.text || page.description || '').replace(/\s+/g, ' ');
      const lower = source.toLocaleLowerCase();
      const positions = terms.map((term) => lower.indexOf(term)).filter((index) => index >= 0);
      const matchAt = positions.length ? Math.min(...positions) : 0;
      const start = Math.max(0, matchAt - 45);
      const excerpt = (start ? '…' : '') + source.slice(start, start + 190) + (source.length > start + 190 ? '…' : '');
      const description = node('p');
      description.append(highlight(excerpt, query));
      result.append(title, description);
      ui.searchList.append(result);
    });
    if (!matches.length) {
      const empty = node('div', 'empty-search');
      empty.append(node('strong', '', '没有找到匹配内容'), node('span', '', '试试更短的关键词，例如“安装”“agent”或“运行”。多个关键词请用空格分隔。'));
      ui.searchList.append(empty);
    }
  }

  async function initialize() {
    try {
      const bootstrap = byId('docs-bootstrap');
      const data = bootstrap ? JSON.parse(bootstrap.textContent) : await fetchJSON('/api/docs');
      if (!Array.isArray(data.pages) || !data.pages.length) throw new Error('Empty documentation catalog');
      catalog = data.pages;
      version = String(data.version || '');
      offline = Boolean(bootstrap);
      if (offline) catalog.forEach((page) => { if (typeof page.html === 'string') pageCache.set(page.id, page); });
      ui.version.textContent = version || '当前版本';
      byId('language-version').textContent = `语言版本 ${data.languageVersion || '2'} · ${offline ? '离线可读' : '随软件发布'}`;
      byId('offline-label').hidden = !offline;
      document.querySelectorAll('[data-app-link]').forEach((link) => { link.hidden = offline; });
      renderNavigation();
      await renderRoute();
    } catch {
      ui.title.textContent = '文档暂时无法载入';
      ui.description.textContent = '目录与正文随 Chaos 一起发布。';
      ui.nav.replaceChildren(node('p', 'muted loading-nav', '目录尚未载入'));
      renderError('请确认服务正在运行，或重新导出完整的离线文档。', initialize);
    }
  }

  ui.search.addEventListener('focus', () => { if (document.body.classList.contains('nav-open')) setMenu(false); });
  ui.search.addEventListener('input', searchDocs);
  ui.search.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown' || (event.key === 'Enter' && ui.search.value.trim())) {
      const first = ui.searchList.querySelector('a');
      if (first) { event.preventDefault(); first.focus(); }
    }
  });
  byId('clear-search').addEventListener('click', () => { clearSearch(); ui.title.focus({ preventScroll: true }); });
  ui.copyLink.addEventListener('click', async () => {
    const url = new URL(location.href);
    url.hash = routeHref(currentPageId, readRoute().heading);
    if (await copyText(url.href)) toast('文档链接已复制');
  });
  ui.menu.addEventListener('click', () => setMenu(!document.body.classList.contains('nav-open')));
  ui.backdrop.addEventListener('click', () => setMenu(false, true));
  mobile.addEventListener('change', () => setMenu(false));
  byId('back-to-top').addEventListener('click', (event) => {
    event.preventDefault();
    if (currentPageId) {
      const route = routeHref(currentPageId);
      if (location.hash === route) scrollToRoute('', true);
      else location.hash = route;
    }
  });
  document.querySelector('.skip-link').addEventListener('click', (event) => {
    event.preventDefault();
    ui.main.focus();
    ui.main.scrollIntoView({ behavior: 'auto' });
  });
  document.addEventListener('click', (event) => {
    const link = event.target.closest('a[href^="#/"]');
    if (link && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey && event.button === 0) {
      if (link.hash === location.hash) { event.preventDefault(); renderRoute(); }
    }
  });
  document.addEventListener('keydown', (event) => {
    const typing = event.target.matches('input, textarea, select, [contenteditable="true"]');
    if (event.key === '/' && !typing && !event.metaKey && !event.ctrlKey && !event.altKey) {
      event.preventDefault();
      setMenu(false);
      ui.search.focus();
    }
    if (event.key === 'Escape') {
      if (document.body.classList.contains('nav-open')) setMenu(false, true);
      else if (ui.search.value) { clearSearch(); ui.search.focus(); }
    }
    if (event.key === 'Tab' && document.body.classList.contains('nav-open')) {
      const focusable = [...ui.sidebar.querySelectorAll('a, button, input')];
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && (document.activeElement === first || document.activeElement === ui.menu)) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || document.activeElement === ui.menu)) { event.preventDefault(); first?.focus(); }
    }
  });
  window.addEventListener('hashchange', () => {
    if (!location.hash || location.hash.startsWith('#/')) renderRoute();
  });
  setMenu(false);
  initialize();
})();
