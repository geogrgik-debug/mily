# deploy/ — запись на отдельной машине

Полный порядок от пустого VPS до записи, которая переживает перезагрузку и сама
докладывает о себе. Каждый шаг ниже либо прогнан 22.09.2026 через **настоящий
разреженный клон** — такой, какой описан в шаге 2, а не полный — либо помечен
как непроверенный. 22.09 вечером он прошёл целиком и на живой машине записи —
Ubuntu 26.04, Python 3.14.4: служба `active`, отчёт `ok`. Непроверенных шагов
не осталось.

Требования к машине: Linux с systemd, Python 3.11+ (Ubuntu 24.04, 26.04 —
проверена вживую — или Debian 12;
**не** Ubuntu 22.04 — там 3.10), 1 ГБ памяти. Диск: 0.3–0.9 ГБ в сутки на
десять матчей, измерено; 40 ГБ — это 44 дня по верхней границе и четыре месяца
по нижней, на два месяца с запасом нужно 60 ГБ. Часы должны синхронизироваться
по NTP — решающее число проекта это задержка, и метка времени с расстроенных
часов её испортит: `timedatectl` → `System clock synchronized: yes`.

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
# на первый вопрос про отпечаток хоста ответить yes — это же нужно cron в шаге 5
# ожидается: Hi geogrgik-debug/mily! You've successfully authenticated...
```

## 2. Только код записи, без документов проекта

Ветка по умолчанию у репозитория — `main`, и она пустая: README и фото. Вся
работа лежит в ветке разработки, и её надо назвать явно. Без `--branch` шаг 3
упадёт с «No such file: deploy/vps-setup.sh» — раннбук так и был написан до
22.09.

```bash
sudo apt update && sudo apt install -y git curl python3-venv
git clone --depth 1 --filter=blob:none --sparse \
    --branch claude/tennis-prediction-audit-kskn26 \
    git@github.com:geogrgik-debug/mily.git capture
cd capture
git sparse-checkout set --no-cone '/tennis/__init__.py' '/tennis/ingest/**' \
    '/tennis/market/**' '/tennis/markov/**' '/tennis/tests/**' \
    '/deploy/**' '/requirements.txt' '/conftest.py'
```

Проверено: на диске ~580 КБ — рекордер, `market/`, `markov/`, их тесты,
`deploy/`, зависимости, и ничего из `docs/` и `research/`. `tennis/tests/`
нужны: без них `vps-setup.sh` останавливается на шаге тестов с «no tests ran»
(код 5) и не доходит до конца — так было в первой редакции.

Если команду передают через чат или мессенджер, `**`, `__` и `$` в ней могут
пропасть — их принимают за разметку (22.09 так и вышло: `__init__` пришёл как
`init`). Тот же набор без спецсимволов даёт cone-режим; он дополнительно кладёт
файлы из корня (README, START_HERE, HANDOFF, фото), и это ничему не мешает:

```bash
git sparse-checkout set tennis/ingest tennis/market tennis/markov tennis/tests deploy
```

`market/` и `markov/` нужны с коммита `3f695d8`: рекордер выбирает гейм-исходы
для `stakes_subscribe` через `tennis.market.names`, а импорт пакета `market`
тянет `markov`. Без них рекордер не запускается
(`No module named 'tennis.market'`), а `vps-setup.sh` падает на сборе тестов —
найдено при слиянии веток 22.09 тем же разреженным клоном. Этот список путей
сторожит `tennis/tests/test_sparse_checkout.py`: если рекордер начнёт
импортировать что-то за его пределами, покраснеет тест, а не машина записи.

## 3. Окружение

```bash
bash deploy/vps-setup.sh
```

Первой строкой проверяет, достаёт ли машина до фида — ждёт `HTTP 101`. Если
нет, дальше идти бессмысленно: провайдер режет исходящий трафик. Потом venv,
зависимости, классы protobuf, тесты (367 проходят на 23.09; ещё 101 — сверка ядра с
`research/elo_prior.py` — пропускаются, потому что `research/` на эту машину
не попадает) и пустой `data/raw` — единственный каталог, куда службе разрешено
писать. Прогнан с чистого разреженного клона целиком, в последний раз — на
коммите слияния веток 22.09.

## 4. Служба

Шаги 4 и 5 одной командой, без путей и спецсимволов в самой команде:

```bash
bash deploy/install-service.sh
```

Ставит юнит с подставленными путями, запускает службу, добавляет строку отчёта в
crontab (повторный запуск её не дублирует), ждёт 90 с и публикует первый статус.
Проверен против заглушек `systemctl`/`crontab` и копии удалённого репозитория,
а 22.09 — вживую, на машине записи. Ниже то же самое по шагам.

```bash
sudo cp deploy/betboom-capture.service /etc/systemd/system/
sudo sed -i "s#__ROOT__#$(pwd)#; s#__USER__#$(id -un)#" /etc/systemd/system/betboom-capture.service
sudo systemctl daemon-reload
sudo systemctl enable --now betboom-capture
systemctl status betboom-capture --no-pager   # ожидается: active (running)
journalctl -u betboom-capture -f              # раз в минуту строка [hb]
```

Перезапускается всегда, без лимита попыток. Пишет только в `data/`: юнит
закрывает остальную файловую систему (`ProtectHome=read-only`,
`ReadWritePaths=…/data`), поэтому `data/` обязан существовать до первого
запуска — иначе systemd не поднимет службу вовсе (`status=226/NAMESPACE`).
`vps-setup.sh` его создаёт.

Прогнан вживую 22.09 на машине записи (Ubuntu 26.04): служба `active`, отчёт
`ok` — 10 подписок, ноль переподключений. Если `status` показывает не
`active (running)`, присылать вывод обеих команд выше плюс
`journalctl -u betboom-capture -n 50 --no-pager`.

## 5. Отчёт о себе

```bash
systemctl is-active cron    # active; если нет: sudo apt install -y cron
crontab -e
# добавить строку (путь подставить свой):
*/5 * * * * cd /home/USER/capture && bash deploy/heartbeat-push.sh >> /tmp/hb.log 2>&1
```

Раз в пять минут кладёт `status/<hostname>.json` в ветку `capture-status`.
Первый раз запустить руками и посмотреть на вывод:

```bash
bash deploy/heartbeat-push.sh
# последняя строка: pushed <hostname> to capture-status
```

Проверено из такого же мелкого разреженного клона против копии удалённого
репозитория, в обоих состояниях — когда ветка `capture-status` уже есть и когда
её нет: в ветку попадает только `status/<hostname>.json`, рабочий каталог записи
не трогается. Раз в час отдельная сессия читает эту ветку, сравнивает отчёт с
отчётом часовой давности и будит владельца, если запись встала, машина замолчала
или пишет вхолостую (routine `trig_01KZG924NGgJccJgn27NxiRN`, см. `HANDOFF.md`);
задержка обнаружения — до полутора часов.

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

## 7. Вторая контора: 1win

Меритель очерёдности (`tennis.market.lead_lag`) сравнивает конторы только по
записям одной машины. Поэтому 1win пишется здесь же, рядом с BetBoom, своей
службой `onewin-capture`. Код рекордера — `tennis/ingest/onewin/`, он уже входит
в разреженный набор шага 2.

Сначала номер партнёра. Адрес API публичный и записан в юните, а номер хранится
только на машине: одна строка `ONEWIN_PARTNER_ID=<значение>` в
`/etc/mily/onewin.env`, читать которую может только root. В репозиторий и в чат
номер не класть. У владельца он лежит на ноутбуке, в
`data\onewin\partner_id.txt`. На сервер его можно положить одной командой из Git
Bash на ноутбуке, из корня репозитория. Команда выводит только размер файла, а не
сам номер:

```bash
{ printf 'ONEWIN_PARTNER_ID='; head -n 1 data/onewin/partner_id.txt | tr -d '\r\n'; echo; } | ssh root@<IP сервера> 'install -d -m 700 /etc/mily && umask 077 && cat > /etc/mily/onewin.env && wc -c /etc/mily/onewin.env'
```

Потом на сервере:

```bash
bash deploy/install-onewin.sh
```

Скрипт ставит юнит с подставленными путями, запускает службу и через 90 с
показывает журнал. Службу BetBoom и строку отчёта в crontab не трогает. Если файла
с номером нет, он сразу останавливается и пишет, чего не хватает. Сама служба без
этого файла тоже стоит: в юните есть условие `ConditionPathExists`, и systemd
пропускает запуск, не считая его сбоем. Без условия отсутствующий
`EnvironmentFile` был бы сбоем запуска, и `Restart=always` повторял бы его
каждые 10 с.
Проверить: `journalctl -u onewin-capture -n 20 --no-pager`. Сразу после запуска
должна быть строка `[1win] recording...`, через минуту — `[sub] N match(es)`,
через две — `[hb] ... match-odds=...`.

В отчёте машины (`capture-status`) 1win виден в поле `other_providers`: самый
свежий файл, его размер и сколько секунд назад в него писали. Это факты, а не
вердикт. Своих счётчиков у рекордера 1win нет, а тихий файл ночью, когда в лайве
мало матчей, ещё не поломка. Вердикт `ok` по-прежнему выносится только о BetBoom и
только по его файлам: до 24.09 отчёт брал самый свежий файл во всём `data/raw`,
и живой 1win выдал бы мёртвый BetBoom за здоровый.

Объём, по замеру роли «Рынок»: 90 с на 20 матчах дали 115 КБ сжатого, то есть
грубо до ~110 МБ в сутки. Забирать вместе с BetBoom по шагу 6: `provider=1win`
лежит рядом с `provider=betboom`.

Пока 1win пишется с этой машины, в 1win через VPN на ней не заходить. Причина та
же, что и с BetBoom: иначе аккаунт свяжется со сбором котировок по IP.

## Что может пойти не так

| Симптом | Причина | Что делать |
|---|---|---|
| `Лексема "&&" не является допустимым разделителем` | команда вставлена в PowerShell на своём компьютере, а не на сервере | сначала `ssh root@<IP сервера>`, дождаться приглашения `root@…#`, потом вставлять |
| в команде после копирования из чата нет `__`, `**` или `$` | чат принял их за разметку | cone-форма из шага 2 и `bash deploy/install-service.sh` — в них таких символов нет |
| после `git clone` нет `deploy/` | клон без `--branch` попал в пустой `main` | клонировать заново, как в шаге 2 |
| `vps-setup.sh` не даёт `HTTP 101` | провайдер режет исходящий трафик | другой VPS; из Европы фид доступен, проверено |
| `vps-setup.sh`: 9 падений в `test_status.py`, «на диске осталось на 1.1 дня записи» | чекаут старше исправления от 22.09: тесты мерили место во временной папке, а на Ubuntu 26.04 `/tmp` — tmpfs около 1 ГБ | `git pull` и повторить; без обновления — `TMPDIR=/var/tmp bash deploy/vps-setup.sh` |
| `vps-setup.sh`: `no tests ran`, выход с кодом 1 | в разреженном наборе нет `tennis/tests/` | `git sparse-checkout add '/tennis/tests/**'` и повторить |
| `No module named 'tennis.market'` в тестах или в `journalctl` | разреженный набор старше `3f695d8`: нет `market/` и `markov/` | `git sparse-checkout add '/tennis/market/**' '/tennis/markov/**'` и повторить шаг 3 |
| `systemctl status` → `status=226/NAMESPACE` | нет каталога `data/` или неверный `__ROOT__` в юните | `mkdir -p data/raw`, сверить пути в `/etc/systemd/system/betboom-capture.service`, `daemon-reload`, `restart` |
| `[hb] ... 0 subscribed` при растущем файле | подписки потеряны после обрыва | должно чиниться само (переподписка после реконнекта); если нет — `systemctl restart betboom-capture` и присылать `journalctl` |
| в отчёте `reconnects` растёт сотнями, `subscribed: 0`, в `last_disconnect` — `3010 ... Access rejected` | фид отказывает сессиям на своей стороне (23.09 — четверть часа) | на машине ничего не делать: рекордер ждёт до минуты между попытками и сам подпишется, когда пустят. Дольше часа — проверить с другого адреса |
| `reconnects` +200 за 5 минут, поля `last_disconnect` в отчёте нет | код старше `f35626c`: пауза сбрасывалась на каждом рукопожатии | `git pull && systemctl restart betboom-capture` |
| `reconnects` +3…10 за час, в `last_disconnect` — `no close frame received or sent` или `did not receive a valid HTTP response`, котировки при этом идут | фид рвёт сессии на своей стороне: 23.09 с 15:20 МСК, у второго адреса в те же минуты | на машине ничего не делать: рекордер переподключается сам. Сколько секунд без котировок стоил последний обрыв — поле `last_blind_s` в отчёте, все обрывы запуска — `blind_s`. Если фид сразу пускает обратно, это секунды; если после обрыва он ещё отказывает (`did not receive a valid HTTP response`), десятки: 23.09 в 18:13 МСК — пять отказов за 27 с, пауза рекордера дошла до 16 с, `last_blind_s` 46.3 с. Если `blind_s` за час вырос больше чем на минуту — присылать `journalctl -u betboom-capture -n 50 --no-pager` |
| `heartbeat-push.sh` не пушит | у deploy key нет write access | перевыпустить ключ с галочкой |
| `heartbeat-push.sh`: `outside of your sparse-checkout definition` или `could not push after 3 attempts` при живом ключе | старая версия скрипта: worktree унаследовал разреженность, мелкий клон не видел `origin/capture-status` | `git pull` — исправлено 22.09 |
| Много `[sub]` на турниры «Пары» | старая версия кода | `git pull` — парные и симулятор не подписываются с `36cfe9c` |
| `systemctl status onewin-capture` — `inactive (dead)` и `ConditionPathExists=/etc/mily/onewin.env was not met` | нет файла с номером партнёра | положить номер, шаг 7, и повторить `bash deploy/install-onewin.sh` |
| `APP_BUILD` конторы сменился | схема protobuf устарела, поля молча разъехались | пересобрать схему: `tennis/ingest/betboom/extract_schema.py`, рецепт в `docs/TRACK_A_betboom_capture.md` |
