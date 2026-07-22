# Known Issues

- Commit oluşturma mevcut sandbox içinde engelli: cycle 56'da `git add` tekrar denendi ve `.git/index.lock` dosyasını oluşturamadı (`Permission denied`). Stale `.git/index.lock` yok; engel izin seviyesinde. Kod doğrulaması yeşil; yazılabilir `.git` olan ortamda commit atılmalı.
