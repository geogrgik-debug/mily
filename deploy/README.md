# deploy/ — запись на отдельной машине

Полный порядок от пустого VPS до записи, которая переживает перезагрузку и сама
докладывает о себе. Каждый шаг ниже либо прогнан с чистого клона, либо помечен
как непроверенный.

Требования к машине: Linux с systemd, Python 3.11+ (Ubuntu 24.04 или Debian 12;
**не** Ubuntu 22.04 — там 3.10), 1 ГБ памяти, 40 ГБ диска на два месяца записи
(0.3–0.9 ГБ в сутки на десять матчей, измерено).

## 1. Доступ к репозиторию с машины

Репозиторий приватный. Машине нужен свой ключ — **deploy key на этот один
репозиторий**, не личный токен с доступом ко всему аккаунту: машина стоит без
присмотра.

На VPS:

```bash
ssh-keygen -t ed25519 -C "capture-vps" -f ~/.ssh/id_ed25519 -N ""
cat ~/.ssh/id_ed25519.pub
```

Скопировать вывод. На GitHub: репозиторий → **Settings → Deploy keys → Add
deploy key**, вставить, **поставить галочку «Allow write access»** — без неё
машина не сможет публиковать статус (шаг 5). Проверить:

```bash
ssh -T git@github.com
# ожидается: Hi geogrgik-debug/mily! You've successfully authenticated...
```

## 2. Только код записи, без документов проекта

```bash
sudo apt update && sudo apt install -y git python3-venv
git clone --depth 1 --filter=blob:none --sparse git@github.com:geogrgik-debug/mily.git capture
cd capture
git sparse-checkout set --no-cone '/tennis/__init__.py' '/tennis/ingest/**' '/deploy/**' '/requirements.txt' '/conftest.py'
```

Проверено: на диске 340 КБ — рекордер, `deploy/`, зависимости, и ничего из
`docs/`, `research/`, `markov/`, `market/`.

## 3. Окружение

```bash
bash deploy/vps-setup.sh
```

Первой строкой проверяет, достаёт ли машина до фида — ждёт `HTTP 101`. Если
нет, дальше идти бессмысленно: провайдер режет исходящий трафик. Потом venv,
зависимости, классы protobuf, тесты. Прогнан с чистого клона целиком.

## 4. Служба

```bash
sudo cp deploy/betboom-capture.service /etc/systemd/system/
sudo sed -i "s#__ROOT__#$(pwd)#; s#__USER__#$(id -un)#" /etc/systemd/system/betboom-capture.service
sudo systemctl daemon-reload
sudo systemctl enable --now betboom-capture
journalctl -u betboom-capture -f        # раз в минуту строка [hb]
```

Перезапускается всегда, без лимита попыток. Юнит-файл тестами не покрыт —
проверяется первым запуском на месте.

## 5. Отчёт о себе

```bash
crontab -e
# добавить строку (путь подставить свой):
*/5 * * * * cd /home/USER/capture && bash deploy/heartbeat-push.sh >> /tmp/hb.log 2>&1
```

Раз в пять минут кладёт `status/<hostname>.json` в ветку `capture-status`.
Проверено против настоящего удалённого: ветка создаётся сама, в ней только
статус. Раз в два часа отдельная сессия читает эту ветку и будит владельца, если
запись встала или машина замолчала (routine `trig_01KZG924NGgJccJgn27NxiRN`,
см. `HANDOFF.md`).

Ручная проверка с самой машины, в любой момент:

```bash
./.venv/bin/python -m tennis.ingest.status data/raw
```

## 6. Забирать данные

Логи копятся в `data/raw/provider=betboom/date=*/`. В git они не попадают
никогда. Забирать на рабочую машину:

```bash
rsync -avz --progress USER@VPS:/home/USER/capture/data/raw/ ./data/raw/
```

Закрытые дневные файлы можно после копирования удалять с VPS.

## Что может пойти не так

| Симптом | Причина | Что делать |
|---|---|---|
| `vps-setup.sh` не даёт `HTTP 101` | провайдер режет исходящий трафик | другой VPS; из Европы фид доступен, проверено |
| `[hb] ... 0 subscribed` при растущем файле | подписки потеряны после обрыва | должно чиниться само (переподписка после реконнекта); если нет — `systemctl restart betboom-capture` и присылать `journalctl` |
| `heartbeat-push.sh` не пушит | у deploy key нет write access | перевыпустить ключ с галочкой |
| Много `[sub]` на турниры «Пары» | старая версия кода | `git pull` — парные и симулятор не подписываются с `36cfe9c` |
| `APP_BUILD` конторы сменился | схема protobuf устарела, поля молча разъехались | пересобрать схему: `tennis/ingest/betboom/extract_schema.py`, рецепт в `docs/TRACK_A_betboom_capture.md` |
