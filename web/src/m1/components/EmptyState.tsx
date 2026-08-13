import React from 'react';

export function EmptyState({title, detail}: {title: string; detail?: string}) {
  return (
    <div className="m1Empty" role="status">
      <h3>{title}</h3>
      {detail ? <p>{detail}</p> : null}
    </div>
  );
}

export function ErrorState({title, detail}: {title: string; detail: string}) {
  return (
    <div className="m1ErrorBox" role="alert">
      <h3>{title}</h3>
      <p>{detail}</p>
    </div>
  );
}

export function LoadingState({label}: {label: string}) {
  return (
    <p className="m1Loading" role="status">
      {label}
    </p>
  );
}

export function HashValue({value, label}: {value: string | null | undefined; label: string}) {
  const full = value ?? '';
  const short =
    full.length > 16 ? `${full.slice(0, 12)}…${full.slice(-4)}` : full || '不可用';

  async function copyHash() {
    if (!full) {
      return;
    }
    try {
      await navigator.clipboard.writeText(full);
    } catch {
      // 剪贴板不可用时静默失败，仍可通过 title 查看全文
    }
  }

  return (
    <div className="m1HashRow">
      <span>{label}</span>
      <code title={full || undefined}>{short}</code>
      <button type="button" className="secondary m1SmallBtn" onClick={copyHash} disabled={!full}>
        复制全文
      </button>
    </div>
  );
}
