# Codex Autopilot Kit — Windows PowerShell

Bu kit, tek bir uzun Codex oturumu yerine art arda kısa ve sınırlandırılmış `codex exec` turları çalıştırır.

Her tur:

1. Repository ve önceki durum dosyalarını okur.
2. Tek bir öncelikli iş seçer.
3. Kodlar ve test eder.
4. Uygunsa commit oluşturur.
5. Durumu raporlar ve çıkar.
6. PowerShell supervisor yeni turu başlatır.

Böylece bir Codex turu doğal olarak sona erse veya bağlamı daralsa bile bütün süreç durmaz.

## 1. Dosyaları projeye yerleştir

Zip içindeki `codex-run` klasörünü şu klasöre kopyala:

`C:\Users\Pala\Documents\llm-council\codex-run`

`Start-CodexAutopilot.ps1` dosyasını proje köküne kopyala:

`C:\Users\Pala\Documents\llm-council\Start-CodexAutopilot.ps1`

## 2. Mevcut yarım işi güvenli checkpoint yap

PowerShell:

```powershell
cd C:\Users\Pala\Documents\llm-council

git status
git add backend frontend tests pyproject.toml uv.lock
git commit -m "WIP: checkpoint interrupted multilingual provider work"
git push -u origin feature/research-system
```

`AGENTS.md` bu komutla eklenmez.

Commit komutu “nothing to commit” derse sorun değildir.

## 3. Codex ve oturum kontrolü

```powershell
codex --version
codex doctor
```

Gerekirse Codex’e önceden giriş yap. Unattended çalışma sırasında tarayıcı onayı beklememelidir.

## 4. Çalıştır

PowerShell yürütme politikası dosyayı engellerse yalnızca bu çalıştırma için bypass kullan:

```powershell
cd C:\Users\Pala\Documents\llm-council
powershell -ExecutionPolicy Bypass -File .\Start-CodexAutopilot.ps1
```

Doğrulanmış commitleri otomatik olarak feature branch’e push etmesini de istiyorsan:

```powershell
powershell -ExecutionPolicy Bypass -File .\Start-CodexAutopilot.ps1 -AutoPush
```

`master` merge edilmez.

## 5. Durdurma ve duraklatma

Anında durdur:

- PowerShell penceresinde `Ctrl+C`

Bir sonraki güvenli kontrol noktasında durdur:

```powershell
New-Item C:\Users\Pala\Documents\llm-council\codex-run\STOP -ItemType File
```

Duraklat:

```powershell
New-Item C:\Users\Pala\Documents\llm-council\codex-run\PAUSE -ItemType File
```

Devam ettir:

```powershell
Remove-Item C:\Users\Pala\Documents\llm-council\codex-run\PAUSE
```

Yeni çalıştırmadan önce eski STOP dosyasını sil:

```powershell
Remove-Item C:\Users\Pala\Documents\llm-council\codex-run\STOP -ErrorAction SilentlyContinue
```

## 6. Kayıtlar

- Genel log: `codex-run/supervisor.log`
- Her tur: `codex-run/logs/cycle-.../`
- Son bağımsız testler: `codex-run/last-verification.log`
- Durum: `codex-run/AUTONOMOUS_STATE.md`
- Rapor: `codex-run/AUTONOMOUS_REPORT.md`
- Bilinen sorunlar: `codex-run/KNOWN_ISSUES.md`

## Güvenlik

Supervisor yalnızca `--sandbox workspace-write` kullanır. `danger-full-access`, `--yolo`, force push veya master merge kullanmaz.

Bilgisayar ve internet açık kalmalıdır. Script çalışırken Windows’un sistem uykuya geçmesini engellemeye çalışır; kapağı kapatmak veya güç kesintisi yine süreci durdurabilir.

Sonsuz çalışma Codex kullanım kotası tüketir. Yol haritası tamamlanınca prompt, yeni özellik uydurmak yerine uzun aralıklı test/review döngüsüne geçmesini ister.
