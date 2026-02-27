import React, { useEffect, useState } from 'react';
import { Network, ChevronDown, ChevronRight, ArrowRight, AlertTriangle, CheckCircle, Clock } from 'lucide-react';
import { api } from '../services/api';

interface SwarmAgent {
  session_id: string;
  agent_name: string;
  swarm_order: number | null;
  overall_quality: string;
  efficiency_score: number | null;
  security_score: number | null;
  task: string;
  status: string;
  total_steps: number;
}

interface Swarm {
  swarm_id: string;
  agent_count: number;
  overall_quality: string;
  drift_score: number;   // 0.0–1.0 (1.0 = no drift)
  started_at: string;
  ended_at: string;
  agents: SwarmAgent[];
}

const qualityColor = (q: string) => {
  switch (q) {
    case 'EXCELLENT': return 'text-emerald-400 bg-emerald-950/40 border-emerald-900/50';
    case 'GOOD':      return 'text-blue-400 bg-blue-950/40 border-blue-900/50';
    case 'POOR':      return 'text-yellow-400 bg-yellow-950/40 border-yellow-900/50';
    case 'FAILED':    return 'text-red-400 bg-red-950/40 border-red-900/50';
    case 'STUCK':     return 'text-orange-400 bg-orange-950/40 border-orange-900/50';
    default:          return 'text-gray-400 bg-gray-900/40 border-gray-800/50';
  }
};

const driftLabel = (score: number) => {
  const pct = Math.round(score * 100);
  if (pct >= 80) return { label: 'Aligned', color: 'text-emerald-400' };
  if (pct >= 50) return { label: 'Slight Drift', color: 'text-yellow-400' };
  return { label: 'High Drift', color: 'text-red-400' };
};

const SwarmCard: React.FC<{ swarm: Swarm }> = ({ swarm }) => {
  const [expanded, setExpanded] = useState(false);
  const drift = driftLabel(swarm.drift_score);
  const driftPct = Math.round(swarm.drift_score * 100);

  return (
    <div className="bg-dark-surface border border-dark-border rounded-xl overflow-hidden">
      {/* Header */}
      <button
        className="w-full flex items-center gap-4 px-5 py-4 hover:bg-dark-hover transition-colors text-left"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="w-9 h-9 rounded-lg bg-phantom-950/40 border border-phantom-900/40 flex items-center justify-center flex-shrink-0">
          <Network size={18} className="text-phantom-400" />
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-0.5">
            <span className="text-sm font-semibold text-gray-100 truncate">{swarm.swarm_id}</span>
            <span className={`text-xs font-medium px-2 py-0.5 rounded-full border ${qualityColor(swarm.overall_quality)}`}>
              {swarm.overall_quality}
            </span>
          </div>
          <div className="flex items-center gap-3 text-xs text-gray-500">
            <span>{swarm.agent_count} agents</span>
            <span>·</span>
            <span className={drift.color}>{drift.label} ({driftPct}%)</span>
            {swarm.started_at && (
              <>
                <span>·</span>
                <span>{new Date(swarm.started_at).toLocaleString()}</span>
              </>
            )}
          </div>
        </div>

        {/* Drift bar */}
        <div className="flex-shrink-0 w-24 hidden sm:block">
          <div className="flex items-center justify-between mb-1">
            <span className="text-xs text-gray-500">Alignment</span>
            <span className={`text-xs font-medium ${drift.color}`}>{driftPct}%</span>
          </div>
          <div className="w-full bg-dark-bg rounded-full h-1.5">
            <div
              className={`h-1.5 rounded-full transition-all ${driftPct >= 80 ? 'bg-emerald-500' : driftPct >= 50 ? 'bg-yellow-500' : 'bg-red-500'}`}
              style={{ width: `${driftPct}%` }}
            />
          </div>
        </div>

        {expanded ? <ChevronDown size={16} className="text-gray-500 flex-shrink-0" /> : <ChevronRight size={16} className="text-gray-500 flex-shrink-0" />}
      </button>

      {/* Agent Pipeline */}
      {expanded && (
        <div className="border-t border-dark-border px-5 py-4 space-y-3">
          <div className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">
            Agent Pipeline
          </div>
          <div className="flex flex-col gap-2">
            {swarm.agents.map((agent, idx) => (
              <div key={agent.session_id} className="flex items-start gap-3">
                {/* Step number + arrow */}
                <div className="flex flex-col items-center gap-1 flex-shrink-0 pt-1">
                  <div className="w-6 h-6 rounded-full bg-phantom-950/60 border border-phantom-900/50 flex items-center justify-center">
                    <span className="text-xs font-bold text-phantom-300">{agent.swarm_order ?? idx + 1}</span>
                  </div>
                  {idx < swarm.agents.length - 1 && (
                    <div className="flex flex-col items-center gap-0.5">
                      <div className="w-px h-4 bg-phantom-900/40" />
                      <ArrowRight size={10} className="text-phantom-700 -rotate-90" />
                    </div>
                  )}
                </div>

                {/* Agent card */}
                <div className="flex-1 bg-dark-bg border border-dark-border rounded-lg px-4 py-3 mb-1">
                  <div className="flex items-center justify-between gap-3 mb-1">
                    <span className="text-sm font-medium text-gray-200">{agent.agent_name}</span>
                    <div className="flex items-center gap-2 flex-shrink-0">
                      <span className={`text-xs font-medium px-2 py-0.5 rounded-full border ${qualityColor(agent.overall_quality)}`}>
                        {agent.overall_quality}
                      </span>
                      {agent.status === 'running' && (
                        <span className="flex items-center gap-1 text-xs text-phantom-400">
                          <span className="animate-ping w-1.5 h-1.5 rounded-full bg-phantom-400 inline-block" />
                          Running
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Task */}
                  {agent.task && (
                    <p className="text-xs text-gray-400 mb-2 line-clamp-2">{agent.task}</p>
                  )}

                  {/* Scores */}
                  <div className="flex items-center gap-4 text-xs text-gray-500">
                    <span>{agent.total_steps} steps</span>
                    {agent.efficiency_score != null && (
                      <span className="flex items-center gap-1">
                        <CheckCircle size={11} className="text-blue-400" />
                        Eff {agent.efficiency_score}%
                      </span>
                    )}
                    {agent.security_score != null && (
                      <span className={`flex items-center gap-1 ${agent.security_score < 70 ? 'text-red-400' : 'text-gray-500'}`}>
                        {agent.security_score < 70 && <AlertTriangle size={11} />}
                        Sec {agent.security_score}%
                      </span>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

export const SwarmView: React.FC = () => {
  const [swarms, setSwarms] = useState<Swarm[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadSwarms = async () => {
    try {
      const data = await api.getSwarms();
      setSwarms(data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load swarms');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadSwarms();
    const interval = setInterval(loadSwarms, 10000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-gray-100">Swarm Monitor</h1>
          <p className="text-sm text-gray-500 mt-1">
            Multi-agent pipelines — inter-agent alignment and collective drift
          </p>
        </div>
        <button
          onClick={loadSwarms}
          className="flex items-center gap-2 px-3 py-1.5 text-xs text-gray-400 hover:text-gray-200 bg-dark-surface border border-dark-border rounded-lg transition-colors"
        >
          <Clock size={13} />
          Refresh
        </button>
      </div>

      {/* Drift explanation */}
      <div className="bg-dark-surface border border-dark-border rounded-xl p-4 text-xs text-gray-400 space-y-1">
        <p className="font-medium text-gray-300">What is Alignment Score?</p>
        <p>
          Measures how closely each agent's task aligns with the first agent's intent in the swarm.
          100% = all agents work toward the same goal · &lt;50% = significant topic drift.
        </p>
      </div>

      {/* Content */}
      {loading ? (
        <div className="flex items-center justify-center py-16 text-gray-500 text-sm">
          Loading swarms...
        </div>
      ) : error ? (
        <div className="bg-red-950/20 border border-red-900/30 rounded-xl p-6 text-center">
          <p className="text-red-400 text-sm">{error}</p>
        </div>
      ) : swarms.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <div className="w-16 h-16 rounded-2xl bg-dark-surface border border-dark-border flex items-center justify-center mb-4">
            <Network size={28} className="text-gray-600" />
          </div>
          <h3 className="text-base font-medium text-gray-400 mb-2">No swarms yet</h3>
          <p className="text-sm text-gray-600 max-w-sm">
            Use <code className="bg-dark-surface px-1.5 py-0.5 rounded text-phantom-300">swarm_id</code> in{' '}
            <code className="bg-dark-surface px-1.5 py-0.5 rounded text-phantom-300">NornHook</code> to group
            multiple agents into a monitored pipeline.
          </p>
          <pre className="mt-4 text-left text-xs bg-dark-surface border border-dark-border rounded-lg p-4 text-gray-400 max-w-sm">
{`hook = NornHook(
  swarm_id="my-pipeline",
  swarm_order=1,
  agent_name="Researcher"
)`}
          </pre>
        </div>
      ) : (
        <div className="space-y-3">
          {swarms.map(swarm => (
            <SwarmCard key={swarm.swarm_id} swarm={swarm} />
          ))}
        </div>
      )}
    </div>
  );
};
