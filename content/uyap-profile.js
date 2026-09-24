/*
 * UYAP ekran sürümleri kurum ve kullanıcı rolüne göre değişebilir.
 * Gerçek ekrandaki seçiciler doğrulandıktan sonra yalnızca bu dosya güncellenmelidir.
 */
globalThis.UYAP_ARCHIVE_PROFILE = Object.freeze({
  treeRootSelectors: [
    "ul.treeview",
    "li.collapsable",
    ".x-tree-root-node",
    ".x-tree-root-ct",
    ".x-tree-node-ct",
    "[role='tree']"
  ],
  treeNodeSelectors: [
    "span.file[evrak_id]",
    "div.x-tree-node-el",
    ".x-tree-node-leaf",
    "[role='treeitem']"
  ],
  treeLabelSelectors: [
    "span.file",
    "span.folder",
    ".x-tree-node-anchor",
    ".x-tree-node-text",
    "a span",
    "a",
    "span"
  ],
  collapsedToggleSelectors: [
    "div.hitarea.closed-hitarea",
    "div.hitarea.expandable-hitarea",
    "li.closed.expandable > div.hitarea",
    ".x-tree-elbow-plus",
    ".x-tree-elbow-end-plus",
    ".x-tree-ec-icon.x-tree-elbow-plus",
    ".x-tree-ec-icon.x-tree-elbow-end-plus",
    "[role='treeitem'][aria-expanded='false']"
  ],
  rowSelectors: [
    "span.file[evrak_id]",
    "[data-evrak-id]",
    "[data-document-id]",
    "tr.x-grid-row",
    "[role='row']",
    "div.x-tree-node-el",
    ".x-tree-node-leaf"
  ],
  downloadSelectors: [
    "span.file[evrak_id]",
    "[data-action='download']",
    "button[title*='İndir' i]",
    "a[title*='İndir' i]",
    "button[aria-label*='İndir' i]",
    "a[href*='download' i]",
    "a[href*='indir' i]"
  ],
  titleSelectors: [
    "[data-field='evrakAdi']",
    ".evrak-adi",
    "[aria-label*='Evrak Adı' i]"
  ],
  dateSelectors: [
    "[data-field='evrakTarihi']",
    ".evrak-tarihi",
    "time"
  ],
  folderSelectors: ["[data-folder-path]", "[aria-label*='Klasör' i]"],
  exactFileSelector: "span.file[evrak_id]",
  exactFolderSelector: "span.folder",
  exactToggleSelector: "div.hitarea.expandable-hitarea, div.hitarea.closed-hitarea",
  saveMenuSelector: ".context-menu-item-inner",
  courtSelectors: ["[data-court-name]", ".mahkeme-adi", "h1", "h2"],
  caseSelectors: ["[data-case-id]", ".esas-no", "[aria-label*='Esas' i]"],
  expandPassLimit: 80,
  expansionWaitMs: 350,
  settlePasses: 2
});
