# Solana Snapshot GPA

`solana-snapshot-gpa` — это консольная утилита на Rust для извлечения данных аккаунтов напрямую из архивов снепшотов Solana. Она считывает снепшот с диска или по HTTP, применяет фильтры в стиле `getProgramAccounts` и выводит подходящие аккаунты в CSV для анализа, дедупликации или загрузки в другие системы.

## Зачем нужна эта утилита?

Валидаторы Solana публикуют архивы снепшотов (`snapshot-<slot>-<hash>.tar.zst`), которые содержат все аккаунты на конкретном слоте. RPC-узел хранит историю ограниченное время, поэтому получить старые состояния сложно. `solana-snapshot-gpa` позволяет выполнять запросы, похожие на GPA, по историческому снепшоту без развёртывания полного валидатора.

Основные возможности:
- Потоковая обработка снепшотов с диска **или по HTTP** без предварительной загрузки целого архива.
- Фильтрация по публичным ключам, владельцам, размеру аккаунта и условиям memcmp (hex, base58 или файл со списком 32-байтных ключей).
- Вывод записей append-vec в CSV (при необходимости без заголовка).
- Небольшой бинарник, построенный поверх [`solana-snapshot-etl`](https://github.com/riptl/solana-snapshot-etl).

## Быстрый старт

### Необходимые компоненты
- Среда разработки Rust (edition 2021), устанавливается через [rustup](https://rustup.rs/).
- Доступ к архиву снепшота (`.tar.zst`) или к URL с таким архивом.

### Сборка из исходников
```bash
git clone https://github.com/ELGReeND/solana-snapshot-gpa
cd solana-snapshot-gpa
cargo +1.85.1 build --release
```

Готовый бинарник будет в `target/release/solana-snapshot-gpa`.

## Использование CLI

```
solana-snapshot-gpa [ОПЦИИ] <SOURCE>
```

`SOURCE` — путь к локальному архиву снепшота или `http(s)://`-ссылка для потокового чтения.

### Опции
- `-p, --pubkey <PUBKEY>` — одна или несколько публичных ключей через запятую. Параметр можно повторять.
- `--pubkeyfile <PATH>` — файл со списком публичных ключей (по одному в строке, пустые строки игнорируются).
- `-o, --owner <OWNER_OPTS>` — фильтр по владельцу с дополнительными модификаторами:
  - `size:<bytes>` — точная длина данных аккаунта.
  - `memcmp:<base58|0xHEX>@<offset>` — сравнение байт по смещению с указанной последовательностью.
  - `memcmpfile:<path>@<offset>` — сравнение 32 байт по смещению с **любой** строкой из файла (base58 или hex с префиксом `0x`, строго 32 байта).
- `-n, --noheader` — не выводить строку заголовка CSV.

Фильтры объединяются по правилу «ИЛИ»: аккаунт подходит, если совпадает хотя бы один фильтр по публичному ключу или хотя бы один фильтр по владельцу. При отсутствии фильтров выводятся все аккаунты из снепшота.

### Примеры

Извлечь все аккаунты из локального снепшота:
```bash
solana-snapshot-gpa snapshot-139240745-XXXX.tar.zst > all.csv
```

Считать снепшот по HTTPS и отфильтровать список публичных ключей из файла:
```bash
solana-snapshot-gpa --pubkeyfile=pubkeys.txt https://snapshots.solana.com/snapshot.tar.zst > pubkeys.csv
```

Получить аккаунты Token-программы длиной 165 байт, где mint (смещение 32) равен указанному ключу:
```bash
solana-snapshot-gpa \
  --owner=TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA,size:165,memcmp:r21Gamwd9DtyjHeGywsneoQYR39C1VDwrw7tWxHAwh6@32 \
  snapshot.tar.zst > tokens.csv
```

Сравнить значение со множеством 32-байтных ключей из файла:
```bash
solana-snapshot-gpa \
  --owner=TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA,memcmpfile:pubkeys.txt@32 \
  snapshot.tar.zst > matched.csv
```

Комбинированный пример с несколькими фильтрами владельца и публичными ключами:
```bash
solana-snapshot-gpa \
  --owner=Prog1111111111111111111111111111111111,size:44,memcmp:0x8000@40 \
  --owner=AnotherOwner1111111111111111111111111111 \
  --pubkey=SomeKey11111111111111111111111111111111,OtherKey22222222222222222222222222222 \
  snapshot.tar.zst > combined.csv
```

### Формат вывода

CSV содержит записи append-vec с колонками:
1. `pubkey`
2. `owner`
3. `data_len`
4. `lamports`
5. `slot`
6. `id` (append-vec ID)
7. `offset` (смещение внутри append-vec)
8. `write_version`
9. `data` (данные аккаунта в base64)

Используйте `--noheader`, чтобы убрать строку заголовка.

Так как append-vec содержит историю версий записей, один аккаунт может встречаться несколько раз. Самая свежая запись имеет максимальное значение `write_version`.

#### Как оставить только самую свежую версию
```bash
solana-snapshot-gpa --owner=<...> snapshot.tar.zst > result.csv
# оставить запись с максимальным write_version
tail -n +2 result.csv | sort -t, -k8,8nr | awk -F, '!seen[$1]++' > result.latest.csv
```

#### Подготовка данных для `solana-test-validator`
```bash
solana-snapshot-gpa --owner=<...> snapshot.tar.zst > result.csv
# свежая версия для каждого pubkey
tail -n +2 result.csv | sort -t, -k8,8nr | awk -F, '!seen[$1]++' > result.latest.csv
# конвертация в JSON
mkdir -p accounts
awk -F, -v out="accounts" '{filename=out"/"$1".json"; print "{\"pubkey\":\""$1"\",\"account\":{\"lamports\":"$4",\"data\":[\""$9"\",\"base64\"],\"owner\":\""$2"\",\"executable\":false,\"rentEpoch\":0}}" > filename; close(filename)}' result.latest.csv
solana-test-validator --account-dir accounts --reset
```

## Пример рабочего процесса

В репозитории есть скрипт для аккаунтов Whirlpool: [`example/create-whirlpool-snapshot.sh`](example/create-whirlpool-snapshot.sh). Он показывает, как:
- Извлечь все аккаунты Whirlpool.
- Определить аккаунты позиции по размеру данных и получить их по списку pubkey.
- Дедуплицировать записи по `write_version`.
- Отфильтровать закрытые аккаунты и упаковать результаты.

Используйте его как шаблон для собственных пайплайнов.

## Устранение неполадок
- **`Invalid owner filter syntax`** — сначала указывается pubkey владельца, затем через запятую опции (`OWNER,size:165,memcmp:0x00@0`).
- **`Invalid memcmp file`** — строки в файле `memcmpfile` должны содержать ровно 32 байта (base58 или hex с `0x`), пустые строки игнорируются.
- **Ошибки `UnexpectedAppendVec`** — убедитесь, что архив снепшота не повреждён и соответствует версии Solana.

## Лицензия

Проект распространяется под лицензией [Apache 2.0](LICENSE.md). Часть логики извлечения снепшотов адаптирована из [`solana-snapshot-etl`](https://github.com/riptl/solana-snapshot-etl).
