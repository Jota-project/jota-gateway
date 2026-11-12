/**
 * CSS gradient parsing functionality.
 */
import { Gradient, Options } from '../typedef';
/**
 * Parses a CSS gradient value into a structured gradient object.
 * @param value - The CSS gradient string to parse.
 * @param [opt] - The parsing options.
 * @returns A structured Gradient object, or null if parsing fails.
 */
export declare const parseGradient: (value: string, opt?: Options) => Gradient | null;
