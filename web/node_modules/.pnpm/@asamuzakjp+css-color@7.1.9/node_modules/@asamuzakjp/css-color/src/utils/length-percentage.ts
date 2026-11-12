/**
 * Length-percentage calculation and validation.
 */

import {
  CSSToken,
  ParseError,
  tokenize,
  TokenType
} from '@csstools/css-tokenizer';
import { CalculationState } from '../typedef';
import { isString } from './common';

/* Constants */
import { LENGTH } from './constant';
const {
  CloseParen: PAREN_CLOSE,
  Comma: COMMA,
  Comment: COMMENT,
  Delim: DELIM,
  Dimension: DIM,
  EOF,
  Function: FUNC,
  Ident: IDENT,
  Number: NUMBER_TOKEN,
  OpenParen: PAREN_OPEN,
  Percentage: PERCENT,
  Whitespace: W_SPACE
} = TokenType;

/* Regular expressions */
const REG_LENGTH = new RegExp(`^(?:${LENGTH})$`, 'i');
const REG_CALC = /^(?:calc|min|max|clamp)\(/i;
const REG_CALC_KEYWORD = /^(?:e|pi|-?infinity|NaN)$/i;

/**
 * Advances past whitespace and comments in a calculation.
 * @param state - The calculation state containing the token stream and cursor
 */
export const skipCalculationWhitespace = (state: CalculationState): void => {
  let token = state.tokens[state.index];
  while (token && (token[0] === W_SPACE || token[0] === COMMENT)) {
    state.index++;
    token = state.tokens[state.index];
  }
};

/**
 * Checks for actual whitespace, ignoring adjacent comments.
 * @param tokens - The array of CSS tokens
 * @param start - The index of the first token to check
 * @param step - The direction to scan (1 or -1)
 * @returns True if whitespace occurs before another non-comment token, false otherwise
 */
export const hasCalculationWhitespace = (
  tokens: CSSToken[],
  start: number,
  step: 1 | -1
): boolean => {
  for (let i = start; i >= 0 && i < tokens.length; i += step) {
    const [type] = tokens[i] as CSSToken;
    if (type === W_SPACE) {
      return true;
    }
    if (type !== COMMENT) {
      break;
    }
  }
  return false;
};

/**
 * Parses multiplication and division of calculation operands.
 * Multiplication requires a number on one side, and division requires a number divisor.
 * This subset intentionally excludes CSS Values Level 4 typed arithmetic.
 * @param state - The calculation state containing the token stream and cursor
 * @returns The calculation type number, or null on failure
 */
export const parseCalculationProduct = (
  state: CalculationState
): number | null => {
  let type = parseCalculationValue(state);
  if (type === null) {
    return null;
  }
  skipCalculationWhitespace(state);
  let current = state.tokens[state.index];
  while (
    current &&
    current[0] === DELIM &&
    (current[1] === '*' || current[1] === '/')
  ) {
    const operator = current[1];
    state.index++;
    const right = parseCalculationValue(state);
    if (
      right === null ||
      (operator === '*' ? type !== 0 && right !== 0 : right !== 0)
    ) {
      return null;
    }
    type += right;
    skipCalculationWhitespace(state);
    current = state.tokens[state.index];
  }
  return type;
};

/**
 * Parses addition and subtraction, requiring matching operand types.
 * @param state - The calculation state containing the token stream and cursor
 * @returns The calculation type number, or null on failure
 */
export const parseCalculationSum = (state: CalculationState): number | null => {
  const type = parseCalculationProduct(state);
  if (type === null) {
    return null;
  }
  let current = state.tokens[state.index];
  while (
    current &&
    current[0] === DELIM &&
    (current[1] === '+' || current[1] === '-')
  ) {
    if (
      !hasCalculationWhitespace(state.tokens, state.index - 1, -1) ||
      !hasCalculationWhitespace(state.tokens, state.index + 1, 1)
    ) {
      return null;
    }
    state.index++;
    if (parseCalculationProduct(state) !== type) {
      return null;
    }
    current = state.tokens[state.index];
  }
  return type;
};

/**
 * Parses a scalar, length, percentage, parenthesized sum, or math function.
 * Percentages receive the length percent hint in this context.
 * @param state - The calculation state containing the token stream and cursor
 * @returns The calculation type number, or null on failure
 */
export function parseCalculationValue(state: CalculationState): number | null {
  skipCalculationWhitespace(state);
  const currentToken = state.tokens[state.index];
  if (!currentToken || currentToken[0] === EOF) {
    return null;
  }
  state.index++;
  const [tokenType, , , , tokenDetails] = currentToken;
  switch (tokenType) {
    case NUMBER_TOKEN: {
      return 0;
    }
    case PERCENT: {
      return 1;
    }
    case DIM: {
      return REG_LENGTH.test(tokenDetails.unit) ? 1 : null;
    }
    case IDENT: {
      return REG_CALC_KEYWORD.test(tokenDetails.value) ? 0 : null;
    }
    case PAREN_OPEN:
    case FUNC: {
      const name =
        tokenType === FUNC ? tokenDetails.value.toLowerCase() : 'calc';
      if (!['calc', 'min', 'max', 'clamp'].includes(name)) {
        return null;
      }
      const type = parseCalculationSum(state);
      if (type === null) {
        return null;
      }
      let count = 1;
      let nextToken = state.tokens[state.index];
      while (nextToken && nextToken[0] === COMMA) {
        state.index++;
        if (parseCalculationSum(state) !== type) {
          return null;
        }
        count++;
        nextToken = state.tokens[state.index];
      }
      const closingToken = state.tokens[state.index];
      if (
        !closingToken ||
        closingToken[0] !== PAREN_CLOSE ||
        (name === 'calc' && count !== 1) ||
        (name === 'clamp' && count !== 3)
      ) {
        return null;
      }
      state.index++;
      return type;
    }
    default: {
      return null;
    }
  }
}

/**
 * Validates a calculation in a length-percentage context.
 * Types are represented as 0 for numbers and 1 for lengths/percentages, with scalar products only.
 * @param value - The calculation value to validate
 * @returns True if the calculation has type length-percentage, false otherwise
 */
export const isLengthPercentageCalculation = (value: unknown): boolean => {
  if (!isString(value) || !REG_CALC.test(value)) {
    return false;
  }
  const errors: ParseError[] = [];
  const state: CalculationState = {
    tokens: tokenize(
      { css: value },
      { onParseError: errors.push.bind(errors) }
    ),
    index: 0
  };
  const type = parseCalculationValue(state);
  skipCalculationWhitespace(state);
  const finalToken = state.tokens[state.index];
  return (
    errors.length === 0 && type === 1 && !!finalToken && finalToken[0] === EOF
  );
};
