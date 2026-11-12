/**
 * CSS relative color utilities.
 */
import { color as colorParser } from '@csstools/css-color-parser';
import { CSSToken } from '@csstools/css-tokenizer';
import { NumberOrStringColorChannels, Options } from '../typedef';
/**
 * Serializes channel values into an array of numbers or strings.
 * @param channels - The color channels array.
 * @param format - The output format option.
 * @returns The resolved channel values.
 */
export declare const serializeChannels: (channels: [(number | string)[], (number | string)[], (number | string)[], (number | string)[]], format: string) => NumberOrStringColorChannels;
/**
 * Gets the last non-whitespace value from a channel array.
 * @param channel - The channel array.
 * @returns The last non-whitespace value, or undefined if none exists.
 */
export declare const getLastNonWhitespaceValue: (channel: (number | string)[]) => string | number | undefined;
/**
 * Resolves relative color channels from CSS tokens.
 * @param tokens - The CSS tokens to process.
 * @param [opt] - The conversion options.
 * @returns The resolved color channels, or null if invalid.
 */
export declare const resolveColorChannels: (tokens: CSSToken[], opt?: Options) => NumberOrStringColorChannels | null;
/**
 * Resolves the origin color when the light-dark() function is used.
 * @param value - The CSS color value string.
 * @param colorSpace - The color space name.
 * @param options - The conversion options.
 * @param resolver - The color resolver function.
 * @returns The resolved color value, or null if resolution fails.
 */
export declare const resolveLightDarkOriginColor: (value: string, colorSpace: string, options: Options, resolver: (v: string, o?: Options) => string | null) => string | null;
/**
 * Resolves nested relative color origins and channels.
 * @param value - The CSS color value string.
 * @param options - The conversion options.
 * @param resolver - The color resolver function.
 * @returns The resolved color value, or null if resolution fails.
 */
export declare const resolveNestedRelativeColor: (value: string, options: Options, resolver: (v: string, o?: Options) => string | null) => string | null;
/**
 * Extracts the origin color from a relative color expression.
 * @param value - The CSS color value string.
 * @param opt - The conversion options.
 * @param resolver - The color resolver function.
 * @returns The extracted origin color value, or null if invalid.
 */
export declare const extractOriginColor: (value: string, opt?: Options, resolver?: (v: string, o?: Options) => string | null) => string | null;
/**
 * Checks whether the CSS tokens contain a var() function.
 * @param tokens - The CSS tokens to check.
 * @returns True if a var() function exists, otherwise false.
 */
export declare const hasCssVariables: (tokens: CSSToken[]) => boolean;
/**
 * Checks whether the CSS tokens contain relative color syntax.
 * @param tokens - The CSS tokens to check.
 * @returns True if relative color syntax exists, otherwise false.
 */
export declare const hasRelativeColorSyntax: (tokens: CSSToken[]) => boolean;
/**
 * Formats parsed color components into a standard CSS color string.
 * @param parsedComponents - The parsed color components from the color parser.
 * @returns The formatted CSS color string, or null if invalid.
 */
export declare const formatResolvedColor: (parsedComponents: ReturnType<typeof colorParser>) => string | null;
/**
 * Resolves a CSS relative color value.
 * @param value - The CSS relative color string to resolve.
 * @param opt - The conversion options.
 * @param resolver - The color resolver function.
 * @returns The resolved color string, or null if resolution fails.
 */
export declare function resolveRelativeColor(value: string, opt?: Options, resolver?: (v: string, o?: Options) => string | null): string | null;
