/** Pencerenin üst çubuğu: sayfa başlığı solda, kontroller sağda. Boş alanlardan pencere sürüklenebilir. */
export function Toolbar({ title, subtitle, lead, children }: { title: string; subtitle?: string; lead?: React.ReactNode; children?: React.ReactNode }) {
  return (
    <header className="toolbar" data-tauri-drag-region>
      {lead}
      <div style={{ minWidth: 0 }} data-tauri-drag-region>
        <div className="title ellipsis" data-tauri-drag-region>{title}</div>
        {subtitle && <div className="subtitle ellipsis" data-tauri-drag-region>{subtitle}</div>}
      </div>
      <div className="spacer" data-tauri-drag-region />
      {children}
    </header>
  );
}
