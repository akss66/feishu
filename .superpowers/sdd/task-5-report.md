# Task 5 Report: Bounded HTTP Client, Domain Limiter, and Snapshot Store

## Status

Implemented the bounded ingestion HTTP client, per-domain limiter, DNS-pinned transport,
and content-addressed snapshot store. The intended atomic commit subject is
`feat: add bounded fetching and snapshot storage`.

## Official API verification

Detected versions:

- Project contract: `httpx>=0.28.1,<1` from `pyproject.toml`.
- Verified installed versions: HTTPX 0.28.1 and httpcore 1.0.9.
- Added direct ingestion dependency `httpcore>=1.0.9,<2` because production code now imports
  its public connection-pool and network-backend APIs directly; relying on HTTPX's transitive
  dependency would leave the production dependency undeclared.

Official sources checked:

- HTTPX async client lifecycle, single scoped/shared client guidance, async streaming,
  `aiter_bytes()`, and mandatory `Response.aclose()` in manual streaming mode:
  https://www.python-httpx.org/async/#opening-and-closing-clients and
  https://www.python-httpx.org/async/#streaming-responses
- HTTPX client-level timeout configuration and connect/read/write/pool timeout model:
  https://www.python-httpx.org/advanced/timeouts/
- HTTPX custom async transport boundary (`httpx.AsyncBaseTransport`):
  https://www.python-httpx.org/advanced/transports/#custom-transports
- HTTPX redirect default and explicit redirect control:
  https://www.python-httpx.org/compatibility/#redirects
- httpcore public async pool with injectable `network_backend`:
  https://www.encode.io/httpcore/async/
- httpcore public custom network backend interface, including its stated use for non-standard
  DNS requirements, and the separation between `connect_tcp()` and
  `start_tls(..., server_hostname=...)`:
  https://www.encode.io/httpcore/network-backends/#custom-network-backends
- httpcore SNI hostname extension semantics:
  https://www.encode.io/httpcore/extensions/#sni_hostname

No documented API conflict was found for HTTPX 0.28.1/httpcore 1.0.9. HTTPX's built-in
`AsyncHTTPTransport` does not expose a `network_backend` constructor argument, so using it would
not close the DNS validation-to-connect TOCTOU gap. The documented custom-transport boundary plus
httpcore's documented network-backend boundary provides the required stable layer.

## DNS rebinding defense

Every initial URL and redirect target is validated by `UrlSafetyPolicy.validate()`. Its normalized
hostname and complete set of validated public IP addresses are attached to the internal request and
bound to the active coroutine with a `ContextVar`. The custom httpcore network backend rejects any
connection that has no matching validation context, rejects non-443 destinations, rechecks that
every supplied destination is a public IP literal, and calls the underlying backend with the IP
literal rather than the hostname. It therefore performs no second DNS lookup.

The httpcore request still contains the original hostname as its origin. httpcore consequently
calls `start_tls()` with that original hostname, preserving TLS SNI and certificate hostname
verification even though `connect_tcp()` receives the pinned IP. Environment proxies are disabled.
The context is task-local, so concurrent hosts cannot replace one another's destination set.

The shared client and pool remain enabled, but idle cross-request connection reuse is deliberately
disabled with the documented `max_keepalive_connections=0` setting. This makes every request open
a connection to that request's newly validated address set, including when the same hostname
changes addresses between validations.

Directed offline tests prove:

- TCP receives the validated IP while TLS receives the original hostname.
- A request without validated addresses is rejected before the delegate backend is called.
- The same origin resolving from IP A to IP B performs distinct TCP connections to A and then B.
- Concurrent origins retain isolated pinned-address contexts.

## HTTP behavior

- One shared `httpx.AsyncClient`, explicit 20-second client timeout, `follow_redirects=False`, and
  deterministic close via async context manager/`aclose()`.
- Global semaphore and lock-protected per-host limiter; at 1 request/second, request starts are at
  least one second apart. Clock and sleeper are injected for deterministic tests.
- Manual handling of 301/302/303/307/308 with safety validation on every hop and a redirect cap.
- Conditional `If-None-Match`/`If-Modified-Since`; 304 is successful with an empty body.
- Streaming decoded bytes with a strict inclusive 10 MiB cap and early `Content-Length` rejection.
- At most three retries after the initial attempt for 429, 5xx, transient network/timeouts, and
  remote protocol disconnects. Backoff is exponential and never shorter than `Retry-After`.
- Ordinary 4xx is not retried; 401/403 receives a compliance-review classification.
- Responses are closed on success, retry, status failure, and size-limit failure.
- Public errors expose stable codes without upstream exception text. Logs contain only the
  policy-redacted URL, status, byte count, and stable error code; no body, query, or headers.

## Snapshot behavior

- SHA-256 is computed over the raw response body.
- Deterministic gzip (`mtime=0`) and path:
  `YYYY/MM/DD/<source-id>/<sha256>.bin.gz`.
- Source IDs use the registry's lowercase letter/digit/hyphen grammar; traversal, separators,
  drive-like paths, dot segments, and ambiguous uppercase IDs are rejected before I/O.
- Writes use a temporary file in the target directory, flush and `fsync`, then atomic
  `Path.replace()` under the store lock. Temporary files are cleaned in `finally`.
- Existing files are decompressed and compared before reuse; corrupt or different content raises
  `hash_path_conflict` and is never overwritten.
- `SnapshotRef` contains only relative path, digest, media type, and byte count. Request URL,
  query, cookies, authorization, API keys, and arbitrary headers are never persisted as metadata.

## TDD evidence

Initial RED:

```text
python -m pytest tests/unit/test_ingestion_http.py tests/unit/test_snapshot_store.py -v
collected 0 items / 2 errors
ModuleNotFoundError: commerce_agent.ingestion.http
```

After the first minimal implementation, focused GREEN reached 27/27. Review then found two missing
error edges. Each received a new failing regression test before its fix:

```text
test_transient_write_error_is_retried: RED with uncaught httpx.WriteError
test_nontransient_httpx_error_is_wrapped_in_stable_classification:
RED with uncaught httpx.LocalProtocolError
```

Both were then made GREEN by covering the HTTPX network/timeout families and adding a stable,
secret-free non-transient transport classification. Final verification evidence is recorded below
after the fresh pre-commit run.

## Review and concerns

- Correctness/security review covered SSRF, redirect validation, ContextVar isolation,
  per-validation connection pinning, resource cleanup, header/query redaction, path traversal,
  and hash-path conflicts.
- Performance remains bounded by the semaphore and 10 MiB body cap. Snapshot compression and disk
  I/O run through `asyncio.to_thread()` so the event loop is not blocked.
- The implementation intentionally uses httpcore's public low-level API. The `<2` upper bound is
  important; a future httpcore major upgrade must rerun the transport contract tests.
- Automated tests use only HTTPX `MockTransport`, fake clocks/sleepers, fake network streams, and
  temporary directories. No real network is used and settings/`.env` are not loaded.

## Final verification

- Focused tests:
  `python -m pytest tests/unit/test_ingestion_http.py tests/unit/test_snapshot_store.py -v`
  -> 29 passed.
- Full test suite: `python -m pytest -o addopts=''` -> 191 passed.
- Ruff: `python -m ruff check .` -> all checks passed.
- Compile check: `python -m compileall -q src` -> exit code 0.
- Diff/secret scan: staged diff check and manual review completed before commit; test-only dummy
  values such as `top-secret-value` are deliberately non-credential fixtures.

## Final review corrections

The final security review identified three Important issues. Each correction followed a separate
RED/GREEN cycle:

1. Same-origin idle connection reuse could keep using IP A after a later validation returned IP B.
   RED showed only A reached `connect_tcp()`. GREEN sets httpcore's documented
   `max_keepalive_connections=0`, retaining the shared client/pool while forcing each request to
   connect to its current pinned address set. Official parameter semantics:
   https://www.encode.io/httpcore/async/#httpcore.AsyncConnectionPool
2. The per-domain limiter updated its start time before waiting for global capacity. RED held both
   semaphore slots with other hosts and showed the two queued same-host requests starting together.
   GREEN acquires the global semaphore first, then performs the lock-protected domain wait
   immediately before `send()`.
3. `Retry-After` HTTP-date used the process clock directly and server values were unbounded. RED
   covered an oversized delay-seconds value plus future and past HTTP-date values. GREEN injects a
   wall clock distinct from the monotonic limiter clock, removes direct clock reads from parsing,
   and clamps both formats to `[0, 60]` seconds before combining them with exponential backoff.

Final-review residual (Minor, intentionally not expanded in this fix): `SnapshotStore` does not
explicitly reject a pre-existing symbolic link at the final hash path. Existing source-ID grammar,
resolved-parent containment, content comparison, and no-overwrite behavior remain intact; explicit
link rejection is deferred as a separate filesystem-hardening change.

Final-review verification is recorded by the independent fix commit:

- HTTP focused: 22 passed.
- Snapshot focused: 11 passed.
- Full suite: 195 passed.
- Ruff: all checks passed.
- Compile check: exit code 0.
- Staged diff/secret scan: completed before the fix commit.
