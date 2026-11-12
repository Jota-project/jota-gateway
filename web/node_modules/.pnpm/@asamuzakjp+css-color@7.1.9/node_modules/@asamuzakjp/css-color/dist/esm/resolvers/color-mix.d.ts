/**
 * CSS color-mix() utilities.
 */
import { ChannelNoneFlags, ColorAndPct, ColorFormat, ColorMixInnerColorItem, ColorMixInnerContent, ColorResolverFn, ColorSpace, ColorSpaceParseResult, ColorsAndMultipler, InterpolatedComponents, NormalizedPercentages, NumStrColorChannels, Options, SpecifiedColorChannels, TriColorChannels } from '../typedef';
/**
 * Normalizes the percentages for color mixing.
 * @param pctA - The percentage string for the first color.
 * @param pctB - The percentage string for the second color.
 * @returns An object containing normalized percentages and a multiplier, or null if invalid.
 */
export declare const normalizePercentages: (pctA: string, pctB: string) => NormalizedPercentages | null;
/**
 * Performs alpha-weighted interpolation for color components.
 * @param compA - The color components for the first color.
 * @param compB - The color components for the second color.
 * @param alphaA - The alpha channel value for the first color.
 * @param alphaB - The alpha channel value for the second color.
 * @param pA - The normalized ratio for the first color.
 * @param pB - The normalized ratio for the second color.
 * @param invalidate - Indicates whether to invalidate values if alpha is zero.
 * @returns An object containing the interpolated components and the combined alpha value.
 */
export declare const interpolateComponents: (compA: TriColorChannels, compB: TriColorChannels, alphaA: number, alphaB: number, pA: number, pB: number, invalidate?: boolean) => InterpolatedComponents;
/**
 * Formats a computed mixed color into a specified or computed channel array.
 * @param colorSpace - The target color space.
 * @param comps - The 3-channel color components.
 * @param nones - The boolean flags indicating if channels are marked as 'none'.
 * @param alpha - The calculated alpha channel value.
 * @param m - The multiplier factor.
 * @param format - The output format option.
 * @param rgbOverride - Optional pre-calculated RGB channels.
 * @returns The specified color channels array.
 */
export declare const formatMixedColor: (colorSpace: ColorSpace, comps: TriColorChannels, nones: ChannelNoneFlags, alpha: number, m: number, format: ColorFormat, rgbOverride?: TriColorChannels) => SpecifiedColorChannels;
/**
 * Fetches raw color channels or handles fallbacks for currentcolor.
 * @param color - The color input string or pre-parsed channels.
 * @param convertFn - The conversion function to use.
 * @param opt - The conversion options.
 * @returns The color channel values, or null if invalid.
 */
export declare const getRawChannels: (color: string | NumStrColorChannels, convertFn: (c: string, opt: any) => any, opt: any) => NumStrColorChannels | null;
/**
 * Mixes colors in the sRGB or linear sRGB color space.
 * @param colorSpace - The color space name ('srgb' or 'srgb-linear').
 * @param colorA - The first color string.
 * @param colorB - The second color string.
 * @param pA - The mixing ratio for the first color.
 * @param pB - The mixing ratio for the second color.
 * @param format - The output format.
 * @param m - The multiplier factor.
 * @returns The mixed color channels, or null if invalid.
 */
export declare const mixSrgbSpace: (colorSpace: ColorSpace, colorA: string, colorB: string, pA: number, pB: number, format: ColorFormat, m: number) => SpecifiedColorChannels | null;
/**
 * Mixes colors in the XYZ, XYZ-D50, or XYZ-D65 color space.
 * @param colorSpace - The color space name.
 * @param colorA - The first color string.
 * @param colorB - The second color string.
 * @param pA - The mixing ratio for the first color.
 * @param pB - The mixing ratio for the second color.
 * @param format - The output format.
 * @param m - The multiplier factor.
 * @returns The mixed color channels, or null if invalid.
 */
export declare const mixXyzSpace: (colorSpace: string, colorA: string, colorB: string, pA: number, pB: number, format: string, m: number) => SpecifiedColorChannels | null;
/**
 * Mixes colors in the HSL or HWB color space.
 * @param colorSpace - The color space name ('hsl' or 'hwb').
 * @param hueArc - The hue interpolation method.
 * @param colorA - The first color string.
 * @param colorB - The second color string.
 * @param pA - The mixing ratio for the first color.
 * @param pB - The mixing ratio for the second color.
 * @param format - The output format.
 * @param m - The multiplier factor.
 * @returns The mixed color channels, or null if invalid.
 */
export declare const mixHslHwbSpace: (colorSpace: string, hueArc: string, colorA: string, colorB: string, pA: number, pB: number, format: string, m: number) => SpecifiedColorChannels | null;
/**
 * Mixes colors in the LCH or Oklch color space.
 * @param colorSpace - The color space name ('lch' or 'oklch').
 * @param hueArc - The hue interpolation method.
 * @param colorA - The first color string.
 * @param colorB - The second color string.
 * @param pA - The mixing ratio for the first color.
 * @param pB - The mixing ratio for the second color.
 * @param format - The output format.
 * @param m - The multiplier factor.
 * @returns The mixed color channels, or null if invalid.
 */
export declare const mixLchSpace: (colorSpace: string, hueArc: string, colorA: string, colorB: string, pA: number, pB: number, format: string, m: number) => SpecifiedColorChannels | null;
/**
 * Mixes colors in the Lab or Oklab color space.
 * @param colorSpace - The color space name ('lab' or 'oklab').
 * @param colorA - The first color string.
 * @param colorB - The second color string.
 * @param pA - The mixing ratio for the first color.
 * @param pB - The mixing ratio for the second color.
 * @param format - The output format.
 * @param m - The multiplier factor.
 * @returns The mixed color channels, or null if invalid.
 */
export declare const mixLabSpace: (colorSpace: string, colorA: string, colorB: string, pA: number, pB: number, format: string, m: number) => SpecifiedColorChannels | null;
/**
 * Computes the mixed color channels based on the specified color space.
 * @param colorSpace - The color space.
 * @param hueArc - The hue interpolation method.
 * @param colorA - The first color string.
 * @param colorB - The second color string.
 * @param pA - The normalized ratio for the first color.
 * @param pB - The normalized ratio for the second color.
 * @param m - The multiplier factor.
 * @param format - The output format.
 * @returns The computed color channels, or null if invalid.
 */
export declare const computeMixedColor: (colorSpace: string, hueArc: string, colorA: string, colorB: string, pA: number, pB: number, m: number, format: string) => SpecifiedColorChannels | null;
/**
 * Determines whether the given value is possibly a percentage.
 * @param value - value
 * @returns True if the value is percentage, false otherwise.
 */
export declare const isPossiblyPercentage: (value: string) => boolean;
/**
 * Parses a color and percentage argument string.
 * @param arg - The color and percentage argument string to parse.
 * @returns An object containing the color string and percentage string.
 */
export declare const parseColorAndPct: (arg: string) => ColorAndPct;
/**
 * Parses inner content color items
 * @param items - The color items
 * @returns The parsed color-mix() inner colors and total percentage.
 */
export declare const parseInnerContentColorItems: (items: string[]) => ColorMixInnerColorItem | null;
/**
 * Parses a color space argument string.
 * @param arg - The color space argument string to parse.
 * @returns An object containing the color space and hue arc, or null if invalid.
 */
export declare const parseColorSpace: (arg: string) => ColorSpaceParseResult | null;
/**
 * Parses inner content of the color-mix().
 * @param value - The color-mix() value.
 * @returns The parsed color-mix() inner content.
 */
export declare const parseColorMixInnerContent: (value: string) => ColorMixInnerContent | null;
/**
 * Resolves nested functions recursively.
 * @param colorStr - The color string to resolve.
 * @param opt - The conversion options.
 * @param resolver - The color resolver function.
 * @returns The resolved color string, or the original string if no nesting is present.
 */
export declare const resolveIfNested: (colorStr: string, opt: Options, resolver: ColorResolverFn) => string;
/**
 * Normalizes percentages for multiple colors in color-mix() according to CSS Color Module Level 5.
 * @param colors - Array of parsed color and percentage items.
 * @param opt - The conversion options.
 * @param resolver - The color resolver function.
 * @returns An object containing normalized colors with ratios (0-1) and a multiplier m, or null if invalid.
 */
export declare const normalizeMixPercentages: (colors: ColorAndPct[], opt: Options, resolver: ColorResolverFn) => ColorsAndMultipler | null;
/**
 * Normalizes color values and percentage values.
 * @param colors - The list of color items.
 * @param totalPercentage - The total percentage.
 * @param opt - The conversion options.
 * @param resolver - The color resolver function.
 * @returns The normalized color-mix() inner colors and total percentage.
 */
export declare const normalizeColorItems: (colors: ColorAndPct[], totalPercentage: number, opt: Options, resolver: ColorResolverFn) => ColorMixInnerColorItem;
/**
 * Processes and standardizes a color value string.
 * @param colorStr - The color string to process.
 * @param opt - The conversion options.
 * @returns The processed specified color string.
 */
export declare const processColor: (colorStr: string, opt: Options) => string;
/**
 * Formats a specified value for the color-mix().
 * @param colorSpace - The target color space.
 * @param hueArc - The hue interpolation method (hue arc).
 * @param colors - The list of color items.
 * @param opt - The conversion options.
 * @returns The specified value for the color-mix().
 */
export declare const formatSpecifiedValue: (colorSpace: string | null, hueArc: string, colors: ColorAndPct[], opt: Options) => string;
/**
 * Resolves a CSS color-mix() function value.
 * @param value - The color-mix() value to resolve.
 * @param opt - The conversion options.
 * @param resolver - The color resolver function.
 * @returns The resolved color channels, formatted string, or null.
 */
export declare const resolveColorMix: (value: string, opt?: Options, resolver?: ColorResolverFn) => SpecifiedColorChannels | string | null;
