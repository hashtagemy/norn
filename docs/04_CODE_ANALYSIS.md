# Norn — Detaylı Kod Analizi

> **Rapor Tarihi:** 2026-02-28  
> **Kapsam:** Tüm backend Python modülleri

---

## 1. `norn/api.py` — FastAPI Sunucusu (2304 satır)

### Genel Değerlendirme
Bu dosya projenin en büyük ve en karmaşık parçasıdır. Tüm REST endpoint'leri, WebSocket yönetimi, agent import/discovery/execution mantığı tek bir dosyada toplanmıştır.

### İyi Yönler
- ✅ `_atomic_write_json()` ile atomik dosya yazma mekanizması
- ✅ Global exception handler ile temiz 500 yanıtları
- ✅ WebSocket connection manager ile düzgün bağlantı yönetimi
- ✅ CORS ortam değişkeni ile konfigüre edilebilir
- ✅ Stale session detection (5 dakikadan uzun aktif session'ları terminated olarak işaretle)
- ✅ Smart GitHub URL parsing (subfolder, branch, default branch detection)
- ✅ Multi-agent discovery (pyproject.toml parsing, AST-based agent detection)
- ✅ `_detect_package_info()` ile package vs single-file agent algılama
- ✅ Factory function desteği (create_agent, make_agent, vb.)

### Sorunlar
- ❌ **God Object**: 2304 satır tek dosyada — bölünmeli (import, execution, sessions, swarms)
- ❌ `_execute_agent_background()` 320 satırlık monolitik fonksiyon
- ❌ `os.chdir()` ve `sys.path.insert()` process-global yan etkiler
- ❌ Bazı session yazma işlemleri atomik değil
- ❌ Registry dosyasında concurrent access koruması yok
- ❌ `subprocess.run()` çağrılarında tutarsız timeout değerleri (15s, 30s, 60s, 120s, 300s)

### Refactoring Önerileri
```
norn/api.py → Bölünmeli:
├── norn/api/app.py          # FastAPI app, middleware, health check
├── norn/api/agents.py       # Agent import, discovery, CRUD
├── norn/api/sessions.py     # Session CRUD, normalization
├── norn/api/execution.py    # Agent execution, background thread
├── norn/api/swarms.py       # Swarm endpoints
├── norn/api/websocket.py    # WebSocket connection manager
└── norn/api/storage.py      # Registry, config dosya I/O
```

---

## 2. `norn/core/interceptor.py` — NornHook (968 satır)

### Genel Değerlendirme
Projenin kalbi — Strands HookProvider implementasyonu. Her tool çağrısını yakalayıp analiz eder.

### İyi Yönler
- ✅ `HookProvider` interface'ini doğru implement ediyor
- ✅ Lazy-loaded evaluator ve shadow browser (performans)
- ✅ Dashboard entegrasyonu (register agent → create session → stream steps → complete)
- ✅ Sağlam `_on_message_added()` ile auto task detection
- ✅ `ai_reasoning` step tipi ile tool-less agent desteği
- ✅ Swarm tracking (swarm_id, swarm_order, handoff_input)
- ✅ Background AI eval thread ile atexit cleanup
- ✅ Config error pattern detection (exchange API, knowledge base, vb.)

### Sorunlar
- ❌ **BUG-001**: `asyncio.create_task()` senkron callback'ten çağrılıyor
- ❌ `_pending_tasks` list'i thread-safe değil (list append/iteration)
- ❌ `_finalize_report()` birden fazla kez çağrılabilir (background eval thread + _on_session_end)
- ⚠️ `atexit.register(bg_thread.join, 15)` — her session için yeni bir atexit handler ekleniyor, temizlenmiyor

### Önemli Akış
```
BeforeInvocationEvent → _on_session_start()
   ↓
MessageAddedEvent → _on_message_added() (auto task detection)
   ↓
BeforeToolCallEvent → _on_before_tool() (loop detection, max steps)
   ↓
AfterToolCallEvent → _on_after_tool() (step recording, AI eval)
   ↓
AfterInvocationEvent → _on_session_end() (finalization, report)
```

---

## 3. `norn/core/step_analyzer.py` — Deterministik Analiz (148 satır)

### Genel Değerlendirme
Temiz, odaklı, iyi yazılmış modül. Döngü algılama ve verimlilik kontrolü yapıyor.

### İyi Yönler
- ✅ Minimal bağımlılık (sadece collections, logging)
- ✅ Deque ile sliding window pattern detection
- ✅ Counter ile tool frequency tracking
- ✅ SSL bypass detection (verify_ssl=False, vb.)
- ✅ `reset()` metodu ile clean session transitions

### Küçük Sorunlar
- ⚠️ `_hash_input()` basit string karşılaştırması kullanıyor — nested dict'lerde sıralama tutarsızlığı olabilir
- ⚠️ `affected_steps` her zaman boş liste — step_id bilgisi geçirilmiyor

### İyileştirme Önerisi
```python
# _hash_input() için daha güvenilir hash:
import hashlib, json
@staticmethod
def _hash_input(tool_name: str, tool_input: dict) -> str:
    canonical = json.dumps(tool_input, sort_keys=True, default=str)
    return f"{tool_name}:{hashlib.md5(canonical.encode()).hexdigest()}"
```

---

## 4. `norn/core/audit_logger.py` — Yapılandırılmış Loglama (212 satır)

### Genel Değerlendirme
İyi tasarlanmış, thread-safe, pluggable storage backend'i ile.

### İyi Yönler
- ✅ `Protocol` ile pluggable storage interface (`LogStore`)
- ✅ `threading.Lock` ile thread-safety
- ✅ `cleanup_old_logs()` ile retention policy
- ✅ Smart merge — mevcut session verilerini koruyarak güncelleme
- ✅ Step deduplication by `step_id`

### Sorunlar
- ⚠️ `write_session()`: İlk okuma + sonra yazma — lock kapsamı yeterli ama dosya sistemi seviyesinde race condition hâlâ mümkün
- ⚠️ `write_session()` artık `_atomic_write_json()` kullanmıyor, doğrudan `json.dump()` (satır 104) — macOS tempfile sorunundan kaynaklanıyor ancak atomikliği kaybediyor

---

## 5. `norn/agents/quality_evaluator.py` — AI Değerlendirme (318 satır)

### Genel Değerlendirme
Amazon Nova Lite ile AI destekli kalite değerlendirmesi. İyi yapılandırılmış prompt mühendisliği.

### İyi Yönler
- ✅ İki katmanlı model stratejisi (hızlı step-level + detaylı session-level)
- ✅ Detaylı scoring guide ile tutarlı AI yanıtları
- ✅ `_parse_json_response()` ile robust JSON parsing (markdown fence handling)
- ✅ Pure-reasoning agent desteği (0 tool call = heuristic scores)
- ✅ Defensive error handling (AI failure → fallback scores)

### Sorunlar
- ⚠️ Fast model ve primary model **aynı model** (`nova-2-lite-v1:0`) — adlandırma yanıltıcı
- ⚠️ `_build_step_context()` sadece son 5 step gösteriyor — uzun session'larda bağlam kaybı
- ⚠️ `_parse_json_response()` nested JSON'da başarısız olabilir (`rfind('}')` en dıştaki kapanışı bulur)

---

## 6. `norn/agents/shadow_browser.py` — Nova Act Doğrulama (206 satır)

### Genel Değerlendirme
Temiz, iyi yapılandırılmış modül. Nova Act ile bağımsız tarayıcı doğrulaması.

### İyi Yönler
- ✅ Optional dependency handling (Nova Act yoksa graceful degradation)
- ✅ Keyword-based security issue detection
- ✅ Standart response format (_unavailable, _error helper'ları)

### Sorunlar
- ⚠️ Güvenlik tespiti sadece keyword tabanlı — sophisticated injection'ları atlayabilir
- ⚠️ `headless=True` her zaman sabit — konfigüre edilemiyor
- ⚠️ Rate limiting yok — çok sayıda URL'yi hızla ziyaret etmek Nova Act API limitlerini aşabilir

---

## 7. `norn/utils/agent_discovery.py` — Kod Analizi (444 satır)

### Genel Değerlendirme
AST tabanlı kapsamlı kod analizi. Tool, import, dependency ve issue detection yapıyor.

### İyi Yönler
- ✅ AST-based analysis (regex'e göre çok daha güvenilir)
- ✅ External tool detection (Agent() kwargs, use_* functions)
- ✅ Local package detection (working directory'de arama)
- ✅ Severity levelları (HIGH, MEDIUM, LOW)

### Sorunlar
- ❌ **BUG-008**: `AsyncFunctionDef` yakalanmıyor
- ❌ **BUG-009**: `_detect_agent_type()` string matching kullanıyor
- ❌ **BUG-010**: `_extract_tool_name()` tip anotasyonu yanlış
- ❌ **BUG-021**: `_find_entry_points()` false positive
- ⚠️ Standard library listesi sadece 6 modül — `re`, `io`, `ast`, `math`, `typing`, `collections`, `functools`, `hashlib`, `uuid`, `logging`, `tempfile`, `shutil`, `subprocess`, `importlib`, `enum`, `copy`, `abc`, `dataclasses`, `traceback` gibi yaygın modüller eksik → yanlış "missing dependency" uyarısı

---

## 8. `norn/utils/agent_runner.py` — Agent Çalıştırma (251 satır)

### Genel Değerlendirme
Agent'ları Norn monitoring ile saran CLI aracı. Ama büyük ölçüde `api.py`'deki `_execute_agent_background()` tarafından bypass ediliyor.

### Sorunlar
- ❌ **BUG-017**: Eski Hook API (`_hooks` attribute) kullanıyor
- ⚠️ Bu modül `api.py`'nin bağımsızlaştığı sonraki sürümlerde muhtemelen kullanılmıyor
- ⚠️ `_load_agent_module()` `api.py`'den `_detect_package_info()` import ediyor — circular dependency riski

---

## 9. `norn/proxy.py` — Global Monitoring (158 satır)

### Genel Değerlendirme
Strands Agent'a otomatik monitoring ekleme mekanizması. Monkey-patching yaklaşımı.

### İyi Yönler
- ✅ `MonitoredAgent` clean drop-in replacement
- ✅ `quality_report` ve `security_score` property'leri
- ✅ Environment variable ile zero-code activation

### Sorunlar
- ⚠️ Module-level side effect: `NORN_AUTO_ENABLE=true` ise import sırasında monkey-patch uygulanıyor
- ⚠️ `enable_global_monitoring()` geri alınamaz — bir kez çağrıldıktan sonra tüm Agent'lar monitored
- ⚠️ `auto_task_detection` system prompt'u task olarak kullanıyor — system prompt genellikle task değildir

---

## 10. `norn/models/schemas.py` — Veri Modelleri (204 satır)

### Genel Değerlendirme
Temiz, iyi yapılandırılmış Pydantic modelleri.

### İyi Yönler
- ✅ Comprehensive enum tanımları
- ✅ `Field(default_factory=...)` ile doğru mutable default handling
- ✅ `ge`/`le` validation constraint'leri
- ✅ Legacy ActionRecord ile geriye uyumluluk

### Küçük Sorunlar
- ⚠️ `SessionReport.task` alanı `Optional[TaskDefinition]` ama `get_session_report()` fonksiyonunda string olarak da atanabiliyor (satır 872 in interceptor.py): `task=self.task.description if self.task else "Unknown task"`
- ⚠️ `TestCase` ve `TestResult` modelleri tanımlanmış ama hiçbir yerde kullanılmıyor — dead code

---

## 11. `norn/utils/aws_config.py` — AWS Konfigürasyon (129 satır)

### Genel Değerlendirme
İki farklı auth metodu destekleyen AWS Bedrock istemci yardımcısı.

### İyi Yönler
- ✅ Bearer token + IAM credentials dual auth
- ✅ `botocore.UNSIGNED` ile temiz bearer token implementasyonu
- ✅ Adaptive retry config
- ✅ Test fonksiyonu (`test_bedrock_connection()`)

### Sorunlar
- ⚠️ Bu modül hiçbir yerde import edilmiyor — `QualityEvaluator` kendi Bedrock bağlantısını Strands model üzerinden yapıyor. Bu modül dead code olabilir veya kullanıcılara helper olarak sunulmuş olabilir.
- ⚠️ Bearer token placeholder'ı (`aws_access_key_id="placeholder"`) bazı AWS SDK sürümlerinde sorun çıkarabilir

---

## 12. Frontend Kod Analizi

### `App.tsx` (389 satır)
- ✅ WebSocket ile real-time updates + polling fallback
- ✅ Health check ile system status monitoring
- ✅ Session data normalization (backend → frontend format)
- ⚠️ `convertSessionData()` her render'da tekrar çalışıyor — memoization gerekli
- ⚠️ `as any` type cast'ler (BUG-019)

### `services/api.ts` (342 satır)
- ✅ Exponential backoff WebSocket reconnection
- ✅ Ping/pong keepalive
- ✅ Clean disconnect handling
- ✅ Content-Type header'ı sadece body olan isteklerde gönderiliyor
- ⚠️ `getSwarms()` ve `getSwarm()` return tipi `any` — proper typing gerekli
- ⚠️ API key header desteği yok — backend auth kullanılıyorsa frontend çalışmaz
