# Source ingestion Task 7 report

## Status

Complete. Content extraction, original-language identification, URL canonicalization, and
normalized-body SHA-256 fingerprinting are implemented within the Task 7 file boundary.

## TDD evidence

- Baseline before Task 7: `python -m pytest` -> `218 passed`.
- RED: `python -m pytest tests/unit/test_content_extraction.py
  tests/unit/test_deduplication.py -v` -> exit 1 with the two expected missing-module import
  errors for `commerce_agent.ingestion.extract` and `commerce_agent.ingestion.dedupe`.
- First GREEN attempt collected 24 tests; 23 passed and the blank-content test failed because
  Trafilatura fallback retained navigation-only text.
- The extractor now supplies explicit prune XPath rules for navigation, scripts, styles,
  headers, footers, and asides. The focused rerun passed all 24 tests.

## Implemented behavior

- Trafilatura 2.1 extracts article bodies while configured `article_selector` values explicitly
  override automatic extraction.
- Feed-provided plain text is accepted directly. Collected title, author, and publication time
  take precedence over safe HTML metadata extraction.
- Bad publication timestamps return `None` rather than failing an item.
- Text normalization applies Unicode NFC, normalizes line endings and horizontal whitespace,
  and preserves paragraph boundaries and original-language content. Empty normalized bodies are
  rejected with the stable `blank_content` extraction code.
- `LanguageDetector` is a small injected protocol. The offline Lingua implementation loads only
  Chinese, English, and Russian, keeps a 0.75 confidence floor, and returns `und` for short,
  non-linguistic, mixed-script ambiguous, or below-threshold text. It performs no network or model
  download operation.
- URL identity lowercases scheme/host, applies non-transitional IDNA2008-compatible processing,
  removes default ports and fragments, removes only an allowlist of known tracking keys, retains
  and stably sorts business query parameters, and normalizes Unicode paths without conflating an
  encoded slash with a path separator.
- Content and content-group hashes are SHA-256 over normalized UTF-8 text. Equal bodies at
  different canonical URLs therefore retain distinct URL identities while sharing a group hash.

## Model boundary

No existing model fields were changed. `CollectedItem` already carries source body and metadata;
`ExtractedDocument` already carries canonical URL, normalized body, language/confidence, and
publication metadata. Hashing remains a separate Task 7 utility for Task 8 to combine with its
existing `PersistableDocument` hash fields.

## Verification

- Focused: `24 passed`.
- Full suite: `242 passed in 4.54s`.
- `python -m ruff check .`: passed.
- `python -m compileall -q src tests`: passed.
- `git diff --check`: passed before staging; staged diff and secret scan are recorded at commit.

## Concerns

- Language detection intentionally returns `und` for fewer than 20 alphabetic characters or when
  multiple supported scripts each account for at least 20 percent of detected script letters.
  This conservative policy favors avoiding incorrect language labels for short or mixed content.
- Explicit selectors support the same practical tag/id/class descendant subset currently used by
  the source registry; unsupported selectors fail with `invalid_selector` rather than silently
  falling back to unrelated page text.
