# Инструкции по обновлению

Действия, которые нужно выполнить при обновлении на конкретные изменения.
Новые записи — сверху.

---

## SSH host-ключи borg-server переезжают на том

**Касается:** установок, которые обновляются на коммит
`fix(borg): keep the server SSH host key stable across rebuilds` и новее.

### Зачем

Host-ключи SSH у borg-сервера создавались во время сборки Docker-образа,
поэтому любая пересборка без кеша давала серверу новую личность. Нода
записывает отпечаток сервера в свой `known_hosts` при первом подключении,
поэтому после пересборки она встречает не тот ключ, что у неё на руках, и
отказывается заливать архивы:

```
WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!
Host key verification failed.
```

Теперь ключи лежат на именованном томе `edge-bro_borg-hostkeys`, и пересборка
больше не меняет личность сервера.

### Выберите вариант

**Вариант A — сохранить текущую личность.** Подходит, если ноды уже успешно
делают бэкапы, а текущие контейнеры ещё запущены. На нодах менять ничего не
придётся.

Выполнить на хосте оркестратора **до** обновления:

```bash
mkdir -p /tmp/borg-hostkeys
for t in ed25519 rsa ecdsa; do
  docker cp edge-bro-borg-server-1:/etc/ssh/ssh_host_${t}_key     /tmp/borg-hostkeys/
  docker cp edge-bro-borg-server-1:/etc/ssh/ssh_host_${t}_key.pub /tmp/borg-hostkeys/
done

git pull

docker volume create edge-bro_borg-hostkeys
docker run --rm \
  -v edge-bro_borg-hostkeys:/keys \
  -v /tmp/borg-hostkeys:/src:ro \
  debian:bookworm-slim \
  sh -c 'cp /src/ssh_host_* /keys/ && chmod 600 /keys/*_key && chmod 644 /keys/*.pub'

docker compose up -d --build borg-server
rm -rf /tmp/borg-hostkeys
```

**Вариант B — принять одну последнюю смену отпечатка.** Подходит для чистой
установки или если исходные ключи уже потеряны.

```bash
git pull
docker compose up -d --build borg-server
```

Затем на **каждой ноде, которая уже делала бэкап**, удалить устаревшую запись:

```bash
ssh-keygen -R '[<ip-оркестратора>]:12345'   # borg-server слушает порт 12345
ssh-keygen -R <ip-оркестратора>             # на случай записи без порта
```

Следующий бэкап запишет новый отпечаток, и дальше он уже не меняется.

### Проверка

```bash
# Что entrypoint перенял или сгенерировал
docker compose logs borg-server | grep -A4 'host key fingerprints'

# Что сервер отдаёт на самом деле
ssh-keyscan -p 12345 -t ed25519 <ip-оркестратора> | ssh-keygen -lf -
```

Отпечаток ED25519 должен совпадать. Чтобы убедиться, что фикс держит:

```bash
docker compose build --no-cache borg-server
docker compose up -d borg-server
ssh-keyscan -p 12345 -t ed25519 <ip-оркестратора> | ssh-keygen -lf -
```

Отпечаток обязан остаться прежним. Ключ внутри образа при этом поменяется —
так и должно быть, именно эту поломку миграция и убирает.

### Никогда не запускайте `docker compose down -v`

Ключ `-v` удаляет именованные тома, включая `edge-bro_borg-hostkeys` и
`edge-bro_ssh-keys`. Это пересоздаёт и host-ключ сервера, и собственный ключ
оркестратора: у всех нод ломается `known_hosts`, **и** на каждой ноде остаётся
авторизованным мёртвый ключ оркестратора — до её повторного бутстрапа.

`docker compose down` без `-v` безопасен, тома сохраняются.
