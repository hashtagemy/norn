# Norn — Güvenlik Analizi

> **Rapor Tarihi:** 2026-02-28  
> **Kapsam:** Tüm backend Python kodu + API endpoint'leri

---

## 1. Güvenlik Genel Değerlendirmesi

| Kategori | Durum | Notlar |
|---|---|---|
| Kimlik Doğrulama | ✅ Tam | API Key mevcut, WebSocket'te de kontrol var |
| Yetkilendirme | ❌ Yok | Rol bazlı erişim kontrolü yok |
| Girdi Doğrulama | ✅ Var | Path traversal koruması (`_safe_extract`) mevcut |
| Bağımlılık Güvenliği | ✅ İyi | `--user` flag kullanılıyor, `--break-system-packages` yok |
| Veri Güvenliği | ✅ Kısmi | Hassas alanlar (api_key, token, password) maskeleniyor |
| İletişim Güvenliği | ⚠️ Kısmi | CORS konfigüre edilmiş ama geniş |

---

## 2. Kritik Güvenlik Bulguları

### SEC-001: Arbitrary Code Execution (Uzaktan Kod Çalıştırma)

**Risk Seviyesi:** 🔴 KRİTİK  
**Vektör:** GitHub/ZIP import + otomatik çalıştırma

Norn'un temel işlevi gereği, ithal edilen agent'lar doğrudan sunucu prosesinde çalıştırılır:

```python
# api.py satır 1382-1385
spec = importlib.util.spec_from_file_location("agent_module", main_file_path)
agent_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent_module)  # ← UZAKTAN KOD ÇALIŞTIRILIR
```

Ayrıca:
```python
# api.py satır 1320
pip_base = [sys.executable, "-m", "pip", "install", "-q", "--break-system-packages"]
```

**Saldırı Senaryosu:**
1. Saldırgan kötü niyetli bir GitHub deposu oluşturur
2. Norn üzerinden import eder
3. `agent.py` dosyası modül yüklendiğinde otomatik kod çalıştırır
4. Sunucu tam erişim sağlanır

**Azaltma Önerileri:**
- Sandbox ortamda çalıştırma (Docker container, gVisor)
- Agent kodunu çalıştırmadan önce statik analiz
- Dosya sistemi, ağ ve süreç izolasyonu
- `--break-system-packages` bayrağını kaldırma

---

### ~~SEC-002: ZIP Path Traversal~~ ✅ ÇÖZÜLDÜ

**Dosya:** `api.py`
`_safe_extract()` fonksiyonu her üye path'i kontrol ediyor, traversal girişimini reddediyor.

---

### ~~SEC-003: WebSocket Kimlik Doğrulama Bypass~~ ✅ ÇÖZÜLDÜ

**Dosya:** `api.py`
WebSocket endpoint `NORN_API_KEY` kontrol ediyor; eşleşmezse `4001 Unauthorized` ile bağlantıyı kesiyor.

---

### SEC-004: CORS Konfigürasyonu

**Risk Seviyesi:** 🟢 DÜŞÜK (geliştirme modu)

```python
CORS_ORIGINS = os.environ.get("NORN_CORS_ORIGINS", 
    "http://localhost:5173,http://localhost:3000,http://localhost:3001").split(",")
```

CORS, ortam değişkeni ile konfigüre edilebilir. Varsayılan olarak yalnızca localhost'a izin verilir. Ancak production'da yanlış konfigüre edilirse güvenlik riski oluşabilir.

---

### SEC-005: Subprocess Injection Riski

**Risk Seviyesi:** 🟡 ORTA  
**Dosya:** `api.py`, satır 912-917

```python
result = subprocess.run(
    ["git", "clone", "-b", branch, repo_url, str(clone_path)],
    ...
)
```

`repo_url` ve `branch` kullanıcı girdisidir. `subprocess.run()` liste formatında çağrıldığı için shell injection riski düşüktür, ama `git clone` komutunun kendisi post-checkout hook'ları çalıştırabilir.

**Azaltma:** `--config core.hookPath=/dev/null` bayrağı eklenebilir.

---

### ~~SEC-006: Hassas Veri Loglanması~~ ✅ ÇÖZÜLDÜ

**Dosya:** `norn/core/interceptor.py`
`_mask_sensitive()` helper eklendi. `api_key`, `token`, `password`, `secret` ve benzeri field adları `***REDACTED***` olarak maskelenerek StepRecord'a yazılıyor.

---

## 3. Güvenlik Kontrol Matrisi

| Kontrol | Var mı? | Notlar |
|---|---|---|
| API Key Authentication | ✅ | Opsiyonel, ortam değişkeni ile |
| WebSocket Auth | ✅ | API_KEY kontrolü mevcut |
| Rate Limiting | ❌ | Yok |
| Input Validation | ✅ | Path traversal koruması var |
| Path Traversal Protection | ✅ | `_safe_extract()` ile korumalı |
| CSRF Protection | ⚠️ | CORS ile kısmen |
| Sandbox Execution | ❌ | Agent'lar ana proseste çalışır |
| Secret Masking | ✅ | `_mask_sensitive()` ile redact ediliyor |
| Audit Trail | ✅ | JSON dosya tabanlı |
| SSL/TLS | ❓ | Reverse proxy'ye bağlı |
| Dependency Scanning | ⚠️ | `agent_discovery.py` temel kontrol yapar |
| Container Isolation | ❌ | Yok |

---

## 4. Önerilen Güvenlik İyileştirmeleri

### Öncelik 1 (Hemen)
1. ~~ZIP path traversal koruması ekleyin~~ ✅
2. ~~`--break-system-packages` bayrağını kaldırın~~ ✅
3. ~~WebSocket'e auth kontrolü ekleyin~~ ✅

### Öncelik 2 (Kısa Vadeli)
4. Agent çalıştırma için subprocess izolasyonu
5. ~~Hassas veri maskeleme filtresi~~ ✅
6. Rate limiting middleware ekleyin

### Öncelik 3 (Uzun Vadeli)
7. Docker/container tabanlı agent sandbox'ı
8. Rol bazlı erişim kontrolü (RBAC)
9. Agent kodu için statik güvenlik analizi
10. Sensör verisi şifreleme (at-rest)
