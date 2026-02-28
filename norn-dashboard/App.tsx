import React, { useState, useEffect } from 'react';
import { Sidebar } from './components/Sidebar';
import { Dashboard } from './components/Dashboard';
import { SessionDetail } from './components/SessionDetail';
import { AgentDetail } from './components/AgentDetail';
import { AddAgent } from './components/AddAgent';
import { AuditLogView } from './components/AuditLogView';
import { ConfigView } from './components/ConfigView';
import { BrowserAuditView } from './components/BrowserAuditView';
import { SwarmView } from './components/SwarmView';
import { api, SessionData } from './services/api';
import { Session, QualityLevel, IssueType, AgentStep, SessionIssueDetail } from './types';

const App: React.FC = () => {
  const [currentView, setCurrentView] = useState('dashboard');
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [activeRunSessionId, setActiveRunSessionId] = useState<string | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [agents, setAgents] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0); // Force refresh trigger
  const [useWebSocket, setUseWebSocket] = useState(true); // Toggle WebSocket vs polling
  const [systemStatus, setSystemStatus] = useState<'online' | 'offline' | 'checking'>('checking');

  // Load sessions from API
  useEffect(() => {
    if (useWebSocket) {
      // Use WebSocket for real-time updates
      api.connectWebSocket(
        (data) => {
          if (data.type === 'initial' || data.type === 'update') {
            console.log(`[Norn] WS ${data.type}: ${data.sessions?.length} sessions, ${data.agents?.length} agents`);
            const converted = convertSessionData(data.sessions);
            setSessions(converted);
            setAgents(data.agents);
            setError(null);
            setLoading(false);
          } else if (data.type === 'session_update') {
            setSessions(prev => {
              const converted = convertSessionData([data.session]);
              const updated = prev.filter(s => s.id !== data.session.session_id);
              return [...converted, ...updated].sort((a, b) =>
                new Date(b.startTime).getTime() - new Date(a.startTime).getTime()
              );
            });
          }
        },
        () => {
          // Only called when max reconnect attempts exceeded
          console.error('WebSocket failed permanently, falling back to polling');
          setUseWebSocket(false);
        }
      );

      // Also load initial data via REST while WebSocket connects
      loadSessions();
      loadAgents();

      return () => {
        api.disconnectWebSocket();
      };
    } else {
      // Polling fallback
      loadSessions();
      loadAgents();
      const interval = setInterval(() => {
        loadSessions();
        loadAgents();
      }, 5000);
      return () => clearInterval(interval);
    }
  }, [useWebSocket]);

  // Periodic health check for system status
  useEffect(() => {
    const checkHealth = async () => {
      try {
        await api.healthCheck();
        setSystemStatus('online');
      } catch {
        setSystemStatus('offline');
      }
    };
    checkHealth();
    const interval = setInterval(checkHealth, 15000); // Check every 15 seconds
    return () => clearInterval(interval);
  }, []);

  // Refresh session data whenever a specific agent card is opened
  useEffect(() => {
    if (selectedAgentId) {
      loadSessions();
    }
  }, [selectedAgentId]);

  const loadSessions = async () => {
    try {
      const data = await api.getSessions();
      console.log('[Norn] Raw sessions:', data.length);
      const converted = convertSessionData(data);
      console.log('[Norn] Converted sessions:', converted.length, 'first:', converted[0]?.id, 'quality:', converted[0]?.overallQuality);
      setSessions(converted);
      setError(null);
    } catch (err) {
      console.error('Failed to load sessions:', err);
      setError(err instanceof Error ? err.message : 'Failed to load sessions');
    } finally {
      setLoading(false);
    }
  };

  const loadAgents = async () => {
    try {
      const data = await api.getAgents();
      setAgents(data);
    } catch (err) {
      console.error('Failed to load agents:', err);
    }
  };

  // Convert backend session data to frontend format
  const convertSessionData = (data: SessionData[]): Session[] => {
    return data.map(s => {
      const taskPreview = (typeof s.task === 'string' ? s.task : '').substring(0, 100);

      // Convert issues - extract types for quick filtering, keep details for display
      const issueDetails: SessionIssueDetail[] = (s.issues || []).map(i => ({
        issueId: i.issue_id,
        issueType: (i.issue_type || 'NONE') as IssueType,
        severity: i.severity || 5,
        description: i.description || '',
        recommendation: i.recommendation || '',
        affectedSteps: i.affected_steps,
      }));
      const issueTypes = issueDetails.map(i => i.issueType);

      // Convert steps into rich timeline entries
      const steps: AgentStep[] = [];
      let stepIdx = 0;

      for (const step of (s.steps || [])) {
        stepIdx++;
        // Handle null scores — null means "not yet evaluated"
        const secScore = step.security_score;
        const relScore = step.relevance_score;
        const riskScore = secScore != null ? Math.max(0, 100 - secScore) : null;

        // Pure-reasoning agent step (no tool calls — e.g. Thinker)
        if (step.tool_name === 'ai_reasoning') {
          steps.push({
            id: step.step_id || String(stepIdx),
            timestamp: step.timestamp,
            type: 'agent_thought',
            content: step.tool_result || '',
            metadata: { toolName: 'ai_reasoning' }
          });
        } else {
        // Tool call step
        steps.push({
          id: step.step_id || String(stepIdx),
          timestamp: step.timestamp,
          type: 'tool_call',
          content: `${step.tool_name}(${step.tool_input || ''})`,
          metadata: {
            toolName: step.tool_name,
            riskScore,
          }
        });

        // Tool result step
        if (step.tool_result) {
          steps.push({
            id: `${step.step_id || stepIdx}-result`,
            timestamp: step.timestamp,
            type: 'tool_result',
            content: step.tool_result,
            metadata: {
              toolName: step.tool_name,
            }
          });
        }
        } // end non-reasoning branch

        // Norn check step (security/relevance evaluation)
        if (step.reasoning) {
          const hasSecurityIssue = riskScore != null && riskScore > 0;
          const hasRelevanceIssue = relScore != null && relScore < 50;
          const status = step.status || 'SUCCESS';

          let checkContent = '';
          if (status === 'IRRELEVANT' || hasRelevanceIssue) {
            checkContent = `Task Drift: Relevance ${relScore != null ? relScore + '%' : 'N/A'}. ${step.reasoning}`;
          } else if (hasSecurityIssue) {
            checkContent = `Security Alert: Score ${secScore != null ? secScore + '%' : 'N/A'}. ${step.reasoning}`;
          } else if (relScore == null || secScore == null) {
            checkContent = `Evaluating: ${step.reasoning}`;
          } else {
            checkContent = `Monitor: Step OK. Relevance: ${relScore}%, Security: ${secScore}%`;
          }

          const shadowVerification = step.metadata?.shadow_verification
            ? {
                verified: step.metadata.shadow_verification.verified,
                verificationResult: step.metadata.shadow_verification.verification_result as 'VERIFIED' | 'SECURITY_CONCERN' | 'UNAVAILABLE',
                verificationMethod: step.metadata.shadow_verification.verification_method,
                securityScore: step.metadata.shadow_verification.security_score ?? null,
                securityIssues: step.metadata.shadow_verification.security_issues || [],
                details: step.metadata.shadow_verification.details || '',
                url: step.metadata.shadow_verification.url,
              }
            : undefined;

          steps.push({
            id: `${step.step_id || stepIdx}-check`,
            timestamp: step.timestamp,
            type: 'phantom_check',
            content: checkContent,
            metadata: {
              riskScore,
              shadowVerification,
            }
          });
        }
      }

      return {
        id: s.session_id,
        agentName: s.agent_name,
        model: s.model || 'Nova Lite',
        taskPreview,
        startTime: s.start_time,
        status: s.status,
        overallQuality: s.overall_quality as QualityLevel,
        efficiencyScore: s.efficiency_score,
        securityScore: s.security_score,
        issues: issueTypes,
        issueDetails,
        steps,
        aiEvaluation: s.ai_evaluation,
        toolAnalysis: (s as any).tool_analysis,
        decisionObservations: (s as any).decision_observations,
        efficiencyExplanation: (s as any).efficiency_explanation,
        recommendations: s.recommendations,
        taskCompletion: s.task_completion,
        loopDetected: s.loop_detected,
        totalExecutionTimeMs: s.total_execution_time_ms,
      };
    });
  };

  const handleAgentAdded = () => {
    // Reload sessions and agents, go back to dashboard
    loadSessions();
    loadAgents();
    setRefreshKey(prev => prev + 1); // Trigger refresh
    setCurrentView('dashboard');
  };

  const handleSessionSelect = (id: string) => {
    setSelectedSessionId(id);
    setSelectedAgentId(null);
    setCurrentView('detail');
  };

  const handleAgentSelect = (id: string) => {
    setSelectedAgentId(id);
    setSelectedSessionId(null);
    setCurrentView('detail');
  };

  const renderContent = () => {
    if (loading && sessions.length === 0) {
      return (
        <div className="flex items-center justify-center h-full">
          <div className="text-gray-400">Loading sessions...</div>
        </div>
      );
    }

    if (error && sessions.length === 0) {
      return (
        <div className="flex items-center justify-center h-full">
          <div className="bg-red-900/20 border border-red-900/30 rounded-xl p-6 text-center">
            <h3 className="text-lg font-medium text-red-400 mb-2">Connection Error</h3>
            <p className="text-sm text-gray-400">{error}</p>
            <p className="text-xs text-gray-500 mt-2">Make sure the API server is running on port 8000</p>
          </div>
        </div>
      );
    }

    switch (currentView) {
      case 'dashboard':
        return <Dashboard key={refreshKey} sessions={sessions} onSelectSession={handleSessionSelect} onSelectAgent={handleAgentSelect} />;
      case 'add_agent':
        return <AddAgent onAgentAdded={handleAgentAdded} />;
      case 'detail':
        if (selectedSessionId) {
          const session = sessions.find(s => s.id === selectedSessionId);
          if (!session) return <div>Session not found</div>;
          return <SessionDetail session={session} onBack={() => setCurrentView('dashboard')} />;
        } else if (selectedAgentId) {
          const agent = agents.find(a => a.id === selectedAgentId);
          if (!agent) return (
            <div className="flex items-center justify-center h-full">
              <div className="bg-dark-surface border border-dark-border rounded-xl p-8 text-center max-w-sm">
                <h3 className="text-lg font-medium text-white mb-2">Agent not found</h3>
                <p className="text-sm text-gray-400 mb-4">This agent may still be loading or was removed.</p>
                <button
                  onClick={() => { setCurrentView('dashboard'); setSelectedAgentId(null); setActiveRunSessionId(null); }}
                  className="px-4 py-2 bg-phantom-600 hover:bg-phantom-500 text-white text-sm rounded-lg transition-colors"
                >
                  Return to Overview
                </button>
              </div>
            </div>
          );
          const currentSession = activeRunSessionId
            ? sessions.find(s => s.id === activeRunSessionId)
            : sessions
                .filter(s => s.agentName === agent.name)
                .sort((a, b) => new Date(b.startTime).getTime() - new Date(a.startTime).getTime())[0];
          return (
            <AgentDetail
              agent={agent}
              onBack={() => { setCurrentView('dashboard'); setActiveRunSessionId(null); }}
              currentSession={currentSession}
              onSessionStart={setActiveRunSessionId}
            />
          );
        }
        return <div>No selection</div>;
      case 'swarms':
        return <SwarmView onSelectSession={handleSessionSelect} />;
      case 'browser_audit':
        return <BrowserAuditView sessions={sessions} />;
      case 'logs':
        return <AuditLogView />;
      case 'settings':
        return <ConfigView />;
      default:
        return <Dashboard sessions={sessions} onSelectSession={handleSessionSelect} />;
    }
  };

  return (
    <div className="flex h-screen bg-dark-bg text-gray-200 overflow-hidden font-sans">
      <Sidebar currentView={selectedSessionId || selectedAgentId ? 'dashboard' : currentView} onChangeView={(view) => {
        setCurrentView(view);
        setSelectedSessionId(null);
        setSelectedAgentId(null);
        setActiveRunSessionId(null);
      }} />
      
      <main className="flex-1 overflow-hidden flex flex-col">
        {/* Top Bar */}
        <header className="h-16 border-b border-dark-border flex items-center justify-between px-8 bg-dark-bg/80 backdrop-blur-sm z-10">
          <div className="flex items-center gap-2 text-sm font-medium text-white">
            <span>Workspace</span>
          </div>
          <div className="flex items-center gap-4">
            <div className={`flex items-center gap-2 px-3 py-1.5 rounded-full border ${
              systemStatus === 'online' ? 'bg-phantom-950/20 border-phantom-900/30' :
              systemStatus === 'offline' ? 'bg-red-950/20 border-red-900/30' :
              'bg-yellow-950/20 border-yellow-900/30'
            }`}>
               <span className="relative flex h-2 w-2">
                  {systemStatus === 'online' && (
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-phantom-400 opacity-75"></span>
                  )}
                  <span className={`relative inline-flex rounded-full h-2 w-2 ${
                    systemStatus === 'online' ? 'bg-phantom-500' :
                    systemStatus === 'offline' ? 'bg-red-500' :
                    'bg-yellow-500'
                  }`}></span>
                </span>
                <span className={`text-xs font-medium ${
                  systemStatus === 'online' ? 'text-phantom-300' :
                  systemStatus === 'offline' ? 'text-red-400' :
                  'text-yellow-400'
                }`}>
                  {systemStatus === 'online' ? 'System Online' :
                   systemStatus === 'offline' ? 'System Offline' :
                   'Checking...'}
                </span>
            </div>
          </div>
        </header>

        {/* Content Area */}
        <div className="flex-1 overflow-y-auto p-8 relative">
           {renderContent()}
        </div>
      </main>
    </div>
  );
};

export default App;
