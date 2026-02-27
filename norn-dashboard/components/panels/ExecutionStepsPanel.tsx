import React from 'react';
import { Session } from '../../types';
import { Terminal, AlertCircle, AlertTriangle, Info, XCircle, Trash2, Brain } from 'lucide-react';

interface ExecutionStepsPanelProps {
  session: Session;
  onDeleteStep?: (stepId: string) => void;
}

export const ExecutionStepsPanel: React.FC<ExecutionStepsPanelProps> = ({ session, onDeleteStep }) => {
  const isTerminated = session.status === 'terminated';
  const isActive = session.status === 'active';
  const hasSteps = session.steps.length > 0;
  const hasIssues = session.issueDetails && session.issueDetails.length > 0;

  return (
    <div className="bg-dark-surface/50 rounded-lg border border-dark-border overflow-hidden">
      <div className="p-3 bg-dark-surface border-b border-dark-border">
        <h3 className="text-xs font-semibold text-gray-200 flex items-center gap-2">
          <Terminal size={13} className="text-phantom-500" />
          Execution Timeline
        </h3>
      </div>
      <div className="p-3 space-y-3">
        {/* Empty state — terminated or completed with no steps */}
        {!hasSteps && !isActive && (
          <div className="space-y-2">
            <div className={`flex items-start gap-2 p-3 rounded-lg border ${
              isTerminated ? 'bg-red-950/20 border-red-900/30' : 'bg-gray-900/20 border-gray-800/30'
            }`}>
              <XCircle size={14} className={isTerminated ? 'text-red-400 shrink-0 mt-0.5' : 'text-gray-500 shrink-0 mt-0.5'} />
              <div>
                <p className={`text-xs font-medium ${isTerminated ? 'text-red-300' : 'text-gray-400'}`}>
                  {isTerminated ? 'Execution failed before any tools were called' : 'No execution steps recorded'}
                </p>
                <p className="text-[10px] text-gray-500 mt-0.5">
                  {isTerminated
                    ? 'The agent crashed during initialization — check the issues below for details.'
                    : 'This session completed without recording any tool calls.'}
                </p>
              </div>
            </div>

            {/* Show issues when no steps */}
            {hasIssues && (
              <div className="space-y-1.5">
                <h4 className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider px-1">Detected Issues</h4>
                {session.issueDetails.map((issue, idx) => (
                  <div key={issue.issueId || idx} className={`flex items-start gap-2 p-2 rounded-lg border ${
                    issue.severity >= 8 ? 'bg-red-950/20 border-red-900/30' :
                    issue.severity >= 5 ? 'bg-yellow-950/20 border-yellow-900/30' :
                    'bg-blue-950/20 border-blue-900/30'
                  }`}>
                    {issue.severity >= 8 ? (
                      <AlertCircle size={13} className="text-red-400 shrink-0 mt-0.5" />
                    ) : issue.severity >= 5 ? (
                      <AlertTriangle size={13} className="text-yellow-400 shrink-0 mt-0.5" />
                    ) : (
                      <Info size={13} className="text-blue-400 shrink-0 mt-0.5" />
                    )}
                    <div>
                      <div className={`text-xs font-medium ${
                        issue.severity >= 8 ? 'text-red-300' :
                        issue.severity >= 5 ? 'text-yellow-300' :
                        'text-blue-300'
                      }`}>
                        {issue.issueType.replace(/_/g, ' ')}
                      </div>
                      <div className="text-[10px] text-gray-400 mt-0.5">{issue.description}</div>
                      {issue.recommendation && (
                        <div className="text-[10px] text-gray-500 mt-0.5 italic">{issue.recommendation}</div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {[...session.steps].reverse().map((step, idx, arr) => (
          <div key={step.id} className="group relative pl-6">
            {/* Timeline Line */}
            {idx < arr.length - 1 && (
              <div className="absolute left-2 top-5 bottom-0 w-px bg-dark-border"></div>
            )}

            {/* Timeline Dot */}
            <div className={`absolute left-[0.3rem] top-1.5 w-2 h-2 rounded-full border-2 border-dark-bg z-10 ${
              step.type === 'phantom_check' ? (
                step.metadata?.riskScore == null ? 'bg-gray-500' :
                step.metadata.riskScore > 50 ? 'bg-red-500' : 'bg-emerald-500'
              ) :
              step.type === 'agent_thought' ? 'bg-purple-500' :
              step.type === 'tool_result' ? 'bg-cyan-500' :
              step.type === 'user' ? 'bg-blue-500' : 'bg-gray-600'
            }`}></div>

            {/* Delete button — appears on hover */}
            {onDeleteStep && (
              <button
                onClick={() => onDeleteStep(step.id)}
                title="Delete step"
                className="absolute top-0 right-0 opacity-0 group-hover:opacity-100 transition-opacity p-1 rounded hover:bg-red-950/40 z-10"
              >
                <Trash2 size={11} className="text-red-500/50 hover:text-red-400" />
              </button>
            )}

            <div className="space-y-1.5">
              <div className="flex items-center gap-1.5">
                <span className="text-[10px] font-mono text-gray-500">
                  {step.timestamp ? new Date(step.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : ''}
                </span>
                <span className={`text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded flex items-center gap-1 ${
                  step.type === 'phantom_check' ? 'bg-phantom-950 text-phantom-400' :
                  step.type === 'agent_thought' ? 'bg-purple-950/40 text-purple-300' :
                  step.type === 'tool_call' ? 'bg-orange-950/30 text-orange-400' :
                  step.type === 'tool_result' ? 'bg-cyan-950/30 text-cyan-400' :
                  step.type === 'user' ? 'bg-blue-950/30 text-blue-400' : 'bg-gray-800 text-gray-400'
                }`}>
                  {step.type === 'agent_thought' && <Brain size={9} />}
                  {step.type === 'agent_thought' ? 'AI Reasoning' : step.type.replace(/_/g, ' ')}
                </span>
                {step.metadata?.toolName && step.type !== 'agent_thought' && (
                  <span className="text-[10px] text-gray-500">&rarr; {step.metadata.toolName}</span>
                )}
              </div>

              <div className={`text-xs p-2 rounded-lg font-mono border whitespace-pre-wrap ${
                step.type === 'phantom_check'
                  ? (step.metadata?.riskScore == null ? 'bg-gray-900/10 border-gray-800/30 text-gray-400' :
                     step.metadata.riskScore > 0 ? 'bg-red-950/10 border-red-900/30 text-red-200' :
                     'bg-emerald-950/10 border-emerald-900/30 text-emerald-200')
                  : step.type === 'agent_thought' ? 'bg-purple-950/10 border-purple-900/20 text-purple-100'
                  : step.type === 'tool_result' ? 'bg-cyan-950/5 border-cyan-900/20 text-gray-400'
                  : 'bg-black/30 border-dark-border text-gray-300'
              }`}>
                {step.content}
              </div>

              {/* Nova Act shadow verification badge */}
              {step.type === 'phantom_check' && step.metadata?.shadowVerification && (
                <div className="mt-1 flex items-center gap-1.5 flex-wrap">
                  <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium flex items-center gap-1 ${
                    step.metadata.shadowVerification.verificationResult === 'VERIFIED'
                      ? 'bg-emerald-950/30 text-emerald-400 border border-emerald-900/30'
                      : step.metadata.shadowVerification.verificationResult === 'SECURITY_CONCERN'
                      ? 'bg-red-950/30 text-red-400 border border-red-900/30'
                      : 'bg-gray-800 text-gray-500 border border-gray-700'
                  }`}>
                    🔍 Nova Act: {step.metadata.shadowVerification.verificationResult}
                  </span>
                  {step.metadata.shadowVerification.securityScore != null && (
                    <span className="text-[10px] text-gray-500">
                      {step.metadata.shadowVerification.securityScore}/100
                    </span>
                  )}
                  {step.metadata.shadowVerification.securityIssues.map((issue, i) => (
                    <span key={i} className="text-[10px] text-red-400">⚠ {issue}</span>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}

        {session.status === 'active' && (
          <div className="pl-6 pt-1 animate-pulse flex items-center gap-2">
            <div className="w-1.5 h-1.5 bg-phantom-500 rounded-full"></div>
            <span className="text-[10px] text-gray-500 italic">Agent is thinking...</span>
          </div>
        )}
      </div>
    </div>
  );
};
