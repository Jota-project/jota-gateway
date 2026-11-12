/**
 * Utilities for parsing and validating CSS gradients.
 */
import { Options, ValidateGradientLine, ValidateColorStops } from '../typedef';
/**
 * Extracts and returns the gradient type from a CSS value.
 * @param value - The CSS gradient value.
 * @returns The gradient type function name, or an empty string if invalid.
 */
export declare const getGradientType: (value: string) => string;
/**
 * Validates and normalizes a linear-gradient line definition.
 * @param value - The gradient line value to validate.
 * @returns An object containing the normalized line and a validity boolean.
 */
export declare const validateLinearGradientLine: (value: string) => ValidateGradientLine;
/**
 * Validates and normalizes a radial-gradient line definition.
 * @param value - The gradient line value to validate.
 * @returns An object containing the normalized line and a validity boolean.
 */
export declare const validateRadialGradientLine: (value: string) => ValidateGradientLine;
/**
 * Validates and normalizes a conic-gradient line definition.
 * @param value - The gradient line value to validate.
 * @returns An object containing the normalized line and a validity boolean.
 */
export declare const validateConicGradientLine: (value: string) => ValidateGradientLine;
/**
 * Validates a gradient line based on the specified gradient type.
 * @param value - The gradient line value to validate.
 * @param type - The type of the gradient.
 * @returns An object containing the normalized line and a validity boolean.
 */
export declare const validateGradientLine: (value: string, type: string) => ValidateGradientLine;
/**
 * Splits a color stop or transition hint into its component parts.
 * @param value - The color stop or transition hint string.
 * @returns An array of component parts, retaining comments inside functions.
 */
export declare const splitColorStop: (value: string) => string[];
/**
 * Validates a position value within a gradient color stop or transition hint.
 * @param value - The position component string.
 * @param isConic - Whether the gradient uses angle-percentage positions (conic gradient).
 * @returns True if the position is valid for the given context, otherwise false.
 */
export declare const isValidPosition: (value: string, isConic: boolean) => boolean;
/**
 * Validates and resolves a list of gradient color stops and hints.
 * @param list - The array of color stop strings.
 * @param type - The type of the gradient.
 * @param [opt] - The conversion options.
 * @returns An object containing the resolved color stop list and a validity boolean.
 */
export declare const validateColorStopList: (list: string[], type: string, opt?: Options) => ValidateColorStops;
