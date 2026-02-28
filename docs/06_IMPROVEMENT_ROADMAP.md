# Norn — İyileştirme Yol Haritası & Öneriler

> **Rapor Tarihi:** 2026-02-28

---

## 🎯 Öncelik 1 — Kritik Düzeltmeler (Hemen)

### ~~1.1 asyncio.create_task() Düzeltmesi~~ ✅ ÇÖZÜLDÜ
**Dosya:** `norn/core/interceptor.py`
**BUG:** BUG-001

`_on_after_tool()` senkron bir callback'tir. `asyncio.create_task()` yerine thread-safe bir çözüm kullanılmalı:

```python
import concurrent.futures

class NornHook(HookProvider):
    def __init__(self, ...):
        ...
        self._eval_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="norn-eval"
        )

    def _on_after_tool(self, event):
        ...
        if self.enable_ai_eval and self.task:
            future = self._eval_executor.submit(
                asyncio.run,
                self._evaluate_step_relevance(step, result_str_full)
            )
            self._pending_futures.append(future)
```

### ~~1.2 ZIP Path Traversal Koruması~~ ✅ ÇÖZÜLDÜ
**Dosya:** `norn/api.py`
**BUG:** BUG-005

```python
import zipfile

def _safe_extract(zip_ref: zipfile.ZipFile, extract_path: Path):
    """ZIP extractall ile path traversal koruması."""
    resolved_base = extract_path.resolve()
    for member in zip_ref.namelist():
        member_path = (extract_path / member).resolve()
        if not str(member_path).startswith(str(resolved_base)):
            raise ValueError(f"Güvensiz ZIP girişi tespit edildi: {member}")
    zip_ref.extractall(extract_path)
```

### ~~1.3 `--break-system-packages` Kaldırma~~ ✅ ÇÖZÜLDÜ (hiç kullanılmıyordu)
**Dosya:** `norn/api.py`, satır 1320

```python
# ÖNCE:
pip_base = [sys.executable, "-m", "pip", "install", "-q", "--break-system-packages"]

# SONRA:
pip_base = [sys.executable, "-m", "pip", "install", "-q", "--user"]
# veya daha iyi: agent başına izole venv oluştur
```

### ~~1.4 WebSocket Auth Ekleme~~ ✅ ÇÖZÜLDÜ (API_KEY kontrolü mevcuttu)
**Dosya:** `norn/api.py`

```python
@app.websocket("/ws/sessions")
async def websocket_sessions(websocket: WebSocket):
    # Auth check
    if API_KEY:
        api_key = websocket.query_params.get("api_key")
        if api_key != API_KEY:
            await websocket.close(code=4001, reason="Unauthorized")
            return
    await manager.connect(websocket)
    ...
```

---

## 🎯 Öncelik 2 — Orta Vadeli İyileştirmeler (1-2 Hafta)

### ~~2.1 os.chdir() Kaldırma~~ ✅ ÇÖZÜLDÜ (`threading.Lock` ile serialize edildi)
**Dosya:** `norn/api.py`

`os.chdir()` yerine agent'a çalışma dizinini ortam değişkeni olarak geçin ve subprocess kullanın:

```python
# os.chdir() kullanmak yerine:
env = {
    **os.environ,
    "NORN_WORKSPACE": str(workspace_dir),
    "PWD": str(workspace_dir),
}
# subprocess.run() kullanırken cwd=workspace_dir zaten set ediliyor
```

### ~~2.2 sys.path Temizleme~~ ✅ ÇÖZÜLDÜ
```python
original_path = sys.path.copy()
original_modules = set(sys.modules.keys())
try:
    sys.path.insert(0, package_root)
    # ... agent yükleme ve çalıştırma
finally:
    sys.path[:] = original_path
    # İthal edilen agent modüllerini temizle
    for mod_name in set(sys.modules.keys()) - original_modules:
        del sys.modules[mod_name]
```

### 2.3 Session Dosya Yazma Tutarlılığı
Tüm session yazma işlemlerini `_atomic_write_json()` ile değiştirin:

```python
# api.py'deki tüm bu kalıpları:
with open(session_file, 'w') as f:
    json.dump(session, f, indent=2)

# Şununla değiştirin:
_atomic_write_json(session_file, session)
```

### 2.4 Registry File Locking
```python
import fcntl

class RegistryManager:
    def __init__(self, registry_file: Path):
        self._path = registry_file
        self._lock = threading.Lock()

    def read(self) -> list:
        with self._lock:
            if not self._path.exists():
                return []
            with open(self._path) as f:
                fcntl.flock(f, fcntl.LOCK_SH)
                try:
                    return json.load(f)
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)

    def write(self, agents: list):
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_json(self._path, agents)
```

### 2.5 AsyncFunctionDef Düzeltmesi
**Dosya:** `norn/utils/agent_discovery.py`

```python
def _find_functions(self, tree: ast.AST) -> List[Dict[str, Any]]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            ...
            functions.append({
                ...
                "is_async": isinstance(node, ast.AsyncFunctionDef)
            })
```

---

## 🎯 Öncelik 3 — Uzun Vadeli İyileştirmeler (1-3 Ay)

### 3.1 api.py Modüler Bölünme

```
norn/
├── api/
│   ├── __init__.py      # FastAPI app creation
│   ├── app.py           # Middleware, CORS, error handling
│   ├── agents.py        # Agent CRUD endpoints
│   ├── sessions.py      # Session CRUD + normalization
│   ├── execution.py     # Background agent execution
│   ├── swarms.py        # Swarm endpoints
│   ├── websocket.py     # WebSocket manager
│   ├── discovery.py     # Agent discovery + import
│   └── storage.py       # File I/O, registry manager
```

### 3.2 SQLite Veritabanı Geçişi

```python
# Session, Step, Issue tabloları
# WAL modu ile concurrent access
# Migration script ile mevcut JSON'ları import

import sqlite3

class SQLiteStore(LogStore):
    def __init__(self, db_path="norn.db"):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()
```

### 3.3 Container-Based Agent Isolation

```python
# Docker ile agent çalıştırma
import docker

client = docker.from_env()
container = client.containers.run(
    "python:3.12-slim",
    command=f"python {main_file}",
    volumes={str(workspace_dir): {"bind": "/workspace", "mode": "rw"}},
    environment={"NORN_WORKSPACE": "/workspace"},
    mem_limit="512m",
    cpu_period=100000,
    cpu_quota=50000,
    network_mode="none",
    detach=True,
)
```

### 3.4 Frontend Type Cleanup

```typescript
// types.ts ve api.ts birleştir
// Tek bir tip sistemi oluştur
// convertSessionData() gereksinimini ortadan kaldır

export interface Session {
  sessionId: string;       // backend: session_id
  agentName: string;       // backend: agent_name
  // ... tüm fieldlar tek yerde
}

// API response'u doğrudan frontend tipine map et
```

### 3.5 Test Altyapısı

```
tests/
├── unit/
│   ├── test_step_analyzer.py
│   ├── test_schemas.py
│   ├── test_agent_discovery.py
│   └── test_audit_logger.py
├── integration/
│   ├── test_api_endpoints.py
│   ├── test_agent_execution.py
│   └── test_websocket.py
└── conftest.py
```

---

## 📊 Özet Tablo

| Öncelik | Görev | Effort | Impact |
|---|---|---|---|
| ~~🔴 P1~~ | ~~asyncio.create_task() fix~~ | ~~2 saat~~ | ✅ ÇÖZÜLDÜ |
| ~~🔴 P1~~ | ~~ZIP path traversal~~ | ~~1 saat~~ | ✅ ÇÖZÜLDÜ |
| ~~🔴 P1~~ | ~~--break-system-packages kaldır~~ | ~~30 dk~~ | ✅ ÇÖZÜLDÜ |
| ~~🔴 P1~~ | ~~WebSocket auth~~ | ~~1 saat~~ | ✅ ÇÖZÜLDÜ |
| ~~🟡 P2~~ | ~~os.chdir() kaldır~~ | ~~3 saat~~ | ✅ ÇÖZÜLDÜ |
| ~~🟡 P2~~ | ~~sys.path temizle~~ | ~~2 saat~~ | ✅ ÇÖZÜLDÜ |
| 🟡 P2 | Atomik session yazma | 1 saat | Veri bütünlüğü |
| 🟡 P2 | Registry file locking | 2 saat | Race condition |
| 🟡 P2 | AsyncFunctionDef fix | 30 dk | Doğru discovery |
| 🟢 P3 | api.py bölme | 1-2 gün | Bakımlanabilirlik |
| 🟢 P3 | SQLite geçişi | 3-5 gün | Performans + ölçeklenme |
| 🟢 P3 | Container isolation | 1 hafta | Güvenlik izolasyonu |
| 🟢 P3 | Test altyapısı | 1 hafta | Kod güvenilirliği |

---

## 💡 Genel Tavsiyeler

1. **api.py çok büyük** (2304 satır) — modüler bölünme en önemli refactoring
2. **Thread-safety** projenin en zayıf noktası — os.chdir, sys.path, registry yazma
3. **Güvenlik** "monitor" modunda bile dikkat gerektiriyor — agent kodu güvenilmez
4. **Frontend-backend tip senkronizasyonu** manuel — OpenAPI spec'ten otomatik code generation düşünülebilir
5. **Test yokluğu** projenin en büyük riski — kritik fonksiyonlar için unit test yazılmalı
