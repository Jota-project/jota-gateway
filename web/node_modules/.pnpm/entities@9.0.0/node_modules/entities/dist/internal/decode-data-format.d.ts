/**
 * Constants and helpers shared between the decode-data generator
 * (`scripts/write-decode-map.ts`) and the runtime (`src/decode.ts`).
 *
 * The format and the algorithms (front-coded names, exact 32-bit keys in a
 * two-choice cuckoo table, the length-probe front end, and the measured
 * engineering notes) are documented with references in
 * `scripts/README.md`.
 */
/** Bias added to every meta char so none needs escaping in JS source. */
export declare const META_BIAS = 35;
/** Bias for header and choice-bit chars. */
export declare const HEADER_BIAS = 48;
/** Number of header chars: n (2), suffixes length (2), bucket count (2). */
export declare const HEADER_LENGTH = 6;
/** Choice bits packed per char. */
export declare const CHOICE_BITS_PER_CHAR = 6;
/** Multiplier deriving the first candidate bucket from a key. */
export declare const BUCKET_HASH_1 = 2654435761;
/** Multiplier deriving the second candidate bucket from a key. */
export declare const BUCKET_HASH_2 = 2246822507;
/** Size of the (c0,c1) class table (10-bit index). */
export declare const PAIR_TABLE_SIZE = 1024;
/**
 * Map an alphanumeric char code to 6 bits (1..62); 0 = not alphanumeric.
 * Used for the key's last-character field.
 */
export declare const CHAR_REMAP: Uint8Array;
/**
 * The exact 32-bit key for a name occurring at `nameStart` with `length`
 * chars. Covers the first two and last two characters plus the length;
 * middle characters are verified separately against the names blob.
 *
 * The hot decoder inlines this computation; tests assert both stay in sync
 * by looking every entity up through the public API.
 * @param text Text containing the name.
 * @param nameStart Index of the name's first character.
 * @param length Length of the name.
 */
export declare function exactKey(text: string, nameStart: number, length: number): number;
/**
 * The 10-bit (c0,c1) class index used by the probe front end. The function
 * was chosen by exhaustive search over load-free mixers for minimal
 * candidate-set collisions on the real entity set.
 * @param c0 First char code of the candidate name.
 * @param c1 Second char code.
 */
export declare function pairIndex(c0: number, c1: number): number;
/**
 * First candidate bucket for a key in a table of `buckets` buckets.
 * @param key Exact key.
 * @param buckets Bucket count.
 */
export declare function bucketOne(key: number, buckets: number): number;
/**
 * Second candidate bucket for a key.
 * @param key Exact key.
 * @param buckets Bucket count.
 */
export declare function bucketTwo(key: number, buckets: number): number;
/**
 * Decode data for the HTML entity set, built from the serialized form at
 * module init. The format and the algorithms operating on it are documented
 * in `scripts/README.md`. XML's five entities are matched directly and
 * ship no data.
 */
export interface DecodeData {
    /** Exact 32-bit keys; two-slot buckets, slot index = 2*bucket (+1). */
    keys: Int32Array;
    /** Bucket count for the cuckoo table. */
    buckets: number;
    /** Per-slot word offset in `middles`. */
    slotMidOff: Uint16Array;
    /**
     * Per-slot value location in `values`: offset in bits 2..15, legacy
     * flag in bit 1, and length minus one in bit 0. Replaces a per-slot
     * `string[]`: half the footprint and no
     * per-value heap objects, for a slightly costlier emit (see
     * `emitHtmlValue`).
     */
    slotValue: Uint16Array;
    /** Concatenated replacement values; indexed via `slotValue`. */
    values: string;
    /**
     * Per (c0,c1) class: candidate name lengths. Bits 0-14 = exact lengths
     * 2..16, bit 15 = lengths above 16 exist, bits 16-20 = legacy lengths.
     */
    lengthBits: Uint32Array;
    /** Deduplicated middles, packed as pairs of UTF-16 code units per word. */
    middles: Uint32Array;
}
/**
 * Build the lookup structures from a serialized dataset.
 * @param packed Serialized decode data, see `decode-data-format.ts`.
 */
export declare function initDecodeData(packed: readonly [string, string]): DecodeData;
//# sourceMappingURL=decode-data-format.d.ts.map