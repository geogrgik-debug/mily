# mily

Живой прогноз следующего сервисного гейма в теннисе: вероятность удержания и
распределение точного счёта гейма, который ещё не начался.

Исследование и paper trading, не машина для денег — и это вывод из измерений, а
не осторожность. Числа в [`START_HERE.md`](START_HERE.md), раздел 2.

## Начать отсюда

| Документ | Когда читать |
|---|---|
| **[`START_HERE.md`](START_HERE.md)** | **первым делом, если контекста нет.** Что за проект, все измеренные числа, GitHub, установка, карта репозитория, грабли |
| [`HANDOFF.md`](HANDOFF.md) | состояние и план по пунктам |
| [`docs/EXPERIMENT_B_elo_prior_and_process.md`](docs/EXPERIMENT_B_elo_prior_and_process.md) | эксперименты с числами, errata, три внешних ревью |
| [`docs/AUDIT_Tennis_Live_Next_Game_v1.md`](docs/AUDIT_Tennis_Live_Next_Game_v1.md) | аудит исходной концепции, 26 разделов |
| [`docs/TRACK_A_betboom_capture.md`](docs/TRACK_A_betboom_capture.md) | как записывать гейм-рынки |

## Быстрый старт

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m pytest tennis/ -q
```

`tennis/` — продакшн-код, покрытый тестами. `research/` — лабораторный журнал:
скрипты, прогнанные один раз, с прибитыми результатами; не рефакторить.
