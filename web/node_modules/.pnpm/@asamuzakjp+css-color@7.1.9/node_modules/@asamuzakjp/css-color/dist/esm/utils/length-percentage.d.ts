/**
 * Length-percentage calculation and validation.
 */
import { CSSToken } from '@csstools/css-tokenizer';
import { CalculationState } from '../typedef';
/**
 * Advances past whitespace and comments in a calculation.
 * @param state - The calculation state containing the token stream and cursor
 */
export declare const skipCalculationWhitespace: (state: CalculationState) => void;
/**
 * Checks for actual whitespace, ignoring adjacent comments.
 * @param tokens - The array of CSS tokens
 * @param start - The index of the first token to check
 * @param step - The direction to scan (1 or -1)
 * @returns True if whitespace occurs before another non-comment token, false otherwise
 */
export declare const hasCalculationWhitespace: (tokens: CSSToken[], start: number, step: 1 | -1) => boolean;
/**
 * Parses multiplication and division of calculation operands.
 * Multiplication requires a number on one side, and division requires a number divisor.
 * This subset intentionally excludes CSS Values Level 4 typed arithmetic.
 * @param state - The calculation state containing the token stream and cursor
 * @returns The calculation type number, or null on failure
 */
export declare const parseCalculationProduct: (state: CalculationState) => number | null;
/**
 * Parses addition and subtraction, requiring matching operand types.
 * @param state - The calculation state containing the token stream and cursor
 * @returns The calculation type number, or null on failure
 */
export declare const parseCalculationSum: (state: CalculationState) => number | null;
/**
 * Parses a scalar, length, percentage, parenthesized sum, or math function.
 * Percentages receive the length percent hint in this context.
 * @param state - The calculation state containing the token stream and cursor
 * @returns The calculation type number, or null on failure
 */
export declare function parseCalculationValue(state: CalculationState): number | null;
/**
 * Validates a calculation in a length-percentage context.
 * Types are represented as 0 for numbers and 1 for lengths/percentages, with scalar products only.
 * @param value - The calculation value to validate
 * @returns True if the calculation has type length-percentage, false otherwise
 */
export declare const isLengthPercentageCalculation: (value: unknown) => boolean;
