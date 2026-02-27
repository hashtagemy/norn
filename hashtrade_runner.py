#!/usr/bin/env python3
"""
HashTrade + Norn Swarm Monitor Runner
--------------------------------------
HashTrade'in iki ajanını (Controller + Thinker) Norn Swarm Monitor'da
adım adım izlemek için çalıştırır.

Swarm akışı:
  [1] Controller  →  Sistem durumu + market analizi ister
        ↓ (handoff: analiz özeti)
  [2] Thinker     →  Canlı Bybit verisiyle işlem kararı verir

Kullanım:
  python hashtrade_runner.py
  NORN_TASK="show status and decide" python hashtrade_runner.py
"""

import os
import sys
import subprocess
from datetime import datetime
from pathlib import Path

# ── HashTrade extracted path ──────────────────────────────────────────────────
HASHTRADE_DIR = "/var/folders/74/xjck5np15fb28lhlvjz6byj40000gn/T/tmpjor5n9l_/agent_files/hashtrade-v2"

# ── 1. Zshrc env vars yükle (BYBIT_API_KEY, AWS_BEARER_TOKEN_BEDROCK…) ───────
def load_zshrc_env():
    try:
        result = subprocess.run(
            ["zsh", "-c", "source ~/.zshrc && env"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            for line in result.stdout.split("\n"):
                if "=" in line:
                    k, _, v = line.partition("=")
                    if k.strip() and v and k not in os.environ:
                        os.environ[k.strip()] = v
            print("✅ ~/.zshrc env vars yüklendi (BYBIT + Bedrock dahil)")
    except Exception as e:
        print(f"⚠️  Zshrc yüklenemedi: {e}")

load_zshrc_env()

# ── 2. Python path ayarla ─────────────────────────────────────────────────────
sys.path.insert(0, HASHTRADE_DIR)

# ── 3. Swarm ID oluştur ───────────────────────────────────────────────────────
swarm_id = os.environ.get("NORN_SWARM_ID") or f"hashtrade-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
os.environ["NORN_SWARM_ID"] = swarm_id
print(f"🔗 Swarm ID: {swarm_id}")

# ── 4. Norn import ────────────────────────────────────────────────────────────
try:
    sys.path.insert(0, str(Path(__file__).parent))
    from norn import NornHook
    NORN_API = "http://localhost:8000"
    print("✅ NornHook hazır")
except ImportError as e:
    print(f"❌ NornHook import hatası: {e}")
    sys.exit(1)

# ── 5. Strands Agent import ───────────────────────────────────────────────────
from strands import Agent

# ══════════════════════════════════════════════════════════════════════════════
# AGENT 1: Controller  (swarm_order=1)
# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "═"*60)
print("🤖  AGENT 1 — HashtagController")
print("═"*60)

task = os.environ.get(
    "NORN_TASK",
    "Get system status and market analysis for BTC/USDT:USDT. "
    "Check balance, open positions, trend direction, and give a brief recommendation."
)

controller_hook = NornHook(
    swarm_id=swarm_id,
    swarm_order=1,
    agent_name="Controller",
    task=task,
    norn_url=NORN_API,
)

# Patch: HashtagController.__init__ sonrası agent'a hook enjekte et
import controller.agent as _ca

_orig_controller_init = _ca.HashtagController.__init__

def _patched_controller_init(self):
    _orig_controller_init(self)
    # Hook'u mevcut agent'a enjekte et (agent yeniden oluşturuluyor)
    self.agent = Agent(
        model=self.model,
        tools=[
            _ca.get_system_status,
            _ca.get_trade_history,
            _ca.start_autonomous_trader,
            _ca.stop_autonomous_trader,
            _ca.get_market_analysis,
            _ca.get_learnings,
        ],
        system_prompt=_ca.SYSTEM_PROMPT,
        hooks=[controller_hook],
    )
    print(f"   ↳ NornHook enjekte edildi (swarm_order=1)")

_ca.HashtagController.__init__ = _patched_controller_init

from controller.agent import HashtagController
controller = HashtagController()
controller_result = controller.chat(task)
print(f"\n📋 Controller yanıtı:\n{controller_result[:600]}{'...' if len(controller_result) > 600 else ''}")

# ══════════════════════════════════════════════════════════════════════════════
# AGENT 2: TraderThinker  (swarm_order=2)
# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "═"*60)
print("🧠  AGENT 2 — TraderThinker")
print("═"*60)

thinker_task = "Analyze live BTC/USDT:USDT market data and decide: LONG, SHORT, or WAIT. Evaluate ICT setups, trend confluence and risk."

thinker_hook = NornHook(
    swarm_id=swarm_id,
    swarm_order=2,
    agent_name="Thinker",
    task=thinker_task,
    handoff_input=controller_result[:800] if controller_result else None,
    norn_url=NORN_API,
)

# Patch: TraderThinker.__init__ sonrası hook enjekte et
import autonomous.thinker as _thinker_mod

_orig_thinker_init = _thinker_mod.TraderThinker.__init__

def _patched_thinker_init(self, memory=None):
    from autonomous.trade_memory import TraderMemory
    self.memory = memory or TraderMemory()
    # Önce system prompt'u oluştur
    self._build_system_prompt = _orig_build_system_prompt.__get__(self, type(self))
    self.agent = Agent(
        model=_thinker_mod.get_model(),
        system_prompt=self._build_system_prompt(),
        hooks=[thinker_hook],
    )
    print(f"   ↳ NornHook enjekte edildi (swarm_order=2)")

# _build_system_prompt yöntemi patch sonrası erişilebilir olmalı
_orig_build_system_prompt = _thinker_mod.TraderThinker._build_system_prompt
_thinker_mod.TraderThinker.__init__ = _patched_thinker_init

from autonomous.thinker import TraderThinker

thinker = TraderThinker()

# Canlı market verisi çek (Bybit)
print("\n📡 Bybit'ten canlı BTC/USDT verisi çekiliyor...")
market_data = {}
try:
    from autonomous.data_manager import DataManager
    dm = DataManager()
    symbol = "BTC/USDT:USDT"
    mtf = dm.get_multi_timeframe(symbol, timeframes=["4h", "1h", "15m"])
    # Son mumları özet olarak al
    for tf, df in mtf.items():
        if df is not None and not df.empty:
            last = df.iloc[-1]
            market_data[tf] = {
                "close": float(last.get("close", 0)),
                "high": float(last.get("high", 0)),
                "low": float(last.get("low", 0)),
                "volume": float(last.get("volume", 0)),
                "bars": len(df),
            }
    print(f"   ↳ Veri alındı: {list(market_data.keys())}")
except Exception as e:
    print(f"   ⚠️  Market verisi alınamadı: {e} — basit prompt kullanılıyor")
    market_data = {
        "note": "Live data unavailable, use available tools to analyze",
        "symbol": "BTC/USDT:USDT",
        "timestamp": datetime.now().isoformat(),
    }

# Thinker'ı analiz için çalıştır
print("\n💭 Thinker analiz yapıyor...")
try:
    decision = thinker.analyze_market(market_data)
    print(f"\n🎯 Thinker kararı: {decision}")
except Exception as e:
    print(f"⚠️  analyze_market hatası: {e}")
    # Fallback: direkt agent'ı prompt ile çalıştır
    import json
    prompt = (
        f"Market data for BTC/USDT:USDT:\n{json.dumps(market_data, indent=2)}\n\n"
        f"Context from Controller:\n{controller_result[:600]}\n\n"
        "Analyze this data. What is the trend? Should we LONG, SHORT, or WAIT? "
        "Give a clear structured answer with entry, SL, TP levels if applicable."
    )
    decision = str(thinker.agent(prompt))
    print(f"\n🎯 Thinker yanıtı:\n{str(decision)[:600]}")

# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "═"*60)
print(f"✅  HashTrade Swarm tamamlandı → {swarm_id}")
print(f"📊  Norn Swarm Monitor: http://localhost:3000 (Swarm Monitor sekmesi)")
print("═"*60)
