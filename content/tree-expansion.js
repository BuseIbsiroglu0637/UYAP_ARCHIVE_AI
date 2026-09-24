(() => {
  // jQuery treeview may retain closed-hitarea after opening. Read the child UL.
  async function expandTree({root, visible, style, sleep, now = Date.now, timeoutMs = 120000, waitMs = 200, quietMs = 1500}) {
    const attempted = new WeakSet();
    const started = now();
    let clicks = 0;
    let settledSince = null;
    let previousCount = -1;
    while (now() - started < timeoutMs) {
      const toggles = [...root.querySelectorAll('div.hitarea')].filter(t => visible(t.parentElement));
      let pending = 0;
      for (const toggle of toggles) {
        if (!toggle.isConnected || !visible(toggle.parentElement)) continue;
        const list = [...toggle.parentElement.children].find(e => e.tagName === 'UL');
        const closed = list ? style(list).display === 'none' : true;
        if (!closed) continue;
        pending++;
        if (!attempted.has(toggle)) {
          attempted.add(toggle);
          toggle.click();
          clicks++;
        }
      }
      const count = root.querySelectorAll('span.file[evrak_id]').length;
      const loading = [...root.querySelectorAll('[aria-busy="true"], .tree-loading, .loading')].filter(visible).length;
      if (pending || loading || count !== previousCount) settledSince = null;
      else settledSince ??= now();
      if (settledSince !== null && now() - settledSince >= quietMs) return {openedBranches: clicks, settled: true};
      previousCount = count;
      await sleep(waitMs);
    }
    throw new Error('Klasör açılması doğrulanamadı. Eksiksizlik doğrulanamadığı için indirme başlatılmadı; açık dava ekranını kontrol edin.');
  }
  globalThis.UYAP_EXPAND_TREE = expandTree;
})();
