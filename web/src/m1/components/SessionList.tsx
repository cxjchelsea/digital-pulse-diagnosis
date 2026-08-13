import React from 'react';
import type {SessionSummary} from '../types';

export function SessionList({
  sessions,
  selectedSessionId,
  onSelect,
  loading,
  errorText,
}: {
  sessions: SessionSummary[];
  selectedSessionId: string | null;
  onSelect: (sessionId: string) => void;
  loading: boolean;
  errorText: string | null;
}) {
  if (loading) {
    return <p role="status">正在加载会话列表…</p>;
  }
  if (errorText) {
    return (
      <div className="m1ErrorBox" role="alert">
        <h3>会话列表加载失败</h3>
        <p>{errorText}</p>
      </div>
    );
  }
  if (sessions.length === 0) {
    return (
      <div className="m1Empty" role="status" data-testid="m1-session-list-empty">
        <h3>暂无会话</h3>
        <p>后端返回空列表，并非请求失败。</p>
      </div>
    );
  }

  return (
    <div className="m1SessionList" data-testid="m1-session-list">
      {sessions.map((session) => {
        const selected = session.session_id === selectedSessionId;
        const degraded = !session.app_registered;
        return (
          <button
            type="button"
            key={session.session_id}
            className={`m1SessionCard${selected ? ' selected' : ''}${degraded ? ' degraded' : ''}`}
            aria-pressed={selected}
            onClick={() => onSelect(session.session_id)}
          >
            <strong>{session.session_id}</strong>
            <span>来源：{session.source_type}</span>
            <span>
              状态：{session.completed ? '已完成' : '未完成'}
              {session.completion_reason ? ` · ${session.completion_reason}` : ''}
            </span>
            <span>原始持久化：{session.raw_persistence_status}</span>
            <span>
              APP 注册：{session.app_registered ? '是' : '否（降级条目）'} · 已提交 Run：
              {session.committed_run_count}
            </span>
            <span>当前 Run：{session.current_run_id ?? '无'}</span>
          </button>
        );
      })}
    </div>
  );
}
