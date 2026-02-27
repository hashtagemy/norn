/**
 * Norn API Client
 */

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export interface Agent {
  id: string;
  name: string;
  source: 'git' | 'zip' | 'hook';
  repo_url?: string;
  branch?: string;
  main_file: string;
  task_description: string;
  clone_path?: string;
  extract_path?: string;
  added_at: string;
  status: 'ready' | 'analyzing' | 'analyzed' | 'completed' | 'error';
  discovery?: {
    status: string;
    tools: Array<{
      name: string;
      description: string;
      parameters: string[];
      line: number;
    }>;
    functions: Array<{
      name: string;
      description: string;
      line: number;
      is_async: boolean;
    }>;
    classes: Array<{
      name: string;
      description: string;
      bases: string[];
      line: number;
    }>;
    imports: string[];
    dependencies: Array<{
      name: string;
      status: 'installed' | 'missing';
    }>;
    potential_issues: Array<{
      type: string;
      severity: 'HIGH' | 'MEDIUM' | 'LOW';
      description: string;
    }>;
    agent_type: string;
    entry_points: string[];
  };
}

export interface SessionIssue {
  issue_id?: string;
  issue_type: string;
  severity: number;
  description: string;
  recommendation: string;
  affected_steps?: string[];
}

export interface SessionStep {
  step_id: string;
  step_number: number;
  timestamp: string;
  tool_name: string;
  tool_input: string;
  tool_result: string;
  status: 'SUCCESS' | 'FAILED' | 'IRRELEVANT' | 'REDUNDANT' | 'BLOCKED';
  relevance_score: number | null;  // null = not yet evaluated
  security_score: number | null;   // null = not yet evaluated
  reasoning: string;
  metadata?: {
    shadow_verification?: {
      verified: boolean;
      verification_result: string;
      verification_method: string;
      security_score: number | null;
      security_issues: string[];
      details: string;
      url?: string;
    };
    [key: string]: any;
  };
}

export interface SessionData {
  session_id: string;
  agent_name: string;
  model?: string;
  task: string;
  start_time: string;
  end_time?: string;
  status: 'active' | 'completed' | 'terminated';
  total_steps: number;
  overall_quality: 'EXCELLENT' | 'GOOD' | 'POOR' | 'FAILED' | 'STUCK' | 'PENDING';
  efficiency_score: number | null;  // null = not yet evaluated
  security_score: number | null;    // null = not yet evaluated
  issues: SessionIssue[];
  steps: SessionStep[];
  ai_evaluation?: string;
  recommendations?: string[];
  task_completion?: boolean;
  loop_detected?: boolean;
  security_breach_detected?: boolean;
  total_execution_time_ms?: number;
}

export interface AuditLogEvent {
  id: string;
  timestamp: string;
  event_type: 'session_start' | 'session_end' | 'tool_call' | 'issue';
  session_id: string;
  agent_name: string;
  summary: string;
  severity: 'info' | 'warning' | 'critical';
  detail?: string;
}

export interface Stats {
  total_sessions: number;
  active_sessions: number;
  critical_threats: number;
  avg_efficiency: number;
  avg_security: number;
  total_agents: number;
}

class ApiClient {
  private baseUrl: string;
  private ws: WebSocket | null = null;
  private wsReconnectTimer: NodeJS.Timeout | null = null;
  private reconnectAttempts: number = 0;
  private maxReconnectAttempts: number = 10;
  private wsPingInterval: NodeJS.Timeout | null = null;

  constructor(baseUrl: string = API_BASE_URL) {
    this.baseUrl = baseUrl;
  }

  // WebSocket connection with exponential backoff reconnection
  private wsDisconnected = false; // Flag to prevent reconnect after intentional disconnect

  connectWebSocket(onMessage: (data: any) => void, onError?: (error: Event) => void): void {
    this.wsDisconnected = false;
    const wsUrl = this.baseUrl.replace('http://', 'ws://').replace('https://', 'wss://');

    try {
      this.ws = new WebSocket(`${wsUrl}/ws/sessions`);

      this.ws.onopen = () => {
        console.log('WebSocket connected');
        this.reconnectAttempts = 0;

        if (this.wsPingInterval) clearInterval(this.wsPingInterval);

        this.wsPingInterval = setInterval(() => {
          if (this.ws?.readyState === WebSocket.OPEN) {
            this.ws.send('ping');
          } else {
            if (this.wsPingInterval) clearInterval(this.wsPingInterval);
          }
        }, 4000);
      };

      this.ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          onMessage(data);
        } catch (err) {
          console.error('Failed to parse WebSocket message:', err);
        }
      };

      this.ws.onerror = (error) => {
        console.error('WebSocket error:', error);
        // Don't call onError here — let onclose handle reconnect logic
      };

      this.ws.onclose = () => {
        if (this.wsPingInterval) clearInterval(this.wsPingInterval);

        // If intentionally disconnected, don't reconnect
        if (this.wsDisconnected) return;

        if (this.reconnectAttempts < this.maxReconnectAttempts) {
          const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 30000);
          this.reconnectAttempts++;
          console.log(`WebSocket reconnecting in ${delay / 1000}s (attempt ${this.reconnectAttempts}/${this.maxReconnectAttempts})...`);
          this.wsReconnectTimer = setTimeout(() => {
            this.connectWebSocket(onMessage, onError);
          }, delay);
        } else {
          console.error('WebSocket: max reconnect attempts reached, falling back to polling');
          if (onError) onError(new Event('max_reconnect_exceeded'));
        }
      };
    } catch (err) {
      console.error('Failed to create WebSocket:', err);
      if (onError) onError(err as Event);
    }
  }

  disconnectWebSocket(): void {
    this.wsDisconnected = true;
    if (this.wsPingInterval) {
      clearInterval(this.wsPingInterval);
      this.wsPingInterval = null;
    }
    if (this.wsReconnectTimer) {
      clearTimeout(this.wsReconnectTimer);
      this.wsReconnectTimer = null;
    }
    if (this.ws) {
      this.ws.onclose = null; // Prevent reconnect from firing
      this.ws.onerror = null;
      this.ws.close();
      this.ws = null;
    }
  }

  private async request<T>(endpoint: string, options?: RequestInit): Promise<T> {
    const headers: Record<string, string> = {
      ...options?.headers as Record<string, string>,
    };
    // Only add Content-Type for requests with a body (POST, PUT, PATCH)
    const method = (options?.method || 'GET').toUpperCase();
    if (options?.body || ['POST', 'PUT', 'PATCH'].includes(method)) {
      headers['Content-Type'] = 'application/json';
    }

    const response = await fetch(`${this.baseUrl}${endpoint}`, {
      ...options,
      headers,
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(error.detail || `HTTP ${response.status}`);
    }

    return response.json();
  }

  // Sessions
  async getSessions(limit: number = 50): Promise<SessionData[]> {
    return this.request<SessionData[]>(`/api/sessions?limit=${limit}`);
  }

  async getSession(sessionId: string): Promise<SessionData> {
    return this.request<SessionData>(`/api/sessions/${sessionId}`);
  }

  // Swarms
  async getSwarms(): Promise<any[]> {
    return this.request<any[]>('/api/swarms');
  }

  async getSwarm(swarmId: string): Promise<any> {
    return this.request<any>(`/api/swarms/${swarmId}`);
  }

  // Agents
  async getAgents(): Promise<Agent[]> {
    return this.request<Agent[]>('/api/agents');
  }

  async getAgent(agentId: string): Promise<Agent> {
    return this.request<Agent>(`/api/agents/${agentId}`);
  }

  async importGithubAgent(data: {
    repo_url: string;
    agent_name?: string;
    branch?: string;
    main_file?: string;
    task_description?: string;
  }): Promise<Agent[]> {
    return this.request<Agent[]>('/api/agents/import/github', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  }

  async deleteAgent(agentId: string): Promise<{ status: string; id: string }> {
    return this.request(`/api/agents/${agentId}`, {
      method: 'DELETE',
    });
  }

  async deleteSession(sessionId: string): Promise<{ status: string; id: string }> {
    return this.request(`/api/sessions/${sessionId}`, {
      method: 'DELETE',
    });
  }

  async runAgent(agentId: string, task: string): Promise<{ status: string; session_id: string; agent_id: string; message: string }> {
    return this.request(`/api/agents/${agentId}/run`, {
      method: 'POST',
      body: JSON.stringify({ task }),
    });
  }

  // Audit logs
  async getAuditLogs(limit: number = 200): Promise<AuditLogEvent[]> {
    return this.request<AuditLogEvent[]>(`/api/audit-logs?limit=${limit}`);
  }

  // Config
  async getConfig(): Promise<Record<string, any>> {
    return this.request<Record<string, any>>('/api/config');
  }

  async updateConfig(data: Record<string, any>): Promise<Record<string, any>> {
    return this.request<Record<string, any>>('/api/config', {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  }

  // Stats
  async getStats(): Promise<Stats> {
    return this.request<Stats>('/api/stats');
  }

  // Health check — uses plain fetch without Content-Type to avoid CORS preflight
  async healthCheck(): Promise<{ status: string; service: string }> {
    const response = await fetch(`${this.baseUrl}/`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }
}

export const api = new ApiClient();
