# Norn

> **AI Agent Quality & Security Monitoring Platform**

Real-time monitoring and testing platform for AI agents. Import agents from GitHub or ZIP, analyze their code, detect issues, and monitor execution in real-time.

[![Amazon Nova](https://img.shields.io/badge/Amazon-Nova-orange)](https://aws.amazon.com/bedrock/nova/)
[![Strands](https://img.shields.io/badge/Strands-1.23%2B-purple)](https://github.com/awslabs/strands)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![React](https://img.shields.io/badge/React-19%2B-blue)](https://react.dev/)

---

## 🎯 The Problem

AI agents are complex and unpredictable:
- Get stuck in infinite loops
- Drift away from their task
- Have missing dependencies or tools
- Leak sensitive data
- Execute malicious commands
- Hallucinate results

You need to test, analyze, and monitor them before and during execution.

---

## 💡 The Solution

Norn provides a complete platform for agent testing and monitoring:

### 1. Import & Analyze
Import agents from GitHub or ZIP files. Norn automatically:
- Discovers tools, functions, and dependencies
- Detects missing packages and installs them
- Identifies potential issues (security, credentials, missing tools)
- Generates a smart test task tailored to the agent's actual capabilities

### 2. Run & Monitor
Execute agents directly from the dashboard with real-time monitoring:
- Live WebSocket updates during execution
- Step-by-step tool call tracking with relevance and security scoring
- Issue detection and alerts
- Automatic loop and drift detection

### 3. Review & Improve
Comprehensive session reports with:
- Task completion analysis
- Efficiency and security scores
- Per-tool usage analysis (correct / incorrect / unnecessary)
- Agent decision-making observations
- AI-powered recommendations via Amazon Nova

### 4. Browser Audit *(requires Nova Act API key)*
Shadow browser verification powered by Nova Act:
- Automatically visits URLs accessed by agents
- Verifies that web content matches expected results
- Detects prompt injection attacks embedded in web pages
- Enable: `pip install -e ".[browser]"` + set `NOVA_ACT_API_KEY` in `.env`

---

## 🚀 Quick Start

```bash
# Clone repository
git clone https://github.com/hashtagemy/norn.git
cd norn

# Install backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[api]"

# Install frontend
cd norn-dashboard && npm install && cd ..

# Configure environment
cp .env.example .env  # Add your AWS credentials

# Start backend (terminal 1)
python -m norn.api

# Start dashboard (terminal 2)
cd norn-dashboard && npm run dev
# Open http://localhost:3000
```

Open **http://localhost:3000** — the dashboard will be live.

See [QUICKSTART.md](QUICKSTART.md) for detailed setup instructions.

---

## 🎨 Dashboard

Modern React dashboard with real-time monitoring:

- **Agent Management** — Import from GitHub or ZIP, view all registered agents
- **Code Analysis** — Automatic discovery of tools, functions, and dependencies
- **Smart Task Generation** — AI-generated test tasks based on each agent's actual tools
- **Dependency Management** — Auto-install missing packages (PyPI and local)
- **Real-Time Monitoring** — WebSocket-based live updates during execution
- **Session History** — View all past executions with detailed reports
- **Issue Detection** — Security, quality, and dependency issues highlighted
- **Browser Audit** — Nova Act shadow verification for web-browsing agents (set `NOVA_ACT_API_KEY` to enable)
- **Configuration** — Adjust guard mode and thresholds from the UI

**Tech Stack:** React 19 + TypeScript + Tailwind CSS + Vite · FastAPI + Python 3.10+ · WebSocket

---

## 🧠 How It Works

### 1. Agent Import & Discovery
- Import agents from GitHub (with subfolder support) or ZIP files
- AST-based code analysis discovers tools, functions, dependencies, and entry points
- Automatic dependency installation (PyPI and local packages)

### 2. Smart Task Generation
- Analyzes the agent's tools and groups them by capability (web, file, shell, search)
- AI generates a concrete, safe test task that exercises the agent's actual tools
- Tasks always use real URLs and create files before reading them — no hallucinated paths

### 3. Static Analysis
- **Dependency Check** — Detects missing, installed, and local packages
- **Security Scan** — Identifies hardcoded credentials and potential leaks
- **Tool Detection** — Finds `@tool` decorators, external tools, tool imports
- **Issue Classification** — HIGH / MEDIUM / LOW severity with descriptions

### 4. Runtime Monitoring
- **Step Analyzer** — Detects loops, drift, and inefficiency (deterministic, fast)
- **Quality Evaluator** — AI-powered relevance and security scoring via Amazon Nova Micro
- **Security Monitor** — Checks for data leaks, injections, unauthorized access

### 5. Session Evaluation
After task completion, deep analysis with Nova Lite:
- Task completion assessment with confidence score
- Per-tool usage analysis: was each tool used correctly?
- Decision-making pattern observations
- Efficiency explanation (actual steps vs expected)
- Actionable recommendations

### 6. Browser Audit *(optional)*
When agents visit URLs, Nova Act runs a shadow browser session to independently verify:
- The page content matches what the agent reported
- No prompt injection payloads are present in the page
- The agent's actions were legitimate and expected

Requires a Nova Act API key (early access): set `NOVA_ACT_API_KEY` in your `.env`.

---

## 🤖 Amazon Nova Models Used

Norn is built entirely on the Amazon Nova model family, with each model chosen for its strengths:

| Feature | Model | Why |
|---|---|---|
| **Smart Task Generation** | Nova 2 Lite | Generates structured JSON test tasks tailored to each agent's tools |
| **Step Relevance Scoring** | Nova 2 Lite | Per-step relevance (0–100) evaluated in real time during execution |
| **Security Scoring** | Nova 2 Lite | Detects data exfiltration, prompt injection, and credential leaks per step |
| **Session Evaluation** | Nova 2 Lite | Deep post-run analysis: task completion, tool usage, decision patterns, efficiency |
| **Browser Audit** | Nova Act | Autonomous browser agent that independently visits URLs and detects prompt injection |

**Model IDs (configurable via `.env`):**
```
amazon.nova-2-lite-v1:0   # All AI features: real-time scoring, task gen, session eval
Nova Act                  # Shadow browser verification (requires NOVA_ACT_API_KEY)
```

---

## 🔧 Integration Methods

Add Norn to your own Strands agent in one of four ways:

### 1. Manual Hook
```python
from norn import NornHook
from strands import Agent

guard = NornHook(task="Your task description")
agent = Agent(tools=[...], hooks=[guard])
agent("Your task description")
```

### 2. Proxy Wrapper
```python
from norn.proxy import MonitoredAgent

agent = MonitoredAgent(model=model, tools=tools, task="Your task description")
agent("Your task description")
```

### 3. Global Monitoring
```python
from norn.proxy import enable_global_monitoring

enable_global_monitoring()
# All Agent instances are now monitored automatically
```

### 4. Environment Variable (Zero Code)
```bash
export NORN_AUTO_ENABLE=true
python your_agent.py
```

---

## 📊 What You Get

### Session Reports
```json
{
  "session_id": "abc123",
  "overall_quality": "GOOD",
  "efficiency_score": 85,
  "security_score": 100,
  "task_completion": true,
  "tool_analysis": [
    {"tool": "file_write", "usage": "correct", "note": "Created the output file as required"},
    {"tool": "summarize_file", "usage": "correct", "note": "Read and summarized the file accurately"}
  ],
  "decision_observations": ["Agent followed a logical sequence without unnecessary steps"],
  "efficiency_explanation": "Used 3 steps against an expected 10 — very efficient.",
  "recommendations": []
}
```

### Quality Levels
| Level | Score | Meaning |
|---|---|---|
| **EXCELLENT** | 90–100% | Efficient, no issues |
| **GOOD** | 70–89% | Completed with minor issues |
| **POOR** | 40–69% | Inefficient or problematic |
| **FAILED** | 0–39% | Task not completed |
| **STUCK** | — | Infinite loop detected |

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   React Dashboard                        │
│  (Agent Import, Analysis View, Execution, Monitoring)   │
└────────────────────┬────────────────────────────────────┘
                     │ WebSocket + REST API
┌────────────────────▼────────────────────────────────────┐
│                  FastAPI Backend (:8000)                 │
│  • Agent Import (GitHub/ZIP)                            │
│  • Code Discovery & Analysis                            │
│  • Smart Task Generation (Nova Lite)                    │
│  • Dependency Installation                              │
│  • Agent Execution                                      │
│  • Session Management                                   │
└────────────────────┬────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────┐
│              Norn Core                          │
│  • Hook Integration (Strands)                           │
│  • Step Analyzer (loops, drift)                         │
│  • Quality Evaluator (Amazon Nova)                      │
│  • Security Monitor                                     │
│  • Browser Audit (Nova Act) — optional                  │
│  • Audit Logger (JSON)                                  │
└─────────────────────────────────────────────────────────┘
```

**Key files:**

| File | Purpose |
|---|---|
| `norn/api.py` | FastAPI server — REST + WebSocket + task generation |
| `norn/core/interceptor.py` | Hook implementation |
| `norn/core/step_analyzer.py` | Deterministic loop & drift detection |
| `norn/core/audit_logger.py` | Structured JSON logging |
| `norn/agents/quality_evaluator.py` | AI scoring via Amazon Nova |
| `norn/agents/shadow_browser.py` | Nova Act browser verification |
| `norn/utils/agent_discovery.py` | AST-based code analysis |
| `norn/utils/agent_runner.py` | Agent execution harness |

---

## 🎓 Use Cases

- **Development** — Debug agent behavior, identify inefficiencies, test security posture
- **Production** — Monitor agent quality, detect anomalies, ensure compliance
- **Research** — Analyze behavior patterns, compare approaches, collect execution data

---

## 📚 Documentation

- [QUICKSTART.md](QUICKSTART.md) — Step-by-step setup guide

---

## 📄 License

Apache 2.0 — See [LICENSE](LICENSE) for details.

---

## 🙏 Built With

[FastAPI](https://fastapi.tiangolo.com/) · [Strands](https://github.com/awslabs/strands) · [Amazon Nova](https://aws.amazon.com/bedrock/nova/) · [Nova Act](https://aws.amazon.com/nova/act/) · [React](https://react.dev/) · [Tailwind CSS](https://tailwindcss.com/) · [Vite](https://vitejs.dev/)

---

*Norn — Because your agents should be monitored, not mysterious.*
