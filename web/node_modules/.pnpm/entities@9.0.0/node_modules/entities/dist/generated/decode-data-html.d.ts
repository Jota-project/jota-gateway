/**
 * Serialized decode data; the format is documented in
 * `src/internal/decode-data-format.ts`. `[0]` is one-byte data (all char
 * codes <= 0xFF: header, front-coded name suffixes, per-name metadata,
 * cuckoo choice bits); `[1]` holds the replacement values.
 */
export declare const htmlDecodeData: readonly [string, string];
//# sourceMappingURL=decode-data-html.d.ts.map