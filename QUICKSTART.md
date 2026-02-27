# Norn — Quick Start Guide

> Get Norn running locally in under 10 minutes.

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.10+ | Backend |
| Node.js | 18+ | Frontend dashboard |
| AWS account | — | Required for Amazon Bedrock (Nova Lite / Micro) |
| Nova Act API key | — | Shadow Browser verification (optional) |

---

## 1. Clone & Install

```bash
git clone https://github.com/hashtagemy/norn.git
cd norn

python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

pip install -e ".[api]"

# Optional: enable Nova Act shadow browser verification
pip install -e ".[browser]"
# Then set NOVA_ACT_API_KEY in your .env file

cd norn-dashboard
npm install
cd ..
```

---

## 2. Configure Environment

```bash
cp .env.example .env
```

Open `.env` and fill in your AWS credentials:

```dotenv
AWS_ACCESS_KEY_ID=your-access-key-id
AWS_SECRET_ACCESS_KEY=your-secret-access-key
AWS_DEFAULT_REGION=us-east-1

# Optional — for shadow browser verification
# NOVA_ACT_API_KEY=your-nova-act-api-key
```

---

## 3. Start the Backend

```bash
source .venv/bin/activate
python -m norn.api
```

Confirm it is running:

```bash
curl http://localhost:8000/
# {"status":"online","service":"Norn API"}
```

---

## 4. Start the Dashboard

Open a new terminal:

```bash
cd norn-dashboard
npm run dev
```

Navigate to **http://localhost:3000**.

The **"System Online"** indicator in the top bar should be green. If it shows offline, make sure the backend is running.

---

## 5. Run Your First Agent

1. Click **"+ Add Agent"** in the left sidebar
2. Paste a GitHub repository URL **or** upload a ZIP file containing your agent
3. Wait for Norn to import and analyze it
4. Click **"Run"** on the agent card
5. Enter a task description and click **Run**
6. Watch the live execution feed — scores and issues appear in real time

Output files created by the agent land in `norn_logs/workspace/{session_id}/`, keeping the project root clean.

---

## 6. Monitor a Multi-Agent Swarm

Use `swarm_id` to group multiple agents into a single monitored pipeline. Norn tracks them together and calculates an **alignment score** — how closely each agent stayed on the original goal.

```python
from norn import NornHook
from strands import Agent

# Step 1 — Researcher
hook_a = NornHook(
    swarm_id="my-pipeline",   # shared across all agents in this run
    swarm_order=1,            # position in the pipeline
    agent_name="Researcher"
)
agent_a = Agent(tools=[...], hooks=[hook_a])
result_a = agent_a("Find the latest AI safety research trends")

# Step 2 — Writer (receives output from step 1)
hook_b = NornHook(
    swarm_id="my-pipeline",
    swarm_order=2,
    agent_name="Writer"
)
agent_b = Agent(tools=[...], hooks=[hook_b])
agent_b(f"Write a report based on this research: {result_a}")
```

After both agents finish, open the **Swarm Monitor** tab in the dashboard to see:

```
[my-pipeline]  2 agents  Alignment: 82%  GOOD
  1 · Researcher  →  EXCELLENT  Eff 88%  Sec 95%
  2 · Writer      →  GOOD       Eff 76%  Sec 91%
```

The alignment score compares each agent's task to the first agent's intent:
- **≥ 80%** — Aligned (green)
- **50–79%** — Slight Drift (yellow)
- **< 50%** — High Drift (red)

---

## 7. Workspace Isolation

Every agent run gets its own working directory so output files don't pollute the project root:

```
norn_logs/workspace/
├── git-20260227-my-agent-run1/
│   ├── result.txt
│   └── data.db
└── hook-my-pipeline-researcher/
    └── findings.md
```

Agents can also read the `NORN_WORKSPACE` environment variable to explicitly reference their workspace:

```python
import os
from pathlib import Path

workspace = Path(os.environ.get("NORN_WORKSPACE", "."))
output_file = workspace / "result.txt"
output_file.write_text("Done!")
```

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `ModuleNotFoundError: norn` | Run `pip install -e ".[api]"` inside the virtual environment |
| Port 8000 already in use | `lsof -i :8000` to find the process, then `kill <PID>` |
| Dashboard shows "Connection Error" | Confirm the backend is running: `curl localhost:8000/` |
| AWS credentials error | Check `.env` values and verify Bedrock access in your AWS account |
| Scores show "N/A" | AWS Bedrock connection may be failing — check credentials and region |
| Agent import fails | Confirm the GitHub URL is public and accessible |
| Shadow browser shows "UNAVAILABLE" | Set `NOVA_ACT_API_KEY` in `.env` and run `pip install -e ".[browser]"` |
| Swarm Monitor shows empty | Ensure `swarm_id` is set in `NornHook` and agents have completed at least one run |

---

## Next Steps

- **Configuration** — Adjust guard mode and thresholds in the dashboard **Settings** panel
- **Integration** — See [README.md](README.md) to embed Norn directly into your agent code
- **Swarm Monitoring** — See the **Swarm Monitor** tab for multi-agent pipeline visibility
