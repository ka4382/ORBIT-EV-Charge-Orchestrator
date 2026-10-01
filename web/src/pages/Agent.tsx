import { useState } from 'react';
import { ApiError } from '../api/client';
import { useAgentDecision, useAgentQuery, useAudit } from '../api/hooks';
import type { AgentAction, AgentAnswer } from '../api/types';
import { Glyph } from '../components/Glyph';
import { useToast } from '../components/Toast';
import { fmtDateTime } from '../lib/format';
import './Agent.css';

interface Turn {
  id: number;
  question: string;
  answer?: AgentAnswer;
  error?: string;
}

const suggestions = [
  'Which vehicles will not make their 7am departure?',
  'Move tonight’s charging into the off-peak window',
  'Why did cost spike yesterday?',
];

export function Agent() {
  const [q, setQ] = useState('');
  const [turns, setTurns] = useState<Turn[]>([]);
  const [offline, setOffline] = useState(false);
  const query = useAgentQuery();
  const decide = useAgentDecision();
  const audit = useAudit();
  const { toast } = useToast();

  const ask = (question: string) => {
    if (!question.trim() || query.isPending) return;
    const id = Date.now();
    setTurns((t) => [...t, { id, question }]);
    setQ('');
    query.mutate(question, {
      onSuccess: (a) => {
        setTurns((t) => t.map((x) => (x.id === id ? { ...x, answer: a } : x)));
        void audit.refetch();
      },
      onError: (e) => {
        if (e instanceof ApiError && e.isNotImplemented) {
          setOffline(true);
          setTurns((t) => t.filter((x) => x.id !== id));
          return;
        }
        setTurns((t) => t.map((x) => (x.id === id ? { ...x, error: e.message } : x)));
      },
    });
  };

  const onDecide = (action: AgentAction, decision: 'approve' | 'reject') =>
    decide.mutate(
      { id: action.id, decision },
      {
        onSuccess: (r) => {
          setTurns((t) =>
            t.map((x) =>
              x.answer
                ? {
                    ...x,
                    answer: {
                      ...x.answer,
                      actions: x.answer.actions.map((a) =>
                        a.id === r.id ? { ...a, status: r.execution?.status ?? r.status } : a,
                      ),
                    },
                  }
                : x,
            ),
          );
          toast(
            r.execution
              ? `${action.action} ${r.execution.status.toLowerCase()} · ${r.execution.plan_ids.length} plan(s) created`
              : `${action.action} ${r.status.toLowerCase()}`,
          );
          void audit.refetch();
        },
        onError: (e) => toast(`Failed: ${e.message}`, 'error'),
      },
    );

  if (offline) {
    return (
      <div className="page">
        <div className="page-title">
          <h1 className="display">Agent</h1>
        </div>
        <div className="panel empty agent-offline">
          <Glyph name="toggle-spark" size={72} />
          <h2 className="display" style={{ fontSize: 40, color: 'var(--fg-strong)' }}>
            Agent coming online
          </h2>
          <p>
            The agent endpoint is not reachable right now. Answers resume as soon as the API is
            back.
          </p>
          <button type="button" className="btn btn-secondary" onClick={() => setOffline(false)}>
            Try again
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="page agent">
      <div className="page-title">
        <div>
          <div className="label">Human-approved actions · audited</div>
          <h1 className="display">Agent</h1>
        </div>
      </div>

      <div className="agent-grid">
        <div className="panel agent-chat">
          <div className="agent-thread">
            {turns.length === 0 && (
              <div className="agent-intro">
                <Glyph name="converge" size={48} />
                <p>
                  Ask about departures, stranding risk, tonight’s schedule or costs. Actions it
                  proposes need your approval.
                </p>
                <div className="agent-suggest">
                  {suggestions.map((s) => (
                    <button
                      type="button"
                      key={s}
                      className="btn btn-secondary btn-sm"
                      onClick={() => ask(s)}
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {turns.map((t) => (
              <div key={t.id} className="agent-turn">
                <div className="agent-q">
                  <span className="label">You</span>
                  <p>{t.question}</p>
                </div>
                <div className="agent-a">
                  <span className="label">ORBIT</span>
                  {t.error ? (
                    <p className="agent-error">{t.error}</p>
                  ) : t.answer ? (
                    <>
                      <p className="agent-answer-text">{t.answer.answer}</p>
                      {t.answer.mode && (
                        <p className="agent-meta mono">
                          {t.answer.mode === 'llm' ? 'LLM' : 'offline'} ·{' '}
                          {t.answer.usage?.tool_calls ?? 0} tool calls ·{' '}
                          {Math.round(t.answer.usage?.latency_ms ?? 0)} ms
                          {t.answer.usage?.cost_usd
                            ? ` · $${t.answer.usage.cost_usd.toFixed(4)}`
                            : ''}
                          {t.answer.usage?.fallback_reason
                            ? ` · fell back: ${t.answer.usage.fallback_reason}`
                            : ''}
                        </p>
                      )}
                      {t.answer.actions.length > 0 && (
                        <div className="agent-actions">
                          {t.answer.actions.map((a) => (
                            <div
                              key={String(a.id)}
                              className="agent-action panel"
                              data-status={a.status}
                            >
                              <div className="agent-action-head">
                                <strong className="mono">{a.action}</strong>
                                <span
                                  className="chip"
                                  data-status={
                                    a.status === 'APPROVED' || a.status === 'EXECUTED'
                                      ? 'DRIVING'
                                      : a.status === 'REJECTED' || a.status === 'FAILED'
                                        ? 'AT_RISK'
                                        : 'OFFLINE'
                                  }
                                >
                                  {a.status}
                                </span>
                              </div>
                              <dl className="agent-params">
                                {Object.entries(a.params).map(([k, v]) => (
                                  <div key={k}>
                                    <dt>{k}</dt>
                                    <dd className="mono">
                                      {Array.isArray(v) ? v.join(', ') : String(v)}
                                    </dd>
                                  </div>
                                ))}
                              </dl>
                              <p className="agent-rationale">{a.rationale}</p>
                              {a.status === 'PENDING' && (
                                <div className="agent-action-btns">
                                  <button
                                    type="button"
                                    className="btn btn-primary btn-sm"
                                    onClick={() => onDecide(a, 'approve')}
                                    disabled={decide.isPending}
                                  >
                                    Approve
                                  </button>
                                  <button
                                    type="button"
                                    className="btn btn-secondary btn-sm"
                                    onClick={() => onDecide(a, 'reject')}
                                    disabled={decide.isPending}
                                  >
                                    Reject
                                  </button>
                                </div>
                              )}
                            </div>
                          ))}
                        </div>
                      )}
                      <small className="vd-muted">audit #{String(t.answer.audit_id)}</small>
                    </>
                  ) : (
                    <p className="vd-muted">Thinking…</p>
                  )}
                </div>
              </div>
            ))}
          </div>
          <form
            className="agent-input"
            onSubmit={(e) => {
              e.preventDefault();
              ask(q);
            }}
          >
            <input
              className="input"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Ask the fleet…"
              aria-label="Question"
            />
            <button
              type="submit"
              className="btn btn-primary"
              disabled={query.isPending || !q.trim()}
            >
              {query.isPending ? '…' : 'Ask'}
            </button>
          </form>
        </div>

        <aside className="panel agent-audit">
          <div className="panel-head">
            <h2>Audit trail</h2>
          </div>
          {audit.isError ? (
            <div className="empty">
              <p>
                {audit.error instanceof ApiError && audit.error.status === 403
                  ? 'Audit log needs the admin or auditor role.'
                  : audit.error.message}
              </p>
            </div>
          ) : audit.data?.items.length ? (
            <ol className="audit-list">
              {audit.data.items.map((r) => (
                <li key={String(r.id)}>
                  <span className="mono audit-ts">{fmtDateTime(r.ts)}</span>
                  <span className="audit-action">
                    <strong>{r.action}</strong> · {r.actor_id}
                    {r.resource ? ` · ${r.resource}` : ''}
                  </span>
                  {r.details && (
                    <span className="audit-detail mono">
                      {typeof r.details === 'string' ? r.details : JSON.stringify(r.details)}
                    </span>
                  )}
                </li>
              ))}
            </ol>
          ) : (
            <div className="empty">
              <p>{audit.isLoading ? 'Loading…' : 'No audit rows yet.'}</p>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
