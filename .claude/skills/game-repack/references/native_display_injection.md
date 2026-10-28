# Native Display-Layer Injection Fallback

Read this reference only for native games after direct resource or binary
replacement is unsafe or ineffective. It does not apply to the normal Unity,
.NET/IL2CPP, or RPG Maker repack paths.

## Decision order

1. Prefer replacing an understood external or embedded resource while preserving
   its format, encoding, offsets, and lookup keys.
2. Patch an in-place string only when its allocation and all references are
   understood and the translation fits without shifting later data.
3. If visible names also serve as internal IDs, keep the internal English value
   and translate only the display copy.
4. Use an offline runtime display hook only when the earlier choices are unsafe
   or do not affect the parsed runtime text.

## Hook before destructive formatting

The final draw call may receive individual words, wrapped lines, style fragments,
or glyphs. Do not build permanent word-by-word translation exceptions.

- Capture a native call stack from representative untranslated fragments.
- Trace upward to the earliest shared routine that still receives the complete
  string immediately before tokenization, style parsing, or word wrapping.
- Replace the complete string there and let the game perform its normal layout.
- Keep the final draw hook only as a fallback for short labels and UI names.
- Expect separate pipelines for message logs, dialogs, confirmation panels,
  descriptions, and other UI families. Hook shared pre-format entry points, not
  individual scenes.

## Keep translation offline and unified

- Load translations from the aligned `{gamename}_text.csv` and
  `{gamename}_text_translated.csv` pair.
- Runtime hooks must perform local lookup only; no network translation.
- Runtime unmatched logs are coverage diagnostics, not a second translation
  database.
- Derive predictable composite strings from static templates/components instead
  of collecting every displayed combination.

## Preserve layout and game logic

- Retain internal IDs, resource keys, script commands, placeholders, markup, and
  save-facing values in their original form.
- For engines that wrap only at ASCII spaces, add controlled break opportunities
  to translated CJK text before the native wrapper runs; do not split markup or
  placeholders.
- Test startup, containers/inventory, shops, saves, quests, long paragraphs,
  confirmation dialogs, and dynamic text. A successful title-screen test is not
  sufficient.
