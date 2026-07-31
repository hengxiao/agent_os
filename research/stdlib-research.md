# Standard / Common Library Research for agent_os Design

This document surveys how mainstream programming languages partition their
built-in capabilities into a **system/runtime layer** and a **common/standard
library layer**, so we can mirror that structure in `agent_os`.

## 1. Executive summary

Every language below separates three concerns:

1. **Runtime / system primitives**: things that talk to the OS kernel, the
   language VM, or the process itself (file handles, sockets, processes, time,
   memory). These are usually privileged or low-level and live in a top-level
   `system`/`os`/`java.lang`/`System` namespace.
2. **Standard / common library**: portable, composable utilities that every
   program needs but that do not require special privileges (text, dates,
   collections, encoding, hashing, JSON, regular expressions). These live in
   `common`/`std`/`java.util`/`System.*` namespaces.
3. **Third-party / external libraries**: vendor-specific integrations (HTTP
   clients for specific services, cloud SDKs, ML model APIs). These are usually
   outside the standard distribution and are imported as packages.

The key insight for `agent_os`: keep the kernel small (`system.*`), and put
reusable, policy-free, deterministic capabilities into `common.*`. This matches
Python's `os` vs `collections/json/re`, Go's `os`/`syscall` vs `strings/bytes/encoding/json`,
and Rust's `std::fs`/`std::process` vs `std::collections`/`std::hash`.

## 2. Language-by-language survey

### 2.1 Python

Python's standard library is deliberately split into functional groups.

| Layer | Representative modules | Purpose |
|-------|--------------------------|---------|
| **System / runtime** | `os`, `sys`, `io`, `subprocess`, `time`, `signal`, `platform`, `ctypes` | OS interface, interpreter state, process management, low-level I/O |
| **File & path** | `pathlib`, `os.path`, `stat`, `glob`, `fnmatch`, `shutil`, `tempfile` | File-system abstractions, path manipulation, directory operations |
| **Text & data** | `string`, `re`, `difflib`, `textwrap`, `json`, `csv`, `tomllib`, `xml` | Text processing, parsing, serialization |
| **Collections & algorithms** | `collections`, `itertools`, `functools`, `heapq`, `bisect`, `array` | Data structures, iteration, combinatorial utilities |
| **Numeric & math** | `math`, `statistics`, `random`, `decimal`, `fractions`, `numbers` | Arithmetic, statistics, randomness |
| **Crypto & hashing** | `hashlib`, `hmac`, `secrets`, `base64`, `binascii` | Digest, HMAC, secure randomness, encoding |
| **Network** | `socket`, `ssl`, `urllib`, `http`, `asyncio`, `email` | Low-level to medium-level network I/O |
| **Date & time** | `datetime`, `zoneinfo`, `calendar` | Date/time representation, time zones |
| **Development & testing** | `unittest`, `doctest`, `pdb`, `traceback`, `logging` | Debugging, testing, introspection |
| **Concurrency** | `threading`, `multiprocessing`, `concurrent.futures`, `queue` | Threads, processes, futures |

Key design notes from Python:

- `os` and `sys` are the two oldest "system" namespaces; they expose the
  boundary between Python and the operating system.
- `io` is the abstract I/O layer that sits underneath `open()` and file
  objects.
- `json`, `re`, `csv`, and `hashlib` are "common" utilities: they are pure
  (or mostly pure) computations that every program needs.
- The standard library is large (~220 modules), so grouping by domain is
  essential. Python uses module names like `xml.etree.ElementTree` for
  sub-domains, which is exactly the dotted-hierarchy idea we are adopting.

### 2.2 Go

Go's standard library is famous for being small, consistent, and tightly scoped.

| Layer | Representative packages | Purpose |
|-------|-------------------------|---------|
| **System / runtime** | `os`, `os/exec`, `os/signal`, `syscall`, `runtime`, `context`, `log` | OS interface, process management, signals, runtime introspection, context propagation |
| **File & path** | `io`, `io/fs`, `bufio`, `path`, `path/filepath`, `os` (file ops) | Streaming I/O, file system abstraction, path manipulation |
| **Text & data** | `strings`, `bytes`, `regexp`, `encoding/json`, `encoding/csv`, `encoding/base64`, `text/template` | Text processing, parsing, serialization, templating |
| **Collections & algorithms** | `container/heap`, `container/list`, `container/ring`, `sort`, `sync`, `sync/atomic` | Heaps, lists, sorting, synchronization primitives |
| **Numeric & math** | `math`, `math/big`, `math/rand`, `cmp`, `strconv` | Float arithmetic, big integers, randomness, comparisons |
| **Crypto & hashing** | `crypto`, `crypto/sha256`, `crypto/hmac`, `hash`, `hash/crc32` | Cryptographic primitives, hashing |
| **Network** | `net`, `net/http`, `net/url`, `net/smtp`, `crypto/tls` | TCP/UDP, HTTP client/server, URL parsing, TLS |
| **Date & time** | `time` | Time representation, formatting, timers, tickers |
| **Development & testing** | `testing`, `fmt`, `errors`, `reflect`, `log` | Formatting, errors, reflection, unit tests |
| **Concurrency** | `sync`, `sync/atomic`, `context`, `runtime` | Mutexes, channels, goroutine scheduling, atomics |

Key design notes from Go:

- Go splits I/O into `io` (streaming abstractions) and `os` (file descriptors).
- `encoding/*` is a clean sub-tree: JSON, CSV, XML, base64, hex, etc.
- `crypto/*` is also a clean sub-tree under `crypto`.
- `container/*` holds generic data structures.
- Go's `strings`/`bytes` packages are pure string/slice utilities, which maps
  naturally to a `common.text` namespace in agent_os.

### 2.3 Rust

Rust's standard library is layered into `core`, `alloc`, and `std`.

| Layer | Representative modules | Purpose |
|-------|--------------------------|---------|
| **System / runtime** | `std::fs`, `std::process`, `std::env`, `std::os`, `std::thread`, `std::sync`, `std::time` | File system, processes, environment, OS-specific extensions, threading, synchronization |
| **File & path** | `std::path`, `std::io`, `std::fs` | Path types, I/O traits, file operations |
| **Text & data** | `std::str`, `std::string`, `std::fmt`, `std::char` (primitive methods), `std::collections` | String handling, formatting, collections |
| **Collections & algorithms** | `std::collections::HashMap`, `std::collections::BTreeMap`, `std::collections::VecDeque`, `std::collections::HashSet`, `std::cmp` | Maps, sets, queues, comparison traits |
| **Numeric & math** | `std::num`, `std::f32`, `std::f64` (primitive constants) | Numeric types and traits |
| **Crypto & hashing** | `std::hash` (trait only), `std::collections::hash_map` | Hashing abstractions; actual algorithms are in third-party crates |
| **Network** | `std::net` (TCP/UDP only), `std::io` | Low-level networking primitives |
| **Date & time** | `std::time` | Durations, system time, instant |
| **Development & testing** | `std::dbg!`, `std::assert!` macros, `std::backtrace` | Debug macros, backtraces |
| **Concurrency** | `std::thread`, `std::sync`, `std::sync::atomic` | Threads, locks, atomics, channels |

Key design notes from Rust:

- Rust keeps the standard library deliberately small. Advanced hashing,
  serialization, regex, and async runtimes live in the `crates.io` ecosystem.
- `std::collections` is a single sub-tree for maps, sets, queues, and linked
  lists.
- `std::io` defines the `Read`/`Write` traits that the whole ecosystem uses.
- This suggests agent_os should not try to duplicate every algorithm; instead
  provide a well-chosen "prelude" of common tools and leave advanced ones to
  external skill packs.

### 2.4 Java

Java's class library is organized by large packages.

| Layer | Representative packages | Purpose |
|-------|-------------------------|---------|
| **System / runtime** | `java.lang`, `java.lang.reflect`, `java.lang.management`, `java.io` (basic streams), `java.nio` | Core types, reflection, JVM management, low-level I/O, NIO |
| **File & path** | `java.nio.file`, `java.io.File`, `java.io` streams | File system abstraction, path API, file I/O |
| **Text & data** | `java.util.regex`, `java.text`, `java.util.Formatter`, `java.xml` (JAXP) | Regular expressions, formatting, localization, XML |
| **Collections & algorithms** | `java.util` (List, Map, Set, Queue), `java.util.stream`, `java.util.function` | Collections, functional interfaces, streams |
| **Numeric & math** | `java.math`, `java.util.Random`, `java.lang.Math` | Big decimals, big integers, randomness, math functions |
| **Crypto & hashing** | `java.security`, `javax.crypto`, `java.security.MessageDigest` | Cryptographic services, digests, ciphers |
| **Network** | `java.net`, `java.net.http`, `javax.net.ssl` | URL, sockets, HTTP client, TLS |
| **Date & time** | `java.time` (Instant, LocalDate, ZonedDateTime) | Modern date/time API |
| **Development & testing** | `java.util.logging`, `java.lang.StackTraceElement`, `java.lang.instrument` | Logging, stack traces, instrumentation |
| **Concurrency** | `java.util.concurrent`, `java.util.concurrent.atomic`, `java.util.concurrent.locks` | Executors, futures, atomics, locks |

Key design notes from Java:

- `java.lang` is implicit (always imported) and contains the most fundamental
  types: `Object`, `String`, `System`, `Runtime`, `Thread`, `Math`.
- `java.util` is the "common" workhorse package: collections, formatting,
  randomness, date/time (older), regex, scanner.
- `java.io` + `java.nio` handle I/O, with `java.nio.file` being the modern
  path/file API.
- `java.time` is a separate, modern date/time package that was added later.
- This reinforces the idea of a small `system` core and a larger `common` tree.

### 2.5 Node.js / JavaScript

Node.js exposes a set of built-in modules that serve as its "standard library"
for server-side programming.

| Layer | Representative modules | Purpose |
|-------|--------------------------|---------|
| **System / runtime** | `os`, `process`, `child_process`, `cluster`, `timers`, `v8`, `vm`, `worker_threads` | OS info, process management, child processes, timers, VM isolation |
| **File & path** | `fs`, `path`, `stream`, `fs/promises` | File system operations, path manipulation, streams |
| **Text & data** | `util`, `querystring`, `string_decoder`, `url`, `punycode` | Text utilities, URL parsing, string decoding |
| **Collections & algorithms** | `buffer` (binary data), `util` (promisify, inspect) | Binary buffer, utility helpers |
| **Numeric & math** | `crypto` (randomness, PBKDF2), `Number` (global) | Math is mostly global; crypto provides randomness |
| **Crypto & hashing** | `crypto`, `tls` | Hashing, ciphers, TLS |
| **Network** | `http`, `https`, `http2`, `net`, `dgram`, `dns` | HTTP servers/clients, TCP/UDP, DNS |
| **Date & time** | `process.hrtime`, `Date` (global) | Time is mostly global `Date` |
| **Development & testing** | `assert`, `util`, `console`, `diagnostics_channel`, `test` (v18+) | Assertions, logging, diagnostics, test runner |
| **Events & async** | `events`, `async_hooks`, `stream`, `timers` | Event emitters, async hooks, streams, timers |

Key design notes from Node.js:

- Node.js is very I/O-centric. `fs`, `stream`, `net`, `http`, `crypto`, and
  `os` are the most commonly used built-ins.
- There is no rich built-in collection library like Java's `java.util` or
  Python's `collections`. JavaScript's `Array`, `Map`, and `Set` globals cover
  most needs.
- For agent_os, this means `common.text` and `common.web` are important, but
  a deep `common.collections` tree may be less critical unless we target
  data-heavy agent workflows.

### 2.6 .NET

.NET's class library is the canonical example of dotted hierarchical naming.

| Layer | Representative namespaces | Purpose |
|-------|---------------------------|---------|
| **System / runtime** | `System`, `System.Runtime`, `System.Reflection`, `System.Threading`, `System.Environment`, `System.Diagnostics` | Fundamental types, reflection, threading, diagnostics, process info |
| **File & path** | `System.IO` (File, Directory, Path, Stream, StreamReader) | File system and stream abstractions |
| **Text & data** | `System.Text`, `System.Text.Json`, `System.Text.RegularExpressions`, `System.Xml` | Text encoding, JSON, regex, XML |
| **Collections & algorithms** | `System.Collections`, `System.Collections.Generic`, `System.Collections.Concurrent` | Lists, dictionaries, queues, concurrent collections |
| **Numeric & math** | `System.Numerics`, `System.Math` (static class in `System`) | Big integers, vectors, math functions |
| **Crypto & hashing** | `System.Security.Cryptography` (SHA256, HMAC, RSA, AES) | Cryptographic primitives |
| **Network** | `System.Net`, `System.Net.Http`, `System.Net.Sockets`, `System.Net.Mail` | HTTP, sockets, mail, URL |
| **Date & time** | `System.DateTime` (in `System`), `System.TimeZoneInfo` | Date/time types, time zones |
| **Development & testing** | `System.Diagnostics`, `System.Diagnostics.Trace`, `System.Diagnostics.Debug`, `Microsoft.VisualStudio.TestTools` | Tracing, debugging, unit testing |
| **Concurrency** | `System.Threading`, `System.Threading.Tasks`, `System.Threading.Channels` | Threads, tasks, async/await, channels |

Key design notes from .NET:

- .NET is the closest model to our proposed naming: `System.IO.File`,
  `System.Net.Http.HttpClient`, `System.Collections.Generic.List<T>`.
- The `System` namespace is the root for fundamental types and runtime
  services; `System.IO`, `System.Net`, `System.Text` are second-level domains.
- This directly supports our plan: `system.file.read`, `system.net.http_fetch`,
  `system.task.todo_write`, `common.text.summarize`, `common.web.fetch_page`.

## 3. Cross-language patterns

Across all six languages, the same capability clusters appear again and again:

| Functional cluster | Typical names in languages | Proposed agent_os namespace |
|--------------------|----------------------------|----------------------------|
| File system read/write | `os`, `fs`, `System.IO`, `java.nio.file`, `std::fs` | `system.file.*` |
| Directory listing / search | `os.listdir`, `fs.readdir`, `System.IO.Directory`, `std::fs::read_dir` | `system.file.list`, `system.file.search` |
| Process / shell execution | `subprocess`, `os/exec`, `std::process`, `child_process`, `System.Diagnostics.Process` | `system.shell.exec`, `system.process.*` |
| Network HTTP | `urllib`, `http`, `net/http`, `std::net`, `java.net.http`, `System.Net.Http`, `http` | `system.net.http_fetch`, `system.net.http_request` |
| Time / clock | `datetime`, `time`, `std::time`, `java.time`, `System.DateTime` | `system.time.now` |
| Task / scheduling | `sched`, `context`, `System.Threading.Tasks`, `timers` | `system.task.todo_write`, `system.task.todo_update` |
| User interaction | `input`, `System.Console`, `ask_user` | `system.ui.ask_user`, `system.ui.notify_user` |
| Text manipulation | `string`, `re`, `textwrap`, `strings`, `bytes`, `System.Text`, `java.util.regex` | `common.text.*` |
| Data serialization | `json`, `csv`, `xml`, `pickle`, `encoding/json`, `System.Text.Json`, `javax.json` | `common.text.extract_json`, `common.text.csv_to_rows`, `common.web.fetch_page` |
| Collections / data structures | `collections`, `java.util`, `System.Collections.Generic`, `std::collections` | `common.data.*` (future) |
| Hashing / encoding | `hashlib`, `base64`, `crypto/sha256`, `System.Security.Cryptography`, `crypto` | `common.text.hash_digest`, `common.crypto.*` (future) |
| Math / stats | `math`, `statistics`, `math/big`, `java.math`, `System.Numerics` | `common.math.*` (future) |
| Web scraping / fetching | `urllib.request`, `net/http`, `requests` (3rd party), `System.Net.Http` | `common.web.fetch_page`, `common.web.search` |
| Evaluation / testing | `unittest`, `testing`, `assert`, `System.Diagnostics` | `common.eval.judge`, `common.dev.run_tests` |
| Memory / knowledge | `shelve`, `sqlite3`, `pickle` (persistence), external vector DBs | `common.memory.*` |
| Research / retrieval | `urllib`, `requests`, `html.parser`, external search APIs | `common.research.*` |

## 4. Implications for agent_os

Based on the survey, we recommend the following shape for the `system` and
`common` libraries:

### 4.1 `system.*` — kernel/runtime primitives

`system.*` should contain only capabilities that the agent kernel itself
provides and that require elevated trust or OS interaction. They are the
"built-in tools" of the framework.

Proposed sub-domains:

- `system.file.*` — read, write, edit, list, search, stat, watch
- `system.shell.*` — exec, capture output, timeout
- `system.net.*` — http_fetch, http_request, socket (future), DNS (future)
- `system.time.*` — now, sleep, timer, benchmark
- `system.task.*` — todo_write, todo_update, schedule
- `system.ui.*` — ask_user, notify_user, prompt
- `system.skill.*` — skill_search, skill_register, skill_reload
- `system.blob.*` — blob_get, blob_put, blob_list
- `system.python.*` — python_exec (sandboxed code execution)
- `system.process.*` — process management (future, beyond shell)

### 4.2 `common.*` — portable standard library

`common.*` should contain reusable, deterministic, policy-free capabilities
that are useful across many agent tasks. They should be implementable as either
code skills or prompt skills and should not require OS privileges beyond what
the kernel already exposes through `system.*`.

Proposed sub-domains:

- `common.text.*` — summarize, extract_json, template_render, diff, word_count,
  token_estimate, chunk, classify, translate, rewrite, extract, normalize_whitespace,
  slugify, csv_to_rows, rows_to_markdown, hash_digest, citation_check
- `common.web.*` — fetch_page, search, parse_html, extract_links
- `common.memory.*` — extract, reconcile, check, verify
- `common.research.*` — one, iterative, report, source_rank
- `common.dev.*` — run_tests, apply_patch, read_file_smart, summarize_tree
- `common.eval.*` — judge, pairwise_compare, calibrate_judge, progress_track
- `common.data.*` (future) — bm25_score, rrf_merge, retrieval_metrics, deduplicate
- `common.math.*` (future) — mean, stddev, percentile, dot_product, simple_stats
- `common.crypto.*` (future) — hmac, sha256, verify_signature
- `common.format.*` (future) — markdown, yaml, toml helpers

### 4.3 Design principles derived from the survey

1. **Keep the runtime small**. Like Go and Rust, the kernel should expose only
   the primitives that need special privileges. Everything else goes into
   `common.*` as a skill.
2. **Group by domain, not by implementation**. Like .NET's `System.IO` and
   Python's `json`/`csv`, use dotted names that describe the capability domain.
3. **Use second-level namespaces aggressively**. `common.text.*`,
   `common.web.*`, `common.memory.*`, `common.dev.*` are easier to browse than
   a flat list of 50 tools.
4. **Do not duplicate every algorithm**. Rust and Go leave advanced algorithms
   to the ecosystem. agent_os should provide a curated "prelude" and let
   third-party skill packs fill the rest.
5. **Separate I/O from computation**. `system.file.read` performs I/O;
   `common.text.summarize` performs a pure computation. This separation makes
   testing, caching, and permissioning easier.
6. **Mirror the naming of mainstream ecosystems**. This lowers the learning
   curve for developers coming from Python, Go, Rust, Java, Node.js, or .NET.

## 5. Sources

- [Python Standard Library](https://docs.python.org/3/library/index.html)
- [Go Standard Library Packages](https://golang.org/pkg/)
- [Rust Standard Library](https://doc.rust-lang.org/std/)
- [Java SE 17 API](https://docs.oracle.com/en/java/javase/17/docs/api/index.html)
- [Node.js API Docs](https://nodejs.org/docs/latest/api/)
- [.NET Class Library Overview](https://learn.microsoft.com/en-us/dotnet/standard/class-library-overview)
