/**
 * Decoding mode for named entities.
 */
export declare enum DecodingMode {
    /** Entities in text nodes that can end with any character. */
    Legacy = 0,
    /** Only allow entities terminated with a semicolon. */
    Strict = 1,
    /** Entities in attributes have limitations on ending characters. */
    Attribute = 2
}
/**
 * Decodes an HTML string.
 * @param htmlString The string to decode.
 * @param mode The decoding mode.
 * @returns The decoded string.
 */
export declare function decodeHTML(htmlString: string, mode?: DecodingMode): string;
/**
 * Decodes an HTML string in an attribute.
 * @param htmlAttribute The string to decode.
 * @returns The decoded string.
 */
export declare function decodeHTMLAttribute(htmlAttribute: string): string;
/**
 * Decodes an HTML string, requiring all entities to be terminated by a
 * semicolon.
 * @param htmlString The string to decode.
 * @returns The decoded string.
 */
export declare function decodeHTMLStrict(htmlString: string): string;
/**
 * Decodes an XML string, requiring all entities to be terminated by a
 * semicolon.
 * @param xmlString The string to decode.
 * @returns The decoded string.
 */
export declare function decodeXML(xmlString: string): string;
/**
 * Producers for character reference errors as defined in the HTML spec.
 */
export interface EntityErrorProducer {
    missingSemicolonAfterCharacterReference(): void;
    absenceOfDigitsInNumericCharacterReference(consumedCharacters: number): void;
    /**
     * Validate the accumulated numeric value, before Unicode replacement.
     * Values beyond the JavaScript number range are positive infinity.
     */
    validateNumericCharacterReference(code: number): void;
}
/**
 * Token decoder with support of writing partial entities; the shared base
 * of `HtmlEntityDecoder` and `XmlEntityDecoder`. Numeric entities are
 * identical in both document types and are handled here; named entities are
 * left to the subclasses.
 *
 * The decoder is driven by a tokenizer: after a `&`, write the following
 * input (possibly in chunks). `write` returns the number of characters
 * consumed once the entity is decided, or -1 while more input is needed.
 * Rejection may surface later than strictly possible (the decoder buffers
 * until a terminator or the name-length limit), but emitted code points,
 * consumed counts, and final return values are exact.
 */
declare abstract class EntityDecoderBase {
    /**
     * The function that is called when a codepoint is decoded.
     *
     * For multi-byte named entities, this will be called multiple times,
     * with the second codepoint, and the same `consumed` value.
     * @param codepoint The decoded codepoint.
     * @param consumed The number of bytes consumed by the decoder.
     */
    protected readonly emitCodePoint: (cp: number, consumed: number) => void;
    /** An object that is used to produce errors. */
    protected readonly errors?: EntityErrorProducer | undefined;
    /** The current state of the decoder. */
    protected state: number;
    /** Characters that were consumed while parsing an entity. */
    protected consumed: number;
    /** Accumulated numeric code point, or a packed partial XML name. */
    protected result: number;
    /** The mode in which the decoder is operating. */
    protected decodeMode: DecodingMode;
    /** Replacement rules for numeric character references. */
    protected readonly replaceNumericCodePoint: (codePoint: number) => number;
    constructor(
    /**
     * The function that is called when a codepoint is decoded.
     *
     * For multi-byte named entities, this will be called multiple times,
     * with the second codepoint, and the same `consumed` value.
     * @param codepoint The decoded codepoint.
     * @param consumed The number of bytes consumed by the decoder.
     */
    emitCodePoint: (cp: number, consumed: number) => void, 
    /** An object that is used to produce errors. */
    errors?: EntityErrorProducer | undefined);
    /**
     * Switches between the numeric decimal and hexadecimal states.
     *
     * Equivalent to the `Numeric character reference state` in the HTML spec.
     * @param input The string containing the entity (or a continuation of the entity).
     * @param offset The current offset.
     * @returns The number of characters that were consumed, or -1 if the entity is incomplete.
     */
    private stateNumericStart;
    /**
     * Parses a hexadecimal numeric entity.
     *
     * Equivalent to the `Hexadecimal character reference state` in the HTML spec.
     * @param input The string containing the entity (or a continuation of the entity).
     * @param offset The current offset.
     * @returns The number of characters that were consumed, or -1 if the entity is incomplete.
     */
    private stateNumericHex;
    /**
     * Parses a decimal numeric entity.
     *
     * Equivalent to the `Decimal character reference state` in the HTML spec.
     * @param input The string containing the entity (or a continuation of the entity).
     * @param offset The current offset.
     * @returns The number of characters that were consumed, or -1 if the entity is incomplete.
     */
    private stateNumericDecimal;
    /**
     * Validate and emit a numeric entity.
     *
     * Implements the logic from the `Hexadecimal character reference start
     * state` and `Numeric character reference end state` in the HTML spec.
     * @param lastCp The last code point of the entity. Used to see if the
     *               entity was terminated with a semicolon.
     * @param expectedLength The minimum number of characters that should be
     *                       consumed. Used to validate that at least one digit
     *                       was consumed.
     * @returns The number of characters that were consumed.
     */
    private emitNumericEntity;
    /**
     * Resets the instance to make it reusable.
     * @param decodeMode Entity decoding mode to use.
     */
    startEntity(decodeMode: DecodingMode): void;
    /**
     * Write an entity to the decoder. This can be called multiple times with partial entities.
     * If the entity is incomplete, the decoder will return -1.
     * @param input The string containing the entity (or a continuation of the entity).
     * @param offset The offset at which the entity begins. Should be 0 if this is not the first call.
     * @returns The number of characters that were consumed, or -1 if the entity is incomplete.
     */
    write(input: string, offset: number): number;
    /**
     * Decode a named entity.
     * @param input The string containing the entity (or a continuation of the entity).
     * @param offset The current offset.
     * @returns The number of characters that were consumed, or -1 if the entity is incomplete.
     */
    protected abstract stateNamedEntity(input: string, offset: number): number;
    /**
     * Resolve a named entity that was still incomplete when the input
     * ended.
     * @returns The number of characters that were consumed.
     */
    protected abstract endNamedEntity(): number;
    /**
     * Signal to the parser that the end of the input was reached.
     *
     * Remaining data will be emitted and relevant errors will be produced.
     * @returns The number of characters consumed.
     */
    end(): number;
}
/**
 * Streaming decoder for HTML entities.
 *
 * When the entity fits inside the current chunk (the common case), the
 * lookup runs directly on the chunk via the same length-probe scheme as the
 * synchronous decoder; chunk-boundary runs use a reusable character buffer.
 */
export declare class HtmlEntityDecoder extends EntityDecoderBase {
    /** Total name characters seen for the current named entity. */
    private runLength;
    /** Reused for ASCII alphanumerics; the 32nd character rules out an exact match. */
    private readonly nameBuffer;
    /**
     * Emit the replacement for a matched slot. Values are at most two
     * UTF-16 code units; like previous versions, each unit is emitted as
     * its own callback (surrogate halves included).
     * @param slot The matched slot.
     */
    private emitSlot;
    startEntity(decodeMode: DecodingMode): void;
    protected stateNamedEntity(input: string, offset: number): number;
    protected endNamedEntity(): number;
}
/**
 * Streaming decoder for XML entities: the five predefined named entities
 * plus numeric character references.
 */
export declare class XmlEntityDecoder extends EntityDecoderBase {
    protected readonly replaceNumericCodePoint: (codePoint: number) => number;
    protected stateNamedEntity(input: string, offset: number): number;
    protected endNamedEntity(): number;
}
export { replaceCodePoint, replaceCodePointXML } from "./decode-codepoint.js";
//# sourceMappingURL=decode.d.ts.map