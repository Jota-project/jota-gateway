/**
 * Color conversion and transformation functions.
 */
import { ColorChannels, ColorChannelsWithPowerless, LinearRgbOptions, Options } from '../typedef';
/**
 * Converts a CSS color value to linear sRGB channels.
 * @param value - The CSS color value to convert.
 * @param opt - The linear RGB options.
 * @returns The color channels array: [r, g, b, alpha] (where r, g, b, alpha range from 0 to 1), or null if invalid.
 */
export declare const convertColorToLinearRgb: (value: string, opt?: LinearRgbOptions) => ColorChannels | null;
/**
 * Converts a CSS color value to standard sRGB channels.
 * @param value - The CSS color value to convert.
 * @param opt - The conversion options.
 * @returns The color channels array: [r, g, b, alpha] (where r, g, b range from 0 to 255, and alpha ranges from 0 to 1), or null if invalid.
 */
export declare const convertColorToRgb: (value: string, opt?: Options) => ColorChannels | null;
/**
 * Converts a CSS color value to XYZ color channels.
 * @param value - The CSS color value to convert.
 * @param opt - The conversion options.
 * @returns The color channels array: [x, y, z, alpha], or null if invalid.
 */
export declare const convertColorToXyz: (value: string, opt?: Options) => ColorChannels | null;
/**
 * Converts a CSS color value to HSL color channels.
 * @param value - The CSS color value to convert.
 * @param opt - The conversion options.
 * @returns The color channels array: [h, s, l, alpha] (hue may be marked as powerless if applicable), or null if invalid.
 */
export declare const convertColorToHsl: (value: string, opt?: Options) => ColorChannels | ColorChannelsWithPowerless | null;
/**
 * Converts a CSS color value to HWB color channels.
 * @param value - The CSS color value to convert.
 * @param opt - The conversion options.
 * @returns The color channels array: [h, w, b, alpha] (hue may be marked as powerless if applicable), or null if invalid.
 */
export declare const convertColorToHwb: (value: string, opt?: Options) => ColorChannels | [number | string, number, number, number] | null;
/**
 * Converts a CSS color value to CIELAB color channels.
 * @param value - The CSS color value to convert.
 * @param opt - The conversion options.
 * @returns The color channels array: [l, a, b, alpha], or null if invalid.
 */
export declare const convertColorToLab: (value: string, opt?: Options) => ColorChannels | null;
/**
 * Converts a CSS color value to CIELCH color channels.
 * @param value - The CSS color value to convert.
 * @param opt - The conversion options.
 * @returns The color channels array: [l, c, h, alpha] (hue may be marked as powerless if applicable), or null if invalid.
 */
export declare const convertColorToLch: (value: string, opt?: Options) => ColorChannels | [number, number, number | string, number] | null;
/**
 * Converts a CSS color value to Oklab color channels.
 * @param value - The CSS color value to convert.
 * @param opt - The conversion options.
 * @returns The color channels array: [l, a, b, alpha], or null if invalid.
 */
export declare const convertColorToOklab: (value: string, opt?: Options) => ColorChannels | null;
/**
 * Converts a CSS color value to Oklch color channels.
 * @param value - The CSS color value to convert.
 * @param opt - The conversion options.
 * @returns The color channels array: [l, c, h, alpha] (hue may be marked as powerless if applicable), or null if invalid.
 */
export declare const convertColorToOklch: (value: string, opt?: Options) => ColorChannels | [number, number, number | string, number] | null;
