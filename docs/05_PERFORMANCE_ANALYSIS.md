# Norn — Performans & Ölçeklenebilirlik Analizi

> **Rapor Tarihi:** 2026-02-28

---

## 1. Mevcut Performans Profili

### Darboğazlar

| Bileşen | Sorun | Etki |
|---|---|---|
| **Session Listesi** | Her `/api/sessions` çağrısında tüm JSON dosyaları okunuyor | O(n) disk I/O, n = session sayısı |
| **Audit Log** | Son 50 session dosyası açılıp tüm step'ler taranıyor | Her log görüntülemede yoğun disk I/O |
| **Swarm Listesi** | `_load_all_sessions()` TÜM session'ları yükler | Bellek kullanımı session sayısı ile doğru orantılı |
| **WebSocket Update** | Her 5 saniyede tüm session'lar yeniden yüklenir | Session sayısı arttıkça WebSocket update süresi artar |
| **AI Evaluation** | Her tool step için Bedrock API çağrısı | Yavaş agent'larda API rate limiting riski |
| **Agent Discovery** | Her GitHub import'ta subprocess ile `git clone` | Büyük repo'larda 30-60 saniye |

### Bellek Kullanımı

```
Risk Noktaları:
1. sys.path kirlenmesi (BUG-003) — kümülatif bellek büyümesi
2. _pending_tasks list'i — temizlenmezse büyür
3. atexit handler birikimi — her session yeni handler ekler
4. get_sessions() — tüm session'lar bellekte
```

---

## 2. Dosya Tabanlı Depolama Limitleri

Norn şu anda JSON dosya tabanlı depolama kullanıyor. Bu yaklaşımın limitleri:

| Metrik | Tahmin |
|---|---|
| Maksimum uygun session sayısı | ~500-1000 |
| Maksimum session dosya boyutu | Tek dosya ~1-5MB (çok sayıda step) |
| Disk I/O bottleneck | ~100 eşzamanlı okuma/yazma |
| Race condition riski | Yüksek (>10 eşzamanlı agent) |

### Önerilen Geçiş Yolu

```
Faz 1 (Kısa Vadeli):
├── Session listesi için in-memory cache (TTL: 5s)
├── Registry dosyası için file locking (fcntl.flock)
└── Session index dosyası (sadece metadata)

Faz 2 (Orta Vadeli):
├── SQLite veritabanına geçiş
├── Session tablosu + Step tablosu + Issue tablosu
└── WAL modu ile concurrent read/write

Faz 3 (Uzun Vadeli):
├── PostgreSQL/DynamoDB
├── Redis cache katmanı
└── S3 tabanlı step storage (büyük session'lar)
```

---

## 3. API Performans Sorunları

### `/api/sessions` — O(n) Okuma
```python
def get_sessions(limit: int = 50):
    session_files = sorted(
        SESSIONS_DIR.glob("*.json"),        # Tüm dosyalar listelenir
        key=lambda f: f.stat().st_mtime,    # Her dosya için stat çağrısı
        reverse=True
    )
    for file in session_files[:limit]:
        with open(file) as f:               # limit kadar dosya açılır
            session = json.load(f)           # Tüm JSON parse edilir
            normalized = normalize_session(session)  # + normalization
```

### `/api/audit-logs` — O(n × steps)
```python
def get_audit_logs(limit: int = 200):
    for file in session_files[:50]:          # 50 dosya
        session = json.load(f)              # Her biri tam yüklenir
        for step in session.get("steps", []):  # Her step'ten event çıkar
            events.append(...)
    events.sort(...)                         # Sonuç sıralanır
```

### `/api/stats` — get_sessions() çağırıyor
```python
def get_stats():
    sessions = get_sessions()  # TÜM session'ları yükler sadece 6 sayı hesaplamak için
```

---

## 4. WebSocket Ölçeklenebilirlik

```python
class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            await connection.send_json(message)  # Sıralı gönderim
```

**Sorunlar:**
- Broadcast sıralı — bir yavaş istemci tüm broadcast'i geciktirir
- Her 5 saniyede TÜM session verisi gönderiliyor — delta bazlı güncelleme yok
- Bağlantı sayısı limiti yok

**Öneriler:**
```python
# Paralel broadcast
async def broadcast(self, message: dict):
    tasks = [conn.send_json(message) for conn in self.active_connections]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    disconnected = {conn for conn, result in zip(self.active_connections, results)
                    if isinstance(result, Exception)}
    self.active_connections -= disconnected

# Delta-based updates
# Özet bilgi gönder, istemci detay isterse ayrı fetch etsin
```

---

## 5. AI Evaluation Maliyeti

Her tool call için bir Bedrock API çağrısı yapılıyor:

| Bileşen | Çağrı Sayısı | Tahmini Maliyet |
|---|---|---|
| Step relevance + security | 1 çağrı / tool step | ~$0.001 / step |
| Session evaluation | 1 çağrı / session | ~$0.005 / session |
| Task generation | 1 çağrı / agent import | ~$0.003 / import |

10 step'lik bir session: ~$0.015
100 session/gün: ~$1.50/gün

**Optimizasyon:**
- Batch evaluation: birden fazla step'i tek API çağrısında değerlendir
- Cache: aynı tool+input kombinasyonu için sonucu tekrar kullan
- Threshold: sadece belirli koşullarda AI eval çalıştır (her step'te değil)

---

## 6. Concurrent Agent Execution

```python
thread = threading.Thread(
    target=_execute_agent_background,
    args=(...),
    daemon=True
)
thread.start()
```

**Sorunlar:**
- Thread sayısı sınırı yok — 100 agent aynı anda başlatılabilir
- Her thread `os.chdir()` yapıyor — process-global side effect
- Her thread `sys.path.insert()` yapıyor — kümülatif kirlenme
- Thread hata durumunda sadece log atıyor, yeniden deneme mekanizması yok

**Öneriler:**
- `concurrent.futures.ThreadPoolExecutor(max_workers=5)` kullanın
- `os.chdir()` yerine subprocess isolation
- Agent queue sistemi (en fazla N agent aynı anda)
