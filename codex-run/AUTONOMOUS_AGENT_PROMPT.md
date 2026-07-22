# LLM Council Autonomous Codex Objective

## Mission

Work continuously and safely on the `llm-council` repository. Each Codex run must complete **one bounded, high-value iteration**, verify it, record state, and exit. The surrounding PowerShell supervisor will start the next run automatically.

Do not ask the user questions during unattended work. For minor ambiguity, choose the safest backward-compatible option. If blocked, document the blocker and move to another safe task. Never deploy, merge to `master`, force-push, buy services, expose secrets, or modify `.env`.

## Current repository context

- Repository: `C:\Users\Pala\Documents\llm-council`
- Protected branch: `master`
- Working/autonomous branch: keep the current non-master feature branch, or create an autonomous child branch if instructed by the supervisor.
- Existing architecture: Python/FastAPI backend, React/Vite frontend, OpenRouter-backed multi-model council.
- Preserve the three-stage council:
  1. Individual responses
  2. Anonymous peer evaluation
  3. Chairman synthesis
- The repository may contain partially completed changes from a paused Codex session. Inspect and continue them; do not blindly restart or discard work.

## Mandatory operating rules for every cycle

1. Read:
   - this file,
   - `codex-run/AUTONOMOUS_STATE.md`,
   - `codex-run/BACKLOG.md`,
   - `git status`,
   - recent commits,
   - the most relevant source and test files.
2. Pick exactly **one** bounded work package that can reasonably be completed and tested in one run.
3. Prefer, in order:
   - finishing partially implemented code,
   - fixing reproducible failures,
   - adding missing regression tests,
   - completing the current phase,
   - then moving to the next gated phase.
4. Do not make speculative rewrites. Preserve compatibility.
5. Use mocks/fakes for provider tests. Do not spend money or run large batches of real API calls.
6. Never read, print, commit, or expose secrets. Do not commit `.env`, API keys, conversation data, `node_modules`, build output, or logs.
7. Run focused tests during development, then the required phase checks.
8. If tests pass and the change is coherent:
   - update `codex-run/AUTONOMOUS_STATE.md`,
   - update `codex-run/BACKLOG.md`,
   - update relevant docs,
   - create one descriptive Git commit.
9. If tests fail:
   - keep the changes only when they are useful for the next cycle,
   - record exact failures and the next repair step,
   - do not falsely mark the task complete.
10. Never use:
    - `git reset --hard`
    - force push
    - history rewriting
    - destructive cleanup outside generated/cache files
    - `--dangerously-bypass-approvals-and-sandbox`
11. Once all gated roadmap items are complete, switch to review/evaluation mode. Do not invent feature churn. Run audits, improve tests/docs, and change production code only for a reproducible issue.
12. End every run with a concise structured status matching the supplied output schema.

## Phase gates

### Phase 0 — Complete the interrupted provider parsing and multilingual quality task

The exact user-supplied task is reproduced below. Treat it as the authoritative acceptance criteria for Phase 0. It originally requested no commit because it was used interactively; in this autonomous workflow, you **may commit only after the relevant tests pass**.

--- BEGIN ORIGINAL TASK ---

Mevcut llm-council projesindeki gerçek manuel testte bulunan OpenRouter response parsing, çok dilli cevap üretimi ve kalite kapısı regresyonlarını düzelt.

Araştırma sistemi ekleme. Henüz yeni bağımsız özellik geliştirme.

GERÇEK TEST SONUCU

Kullanıcı Türkçe olarak şu soruyu sordu:

“Türkiye'de bir lise öğrencisinin yapay zekâ alanında kendini geliştirmesi için uygulanabilir 6 aylık bir plan hazırla. Cevap kısa, gerçekçi ve Türkçe olsun.”

Sonuç:

* Nemotron başarısız oldu.
* Hata mesajı yalnızca `'choices'` olarak gösterildi.
* HTTP kodu yoktu.
* Deneme sayısı 1 olarak kaldı.
* Gemma üç denemeden sonra HTTP 429 ile başarısız oldu.
* GPT-OSS başarılı kabul edildi ancak cevapta Türkçe, İngilizce, Almanca ve anlamsız kelimeler karıştı.
* Sistem yalnızca 1 geçerli üye olduğu için doğru biçimde konseyi durdurdu.

Ayrıca sistem yalnızca Türkçe için çalışmamalı. Kullanıcı hangi dilde sorarsa, açıkça başka bir dil istemediği sürece bütün konsey aşamaları aynı dilde cevap vermeli.

1. OPENROUTER RESPONSE PARSING HATASINI DÜZELT

Backend içinde hiçbir yerde aşağıdaki alanlara doğrulama yapmadan doğrudan erişme:

* response["choices"]
* choices[0]
* message
* content
* finish_reason
* usage

OpenRouter cevabı için güvenli bir ayrıştırma katmanı oluştur.

Önerilen yapı:

* OpenRouterRawResponse
* OpenRouterChoice
* OpenRouterMessage
* ModelCallResult
* ModelCallError

Pydantic veya açık kontrollü parser kullan.

Bir cevap ancak şu koşullarda başarılı sayılsın:

* HTTP isteği başarılı
* JSON geçerli
* `choices` alanı liste
* Liste boş değil
* İlk choice içinde message mevcut
* message.content string
* content boş veya yalnızca whitespace değil

Şu durumların her birini ayrı hata türüne dönüştür:

* invalid_json
* empty_response
* missing_choices
* empty_choices
* missing_message
* missing_content
* empty_content
* provider_error_payload
* malformed_response
* network_error
* timeout
* rate_limited
* authentication_error
* model_not_found

Python `KeyError`, `IndexError` veya `'choices'` gibi ham exception metni kullanıcıya gösterilmesin.

Örnek kullanıcı mesajı:

“Model sağlayıcısı beklenen cevap biçimini döndürmedi: choices alanı bulunamadı.”

Hata metadata’sında şunlar bulunsun:

* http_status
* error_type
* error_message
* provider_error_code
* provider_error_message
* attempts
* retryable
* raw_response_preview

`raw_response_preview` en fazla güvenli ve sınırlı uzunlukta olsun. API anahtarı, authorization header veya başka gizli bilgi içermesin.

2. HTTP 200 İÇİNDE ERROR PAYLOAD KONTROLÜ

Bazı sağlayıcı cevapları HTTP 200 olsa bile gövdede şu biçimde hata içerebilir:

{
"error": {
"message": "...",
"code": 429
}
}

JSON gövdesinde `error` alanı varsa bunu başarılı cevap olarak kabul etme.

İç hata koduna göre sınıflandır:

* 429, 502, 503, 504: geçici ve retryable
* 400, 401, 403, 404: varsayılan olarak kalıcı
* bilinmeyen sağlayıcı hataları: kontrollü provider_error

HTTP status ile body içindeki error code çelişiyorsa ikisini de metadata’ya kaydet ve daha anlamlı olan sağlayıcı hata kodunu sınıflandırmada kullan.

3. RETRY VE FALLBACK POLİTİKASI

`missing_choices`, `empty_choices`, `empty_response`, `provider_error_payload` ve geçici upstream bozulmaları tek denemede başarısız olmasın.

Retryable kabul edilecek durumlar:

* 429
* 502
* 503
* 504
* timeout
* network_error
* empty_response
* empty_choices
* geçici provider_error_payload
* malformed upstream response

Kalıcı kabul edilecek durumlar:

* 400
* 401
* 403
* doğrulanmış model_not_found
* geçersiz API anahtarı
* config hatası

Retry sayısı mevcut config üzerinden çalışsın.

Primary model retry sonrasında başarısızsa, koltuk için tanımlı fallback modeller denenebilsin.

Fallback kullanıldığında:

* primary_model
* actual_model
* fallback_used
* fallback_index

arayüz ve metadata’da açıkça gösterilsin.

Fallback listesi boşsa sistem kontrollü biçimde başarısız olsun.

4. BAŞARILI CEVAP TANIMINI SIKILAŞTIR

Bir API çağrısının HTTP olarak tamamlanması cevabın başarılı sayılması için yeterli değildir.

Bir Stage 1 cevabı şu aşamalardan geçsin:

1. API response schema validation
2. Boş içerik kontrolü
3. Truncation kontrolü
4. Hedef dil kontrolü
5. Metin kalite kontrolü
6. Gerekirse tek onarım
7. Nihai success veya failure kararı

Stage 1 `status=completed` yalnızca kullanılabilir nihai cevap varsa verilsin.

Şu durumlar `completed` sayılmasın:

* İçerik boş
* İçerik büyük ölçüde anlamsız
* Yanlış dilde
* Birden fazla ilgisiz alfabe karışımı var
* Onarım sonrasında hâlâ ciddi biçimde bozuk
* Cevap cümle ortasında kesilmiş ve onarım da başarısız
* Sağlayıcı hata metni içerik olarak dönmüş

5. KULLANICININ DİLİNİ GENEL BİÇİMDE BELİRLE

Sistemi Türkçeye sabitleme.

`requested_language` belirleme sırası:

1. Kullanıcı açıkça bir cevap dili belirttiyse onu kullan.
   Örnek:

   * “Türkçe cevapla”
   * “Answer in English”
   * “Réponds en français”
2. Açık dil talebi yoksa son kullanıcı mesajının baskın dilini tespit et.
3. Dil tespiti belirsizse konuşmanın önceki kullanıcı mesajlarının baskın dilini kullan.
4. Hâlâ belirsizse güvenli bir varsayılan kullan.

Yapılandırılmış alanlar:

* requested_language_code
* requested_language_name
* language_source: explicit | detected | conversation | default
* language_confidence

Mümkünse BCP-47 veya ISO 639-1 kodları kullan:

* tr
* en
* de
* fr
* es
* ar
* ru
* zh

Kullanıcı Türkçe sorarsa Türkçe; İngilizce sorarsa İngilizce; Almanca sorarsa Almanca cevap verilsin.

Kullanıcı bir dilde soru sorup açıkça başka dil isterse açık talep öncelikli olsun.

Örnek:

“Bu konuyu bana İngilizce açıkla.”

Bu durumda soru Türkçe olsa bile hedef dil İngilizce olmalı.

6. HEDEF DİLİ BÜTÜN AŞAMALARA TAŞI

Aşağıdaki bütün promptlara hedef dili açık ve güçlü biçimde ekle:

* Stage 1
* Stage 1 truncation repair
* Stage 1 language repair
* Stage 2
* Stage 2 JSON repair
* Chairman synthesis
* Chairman quality review
* Conversation title generation

Prompt örneği:

“Yanıtın tamamını {requested_language_name} dilinde üret. Teknik olarak zorunlu özel isimler dışında başka dillerden kelime, cümle veya açıklama karıştırma.”

Stage 2 JSON anahtarları sabit İngilizce olabilir, ancak açıklama değerleri hedef dilde olmalı.

Örnek:

{
"strengths": "Türkçe açıklama",
"weaknesses": "Türkçe açıklama"
}

Chairman, kaynak Stage 1 cevapları farklı veya bozuk diller içerse bile nihai cevabı hedef dilde üretmeli.

7. ÇOK DİLLİ KALİTE KAPISI

Mevcut yalnızca Türkçe odaklı kontrolü genel bir hedef dil kalite denetimine dönüştür.

Her cevap için:

* expected_language
* detected_primary_language
* detected_languages
* script_distribution
* language_confidence
* quality_status
* quality_issues

alanlarını üret.

Aşağıdaki kalite sorunlarını tespit et:

* Hedef dil dışında uzun cümleler
* Kiril, Çince, Arapça veya başka alfabelerin ilgisiz karışımı
* “ähz”, “Küt Bathrooms”, “MDahin”, “proj dela rpeye” benzeri anlamsız parçalar
* Bozulmuş Markdown tabloları
* Yarım kalmış satırlar
* Art arda gelen anlamsız tokenlar
* Sağlayıcı hata mesajlarının cevap içine karışması
* Soru ile ilgisiz kaynak veya kurs isimleri
* Aşırı İngilizce açıklama karışımı
* Cevabın baskın dilinin hedef dilden farklı olması

Özel isimleri yanlışlıkla cezalandırma:

* Python
* NumPy
* PyTorch
* TensorFlow
* GitHub
* Kaggle
* Coursera
* FastAPI

gibi teknik terimler hedef dil dışı cevap sayılmamalı.

Kalite statüleri:

* passed
* warning
* failed

8. DİL VE KALİTE ONARIMI

`quality_status=failed` veya baskın dil hedef dilden farklıysa tek bir onarım çağrısı yap.

Onarım promptu hedef dile göre dinamik olsun:

“Önceki cevap dil ve metin kalitesi denetiminden geçemedi. Kullanıcının sorusunu ve bütün kısıtlarını koruyarak cevabı baştan yaz. Cevabın tamamı {requested_language_name} dilinde olmalı. Anlamsız kelimeler, yabancı dil karışımı, bozuk tablo ve yarım cümle kullanma.”

Kurallar:

* Önceki bozuk cevabı devam ettirme.
* Baştan yeniden yaz.
* Kullanıcının uzunluk sınırını koru.
* Bir kez onarım yap.
* Onarım cevabını tekrar aynı kalite kapısından geçir.

Onarım başarılıysa:

* was_repaired=true
* repair_reason alanını kaydet
* quality_status=passed veya warning

Onarım başarısızsa:

* status=failed
* error_type=quality_validation_failed
* Bozuk cevap isteğe bağlı olarak debug metadata’da tutulabilir ama başarılı konsey cevabı olarak kullanılmasın.
* Stage 2 ve Chairman promptlarına katılmasın.

Bu testteki GPT-OSS cevabı onarım başarısız olursa başarılı model sayılmamalı.

9. GERÇEKLİK VE ALAKA İÇİN HAFİF KALİTE KONTROLÜ

Araştırma sistemi henüz olmadığı için kapsamlı doğruluk doğrulaması yapma.

Ancak açıkça bozuk veya anlamsız ifadeleri yakala:

* Uydurma kurs isimleri
* Bağlam dışı kelimeler
* Kırık kaynak başlıkları
* Tamamlanmamış öneriler
* “albümünü okuyun” gibi açık bağlam hataları

Bu kontrol, tartışmalı gerçekleri otomatik yanlış saymamalı. Yalnızca bariz metin bozulmalarına odaklanmalı.

10. MODEL DURUMU VE ARAYÜZ

Stage 1 durum kartında hata mesajı şu biçimde gösterilsin:

* Kısa kullanıcı dostu açıklama
* HTTP kodu varsa
* Hata türü
* Deneme sayısı
* Primary model
* Actual model
* Fallback kullanıldı mı

`'choices'` gibi ham exception gösterme.

Örnek:

“Model sağlayıcısının yanıtında choices alanı bulunamadı. İstek 3 kez denendi.”

Dil kalite hatasında:

“Model hedef dilde kullanılabilir bir yanıt üretemedi.”

Onarım yapıldıysa:

“İlk cevap dil/kalite sorunu nedeniyle yeniden oluşturuldu.”

11. KONSEY MİNİMUM KATILIMCI DAVRANIŞI

Yalnızca kalite kapısından geçen cevaplar başarılı model sayısına dahil edilsin.

Örnek:

* Nemotron malformed response
* Gemma HTTP 429
* GPT-OSS bozuk dil ve başarısız repair

Sonuç 0 başarılı üye olmalı.

GPT-OSS repair ile düzgün cevap üretirse sonuç 1 başarılı üye olabilir.

Başarısız veya kalite kapısından geçemeyen cevap başka modelin cevabını devralmamalı.

Bir başarılı model varsa mevcut davranış gibi devam butonu gösterilmemesi doğru.

12. TESTLER

Backend testleri:

* HTTP 200 ancak `choices` yoksa KeyError oluşmuyor.
* Missing choices kontrollü `missing_choices` hatasına dönüşüyor.
* Empty choices kontrollü hata oluyor.
* Message veya content eksikliği kontrollü hata oluyor.
* Body içindeki error payload tespit ediliyor.
* HTTP 200 içindeki error code 429 retryable kabul ediliyor.
* Missing choices retry politikasını çalıştırıyor.
* Retry sonrasında fallback çalışabiliyor.
* Ham `'choices'` exception kullanıcıya gitmiyor.
* Boş içerik başarılı sayılmıyor.
* Türkçe kullanıcı sorusu `tr` olarak tespit ediliyor.
* İngilizce kullanıcı sorusu `en` olarak tespit ediliyor.
* Almanca kullanıcı sorusu `de` olarak tespit ediliyor.
* Açık “İngilizce cevapla” talebi soru dilini override ediyor.
* Hedef dil bütün Stage 1/2/Chairman promptlarına giriyor.
* Türkçe cevapta teknik İngilizce özel isimler yanlışlıkla kalite hatası sayılmıyor.
* Türkçe + Kiril + anlamsız token karışımı failed oluyor.
* İngilizce soruya Türkçe cevap failed oluyor.
* Dil onarımı yalnızca bir kez çalışıyor.
* Başarılı onarım cevabı konsey üyesi olarak kabul ediliyor.
* Başarısız onarım cevabı successful responses listesine girmiyor.
* Kalite kapısından geçmeyen cevap Stage 2 ve Chairman'a gönderilmiyor.
* Minimum katılımcı sayısı yalnızca kaliteli cevaplardan hesaplanıyor.

Frontend testleri:

* `'choices'` yerine kullanıcı dostu hata gösteriliyor.
* Hata türü, deneme sayısı ve HTTP kodu doğru gösteriliyor.
* Dil kalite onarımı bilgisi gösteriliyor.
* Kalite validation failure başarılı sekme olarak render edilmiyor.
* Fallback kullanılan model doğru gösteriliyor.
* Bir başarılı model varsa continue butonu görünmüyor.
* Eski konuşmalardaki ham hata verileri güvenli biçimde normalize ediliyor.

13. KONTROLLER

Araştırma sistemi ekleme.

Mevcut testlerin tamamını koru.

Çalıştır:

uv run pytest
uv run python -m compileall backend

cd frontend
npm test
npm run lint
npm run build

Sonuçta raporla:

* `'choices'` hatasının kesin kod satırı ve kök nedeni
* OpenRouter schema parser mimarisi
* Hangi hataların retryable olduğu
* Dil tespit ve açık dil override kuralları
* Çok dilli kalite kapısının çalışma biçimi
* Değişen dosyalar
* Yeni testler
* Bütün test sonuçları
* Türkçe, İngilizce ve Almanca manuel test adımları

Araştırma sistemi ekleme ve commit oluşturma.


--- END ORIGINAL TASK ---

Additional Phase 0 acceptance conditions:

- Finish any partially edited `backend/openrouter.py`, `backend/language_quality.py`, and `backend/council.py` work before starting unrelated changes.
- Ensure the language requirement is general, not Turkish-only.
- Ensure raw exceptions such as `'choices'` never reach the UI.
- A malformed or unusable response must not count as a successful council member.
- Add/update frontend rendering tests for normalized user-friendly errors.
- Required checks:
  - `uv run pytest`
  - `uv run python -m compileall backend`
  - in `frontend`: `npm test`, `npm run lint`, `npm run build`
- Commit message when complete:
  - `Stabilize provider parsing and multilingual quality`

### Phase 1 — Reliability and deterministic demo mode

Start only after Phase 0 passes.

Goals:

- Add a deterministic mock/demo provider mode that exercises:
  - three successful models,
  - one 429 plus fallback,
  - malformed response,
  - wrong-language response and repair,
  - Stage 2 timeout/invalid JSON,
  - Chairman retry and quality-review fallback.
- Add an automated smoke path for the complete council flow without real API keys.
- Ensure SSE terminal states always resolve and restored conversations normalize interrupted work.
- Add health/readiness checks and clear startup diagnostics.
- Keep provider abstraction minimal and backward compatible.
- Document demo-mode commands in README and `.env.example`.
- Required full checks must pass.
- Commit message:
  - `Add deterministic council demo and smoke coverage`

### Phase 2 — Evidence-based research MVP

Start only after Phases 0 and 1 pass.

Goals:

- Research modes: Off, Auto, Required.
- Provider interface with SearXNG and Tavily adapters.
- Missing provider/API key must degrade gracefully.
- Research planner: at most four non-duplicate queries.
- Source model fields:
  - source_id, title, url, domain, snippet, published_date,
    retrieved_at, query, provider, relevance_score, reliability_score.
- Deduplicate URLs and near-duplicate results; maximum ten sources.
- Prefer primary/official/academic sources through ranking rules.
- Safe page fetcher:
  - HTTP/HTTPS only,
  - block localhost/private/link-local/internal IP ranges,
  - validate redirects,
  - timeout,
  - maximum body size,
  - no file URLs,
  - tests for SSRF protections.
- Build a shared evidence pack with `[S1]`, `[S2]` IDs.
- Pass the same evidence pack to all council members.
- Validate source references and flag nonexistent source IDs.
- Stage 2 must score source support, recency, relevance, and hallucination risk.
- Chairman must not invent current facts outside the evidence pack.
- Persist evidence and source metadata in conversations.
- Add a 30-minute cache keyed by provider, language, and query.
- Frontend:
  - research mode selector,
  - progress states,
  - Sources tab,
  - safe external links,
  - source usage by model.
- Use mocks for tests; do not require live Tavily/SearXNG.
- Update `.env.example` and README.
- Required full checks must pass.
- Commit message:
  - `Add evidence based research workflow`

### Phase 3 — Product hardening

Start only after Phase 2 passes.

Goals:

- Backward compatibility tests for old conversation JSON.
- Error-boundary and reconnect behavior.
- Accessibility and keyboard navigation for stage/source tabs.
- Clear loading, empty, degraded, and blocked states.
- Dependency/security audit without automatic breaking upgrades.
- Ensure logs redact keys and sensitive payloads.
- Improve install/start scripts for Windows PowerShell.
- Add a concise architecture document and manual QA checklist.
- Required full checks must pass.
- Commit message:
  - `Harden council UX compatibility and operations`

### Phase 4 — Stable review loop

After Phases 0–3:

- Do not add major features autonomously.
- Re-run tests and inspect flaky areas.
- Review diffs for security, correctness, data loss, race conditions, and UI state bugs.
- Improve tests and documentation for real findings only.
- Keep `KNOWN_ISSUES.md` and `AUTONOMOUS_REPORT.md` current.
- If no actionable issue exists, report `stable` and recommend a longer sleep before the next cycle.

## Required project-maintained files

Keep these under `codex-run/`:

- `AUTONOMOUS_STATE.md`: current phase, completed work, test status, next priority.
- `BACKLOG.md`: ordered, bounded tasks with acceptance checks.
- `AUTONOMOUS_REPORT.md`: chronological summary of meaningful changes.
- `KNOWN_ISSUES.md`: blockers, external-service limitations, and unresolved risks.

Do not put secrets or full provider payloads in these files.
