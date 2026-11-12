/**
 * Bitset for ASCII characters that need to be escaped in XML.
 */
export declare const XML_BITSET_VALUE = 1342177476;
/**
 * Matches exactly the characters `encodeXML` escapes: the five XML special
 * characters plus every non-ASCII code unit (lone surrogates included — no
 * `u` flag). Kept in sync with `XML_BITSET_VALUE`.
 *
 * Shared with `encodeNonAsciiHTML` in `encode.ts`. Because the regex is
 * stateful (`g` flag), every call site must set `lastIndex` before use.
 */
export declare const xmlEncodeRegex: RegExp;
/**
 * Encodes all non-ASCII characters, as well as characters not valid in XML
 * documents using XML entities.
 *
 * If a character has no equivalent entity, a numeric decimal reference
 * (eg. `&#252;`) will be used.
 * @param input Input string to encode.
 */
export declare function encodeXML(input: string): string;
/**
 * Encodes all non-ASCII characters, as well as characters not valid in XML
 * documents using numeric decimal reference (eg. `&#252;`).
 *
 * Have a look at `escapeUTF8` if you want a more concise output at the expense
 * of reduced transportability.
 * @param data String to escape.
 */
export declare const escape: typeof encodeXML;
/**
 * Encodes all characters not valid in XML documents using XML entities.
 *
 * Note that the output will be character-set dependent.
 * @param data String to escape.
 */
export declare function escapeUTF8(data: string): string;
/**
 * Encodes all characters that have to be escaped in HTML attributes,
 * following {@link https://html.spec.whatwg.org/multipage/parsing.html#escapingString}.
 * @param data String to escape.
 */
export declare function escapeAttribute(data: string): string;
/**
 * Encodes all characters that have to be escaped in HTML text,
 * following {@link https://html.spec.whatwg.org/multipage/parsing.html#escapingString}.
 * @param data String to escape.
 */
export declare function escapeText(data: string): string;
//# sourceMappingURL=escape.d.ts.map