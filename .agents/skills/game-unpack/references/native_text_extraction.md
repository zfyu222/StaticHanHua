# Native Binary Text Extraction Fallback

Read this reference only for native executables when ordinary string-table or
resource extraction is incomplete. It does not change the Unity, .NET/IL2CPP, or
RPG Maker workflows.

## Build one offline corpus

- Merge EXE strings, embedded resources, external data files, scripts, and map
  records into the single required `{gamename}_text.csv`.
- Append newly verified text to the existing corpus; do not create a growing set
  of independent supplemental translation CSVs.
- Keep an optional catalog beside the required CSV when provenance matters. Record
  the source file/resource, address or record identifier, and extraction method.
- Preserve exact source strings, markup, literal escape sequences, and whitespace
  that participates in lookup or formatting.

## Separate display text from keys

Before classifying a visible string as safely replaceable, check whether the same
value is used by scripts, containers, saves, maps, dictionaries, enums, or object
lookups. Record dual-purpose values as internal identifiers even when players see
them. This classification guides repacking; it must not remove the visible source
from the translation corpus.

## Handle generated text

- Extract stable components and templates instead of enumerating every runtime
  combination, for example `"You see " + object_description`.
- Prefer deterministic template/composite rules during repacking.
- Use runtime observation only to measure coverage and locate missing static
  sources. Do not make encountering each scene a prerequisite for translation.

## Diagnose incomplete coverage

When a complete sentence is absent but individual words appear at the final draw
function, trace callers upward to the point where the full string enters a
tokenizer, style parser, or word-wrap routine. Use that point to identify the
actual static source or template and add it to the unified corpus.
