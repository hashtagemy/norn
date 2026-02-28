# Norn — Proje Genel Bakışı

> **Rapor Tarihi:** 2026-02-28  
> **Sürüm:** 0.2.0  
> **Lisans:** Apache 2.0

---

## 1. Proje Nedir?

Norn, **AI Agent Kalite & Güvenlik İzleme Platformu**dur. Strands tabanlı AI agent'larının gerçek zamanlı izlenmesi, test edilmesi ve güvenlik denetiminden sorumludur.

### Temel Yetenekler

| Yetenek | Açıklama |
|---|---|
| **Agent İçe Aktarma** | GitHub veya ZIP üzerinden agent'ları içe aktarır |
| **Kod Analizi** | AST tabanlı araç, bağımlılık ve güvenlik keşfi |
| **Akıllı Görev Üretimi** | Agent'ın araçlarına göre AI ile test görevi oluşturur |
| **Gerçek Zamanlı İzleme** | WebSocket ile canlı step-by-step izleme |
| **Döngü Algılama** | Deterministik tekrar örüntüsü tespiti |
| **Güvenlik Tarama** | Veri sızıntısı, prompt injection, credential leak tespiti |
| **Swarm İzleme** | Multi-agent pipeline'ları için hizalama skoru |
| **Shadow Browser** | Nova Act ile tarayıcı doğrulaması (opsiyonel) |
| **Oturum Değerlendirme** | Amazon Nova Lite ile AI destekli analiz |

---

## 2. Mimari

```
┌─────────────────────────────────────────────┐
│           React Dashboard (Vite)            │
│  TypeScript + Tailwind CSS + Recharts       │
│  Port: 5173 (dev) / 3000                    │
└────────────────┬────────────────────────────┘
                 │ WebSocket + REST
┌────────────────▼────────────────────────────┐
│         FastAPI Backend (:8000)              │
│  Agent Import, Discovery, Execution         │
│  Session Management, Swarm Endpoints        │
└────────────────┬────────────────────────────┘
                 │
┌────────────────▼────────────────────────────┐
│              Norn Core                      │
│  NornHook (Strands Hook Provider)           │
│  StepAnalyzer (deterministik algılama)      │
│  QualityEvaluator (Amazon Nova Lite)        │
│  ShadowBrowser (Nova Act, opsiyonel)        │
│  AuditLogger (JSON dosya depolama)          │
└─────────────────────────────────────────────┘
```

---

## 3. Teknoloji Yığını

### Backend
- **Python 3.10+**
- **FastAPI** — REST API + WebSocket
- **Strands Agents** ≥ 1.23.0 — Hook sistemi
- **Pydantic** ≥ 2.0 — Veri modelleri
- **Boto3** — AWS Bedrock bağlantısı
- **Amazon Nova 2 Lite** — AI değerlendirme motoru

### Frontend
- **React 19** + TypeScript
- **Vite 6** — Build tool
- **Tailwind CSS 3** — Stil
- **Lucide React** — İkonlar
- **Recharts** — Grafikler

---

## 4. Dosya Yapısı

```
norn/
├── __init__.py               # Paket girişi, NornHook export
├── api.py                    # FastAPI sunucusu (2304 satır, ~91KB)
├── proxy.py                  # MonitoredAgent + global monitoring
├── core/
│   ├── interceptor.py        # NornHook (968 satır) — ana izleme motoru
│   ├── step_analyzer.py      # Döngü ve verimlilik algılama (148 satır)
│   └── audit_logger.py       # JSON dosya tabanlı loglama (212 satır)
├── agents/
│   ├── quality_evaluator.py  # AI değerlendirme (318 satır)
│   └── shadow_browser.py     # Nova Act tarayıcı doğrulama (206 satır)
├── models/
│   └── schemas.py            # Pydantic veri modelleri (204 satır)
└── utils/
    ├── agent_discovery.py    # AST tabanlı kod analizi (444 satır)
    ├── agent_runner.py       # Agent çalıştırma harness (251 satır)
    └── aws_config.py         # AWS kimlik doğrulama (129 satır)

norn-dashboard/
├── App.tsx                   # Ana uygulama (389 satır)
├── services/api.ts           # API istemcisi (342 satır)
├── types.ts                  # TypeScript tip tanımları (86 satır)
└── components/
    ├── Dashboard.tsx          # Ana kontrol paneli
    ├── AddAgent.tsx           # Agent ekleme formu
    ├── AgentDetail.tsx        # Agent detay sayfası
    ├── SessionDetail.tsx      # Oturum detay sayfası
    ├── SessionList.tsx        # Oturum listesi
    ├── SwarmView.tsx          # Swarm izleme
    ├── AuditLogView.tsx       # Audit log görüntüleyici
    ├── BrowserAuditView.tsx   # Shadow Browser sonuçları
    ├── ConfigView.tsx         # Konfigürasyon sayfası
    ├── Sidebar.tsx            # Yan menü
    ├── StatusBadge.tsx        # Durum rozetleri
    └── panels/
        ├── AIAnalysisPanel.tsx
        ├── ExecutionStepsPanel.tsx
        └── TestResultsPanel.tsx
```

---

## 5. Entegrasyon Yöntemleri

Norn 5 farklı yöntemle kullanılabilir:

1. **Manuel Hook** — `NornHook` ile doğrudan entegrasyon
2. **Proxy Wrapper** — `MonitoredAgent` sınıfı
3. **Global Monitoring** — `enable_global_monitoring()` ile tüm agent'ları izleme
4. **Ortam Değişkeni** — `NORN_AUTO_ENABLE=true` ile sıfır kod değişikliği
5. **Swarm** — `swarm_id` ile multi-agent pipeline izleme

---

## 6. Veri Akışı

```
1. Agent Import (GitHub/ZIP)
   ↓
2. AST Discovery (araçlar, bağımlılıklar, güvenlik)
   ↓
3. Bağımlılık Kurulumu (pip install)
   ↓
4. Akıllı Görev Üretimi (Nova Lite)
   ↓
5. Agent Çalıştırma + NornHook enjeksiyonu
   ↓
6. Her Step: Döngü algılama → AI relevance/security scoring → WebSocket broadcast
   ↓
7. Oturum Sonu: AI değerlendirme → JSON dosya kayıt → Dashboard güncelleme
```
