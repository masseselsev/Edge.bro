# Upgrade notes

Migration steps required when pulling specific changes. Newest first.

---

## borg-server SSH host keys move to a volume

**Applies to:** installations that pull `fix(borg): keep the server SSH host key stable across rebuilds` or later.

### Why

The borg server's SSH host keys were generated while the Docker image was
built, so any uncached rebuild gave the server a new identity. Nodes record
that fingerprint in their `known_hosts` on first contact, so after a rebuild
they meet a different key than the one on record and refuse to upload
archives:

```
WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!
Host key verification failed.
```

The keys now live on the named volume `edge-bro_borg-hostkeys`, so rebuilding
no longer changes the server's identity.

### Choose a path

**Path A — keep the current identity.** Use this if nodes already back up
successfully, and the current containers are still running. Nothing on the
nodes has to change.

Run on the orchestrator host, **before** pulling:

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

**Path B — accept one final fingerprint change.** Use this on a fresh install,
or when the original keys are already lost.

```bash
git pull
docker compose up -d --build borg-server
```

Then, on **every node that has backed up before**, drop the stale record:

```bash
ssh-keygen -R '[<orchestrator-ip>]:12345'   # bracket form: borg-server is on port 12345
ssh-keygen -R <orchestrator-ip>             # also clear any plain-port entry
```

The next backup re-records the new fingerprint, and it stays stable from then
on.

### Verify

```bash
# What the entrypoint adopted or generated
docker compose logs borg-server | grep -A4 'host key fingerprints'

# What the server actually serves
ssh-keyscan -p 12345 -t ed25519 <orchestrator-ip> | ssh-keygen -lf -
```

Both must report the same ED25519 fingerprint. To prove the fix holds:

```bash
docker compose build --no-cache borg-server
docker compose up -d borg-server
ssh-keyscan -p 12345 -t ed25519 <orchestrator-ip> | ssh-keygen -lf -
```

The fingerprint must be unchanged. The key inside the image will have changed —
that is expected, and is exactly the failure this migration removes.

### Never run `docker compose down -v`

`-v` deletes the named volumes, including `edge-bro_borg-hostkeys` and
`edge-bro_ssh-keys`. That regenerates both the server's host key and the
orchestrator's own key, which breaks every node's `known_hosts` **and** leaves
a dead orchestrator key authorized on every node until each is re-bootstrapped.

`docker compose down` without `-v` is safe; volumes survive.
