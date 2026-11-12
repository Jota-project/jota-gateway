/**
 * CSS calc() and var() utilities.
 */

import { calc, conversionOptions as CalcOptions } from '@csstools/css-calc';
import { CSSToken, TokenType, tokenize } from '@csstools/css-tokenizer';
import { isValidColor } from '../resolvers/resolve-color';
import { CalcASTNode, MatchedRegExp, Options } from '../typedef';
import { createCacheKey, getCache, setCache } from './cache';
import { isString, isStringOrNumber } from './common';
import { getMaxLength, resolveLengthInPixels, roundToPrecision } from './util';

/* Constants */
import {
  FN_CALC,
  FN_VAR,
  HEX,
  MAX_PCT,
  NUM,
  SYN_FN_CALC,
  SYN_FN_MATH_START,
  SYN_FN_VAR,
  SYN_FN_VAR_START,
  TRIA,
  VAL_SPEC
} from '../utils/constant';

const {
  CloseParen: PAREN_CLOSE,
  Comment: COMMENT,
  Delim: DELIM,
  Dimension: DIM,
  EOF,
  Function: FUNC,
  Ident: IDENT,
  Number: NUMBER_TOKEN,
  OpenParen: PAREN_OPEN,
  Whitespace: W_SPACE
} = TokenType;
const NAMESPACE = 'css-calc-var';
const CSS_WIDE_KEYWORDS = new Set([
  'inherit',
  'initial',
  'revert',
  'revert-layer',
  'unset'
]);
const OPERATORS = new Set(['+', '-', '*', '/']);

/* Regular expressions */
const REG_FN_CALC = new RegExp(SYN_FN_CALC, 'i');
const REG_FN_CALC_NUM = new RegExp(`^calc\\((${NUM})\\)$`, 'i');
const REG_FN_MATH_START = new RegExp(SYN_FN_MATH_START, 'i');
const REG_FN_VAR = new RegExp(SYN_FN_VAR, 'i');
const REG_FN_VAR_START = new RegExp(SYN_FN_VAR_START, 'i');
const REG_OPERATOR = /\s[-+*/]\s/;

/**
 * Parses a term string or number into a numeric value and its unit.
 * @param term - The term string or number to parse
 * @returns An object containing the parsed number and unit, or null on failure
 */
export const parseTerm = (
  term: string | number
): { num: number; unit: string } | null => {
  if (typeof term === 'number') {
    return { num: term, unit: '' };
  }
  if (!isString(term)) {
    return null;
  }
  const tokens = tokenize({ css: term }).filter(
    ([type]) => type !== W_SPACE && type !== COMMENT && type !== EOF
  );
  if (tokens.length !== 1) {
    return null;
  }
  const token = tokens[0] as [
    TokenType,
    string,
    unknown,
    unknown,
    { value?: number; unit?: string } | undefined
  ];
  const [type, val, , , detail] = token;
  if (type === NUMBER_TOKEN) {
    return { num: Number(val), unit: '' };
  }
  if (type === DIM && detail) {
    return { num: Number(detail.value), unit: detail.unit as string };
  }
  if (val.endsWith('%')) {
    return { num: parseFloat(val), unit: '%' };
  }
  return null;
};

/**
 * Calculator class for handling calculation terms and operations.
 */
export class Calculator {
  // Number
  #hasNum: boolean;
  #numSum: number[];
  #numMul: number[];
  // Percentage
  #hasPct: boolean;
  #pctSum: number[];
  #pctMul: number[];
  // Dimension
  #hasDim: boolean;
  #dimSum: string[];
  #dimSub: string[];
  #dimMul: string[];
  #dimDiv: string[];
  // Et cetera
  #hasEtc: boolean;
  #etcSum: string[];
  #etcSub: string[];
  #etcMul: string[];
  #etcDiv: string[];
  // Calculation options
  #calcOpts: CalcOptions;

  /**
   * Creates an instance of Calculator.
   */
  constructor() {
    // Number
    this.#hasNum = false;
    this.#numSum = [];
    this.#numMul = [];
    // Percentage
    this.#hasPct = false;
    this.#pctSum = [];
    this.#pctMul = [];
    // Dimension
    this.#hasDim = false;
    this.#dimSum = [];
    this.#dimSub = [];
    this.#dimMul = [];
    this.#dimDiv = [];
    // Et cetera
    this.#hasEtc = false;
    this.#etcSum = [];
    this.#etcSub = [];
    this.#etcMul = [];
    this.#etcDiv = [];
    // Calculation options
    this.#calcOpts = {
      toCanonicalUnits: true
    };
  }

  get hasNum() {
    return this.#hasNum;
  }

  set hasNum(value: boolean) {
    this.#hasNum = !!value;
  }

  get numSum() {
    return this.#numSum;
  }

  get numMul() {
    return this.#numMul;
  }

  get hasPct() {
    return this.#hasPct;
  }

  set hasPct(value: boolean) {
    this.#hasPct = !!value;
  }

  get pctSum() {
    return this.#pctSum;
  }

  get pctMul() {
    return this.#pctMul;
  }

  get hasDim() {
    return this.#hasDim;
  }

  set hasDim(value: boolean) {
    this.#hasDim = !!value;
  }

  get dimSum() {
    return this.#dimSum;
  }

  get dimSub() {
    return this.#dimSub;
  }

  get dimMul() {
    return this.#dimMul;
  }

  get dimDiv() {
    return this.#dimDiv;
  }

  get hasEtc() {
    return this.#hasEtc;
  }

  set hasEtc(value: boolean) {
    this.#hasEtc = !!value;
  }

  get etcSum() {
    return this.#etcSum;
  }

  get etcSub() {
    return this.#etcSub;
  }

  get etcMul() {
    return this.#etcMul;
  }

  get etcDiv() {
    return this.#etcDiv;
  }

  /**
   * Clears all stored values and resets states.
   */
  clear() {
    // Number
    this.#hasNum = false;
    this.#numSum.length = 0;
    this.#numMul.length = 0;
    // Percentage
    this.#hasPct = false;
    this.#pctSum.length = 0;
    this.#pctMul.length = 0;
    // Dimension
    this.#hasDim = false;
    this.#dimSum.length = 0;
    this.#dimSub.length = 0;
    this.#dimMul.length = 0;
    this.#dimDiv.length = 0;
    // Et cetera
    this.#hasEtc = false;
    this.#etcSum.length = 0;
    this.#etcSub.length = 0;
    this.#etcMul.length = 0;
    this.#etcDiv.length = 0;
  }

  /**
   * Sorts an array of value strings.
   * @param values - The array of values to sort
   * @returns The sorted array of values
   */
  sort(values: string[] = []): string[] {
    const arr = [...values];
    if (arr.length > 1) {
      arr.sort((a, b) => {
        const termA = parseTerm(a);
        const termB = parseTerm(b);
        if (termA && termB && termA.unit && termB.unit) {
          const { num: valA, unit: unitA } = termA;
          const { num: valB, unit: unitB } = termB;
          if (unitA === unitB) {
            if (valA === valB) {
              return 0;
            }
            return valA > valB ? 1 : -1;
          }
          return unitA > unitB ? 1 : -1;
        }
        if (a === b) {
          return 0;
        }
        return a > b ? 1 : -1;
      });
    }
    return arr;
  }

  /**
   * Multiplies the stored values and returns the resolved expression string.
   * @returns The resolved multiplication expression
   */
  multiply(): string {
    const values = [];
    let num;
    if (this.#hasNum) {
      num = 1;
      for (const i of this.#numMul) {
        num *= i;
        if (num === 0 || !Number.isFinite(num) || Number.isNaN(num)) {
          break;
        }
      }
      if (!this.#hasPct && !this.#hasDim && !this.hasEtc) {
        if (Number.isFinite(num)) {
          num = roundToPrecision(num, HEX);
        }
        values.push(num);
      }
    }
    if (this.#hasPct) {
      if (typeof num !== 'number') {
        num = 1;
      }
      for (const i of this.#pctMul) {
        num *= i;
        if (num === 0 || !Number.isFinite(num) || Number.isNaN(num)) {
          break;
        }
      }
      if (Number.isFinite(num)) {
        num = `${roundToPrecision(num, HEX)}%`;
      }
      if (!this.#hasDim && !this.hasEtc) {
        values.push(num);
      }
    }
    if (this.#hasDim) {
      let dim = '';
      let mul = '';
      let div = '';
      if (this.#dimMul.length) {
        if (this.#dimMul.length === 1) {
          [mul] = this.#dimMul as [string];
        } else {
          mul = `${this.sort(this.#dimMul).join(' * ')}`;
        }
      }
      if (this.#dimDiv.length) {
        if (this.#dimDiv.length === 1) {
          [div] = this.#dimDiv as [string];
        } else {
          div = `${this.sort(this.#dimDiv).join(' * ')}`;
        }
      }
      if (Number.isFinite(num)) {
        if (mul) {
          if (div) {
            if (div.includes('*')) {
              dim = calc(`calc(${num} * ${mul} / (${div}))`, this.#calcOpts);
            } else {
              dim = calc(`calc(${num} * ${mul} / ${div})`, this.#calcOpts);
            }
          } else {
            dim = calc(`calc(${num} * ${mul})`, this.#calcOpts);
          }
        } else if (div.includes('*')) {
          dim = calc(`calc(${num} / (${div}))`, this.#calcOpts);
        } else {
          dim = calc(`calc(${num} / ${div})`, this.#calcOpts);
        }
        values.push(dim.replace(/^calc/, ''));
      } else {
        if (!values.length && num !== undefined) {
          values.push(num);
        }
        if (mul) {
          if (div) {
            if (div.includes('*')) {
              dim = calc(`calc(${mul} / (${div}))`, this.#calcOpts);
            } else {
              dim = calc(`calc(${mul} / ${div})`, this.#calcOpts);
            }
          } else {
            dim = calc(`calc(${mul})`, this.#calcOpts);
          }
          if (values.length) {
            values.push('*', dim.replace(/^calc/, ''));
          } else {
            values.push(dim.replace(/^calc/, ''));
          }
        } else {
          dim = calc(`calc(${div})`, this.#calcOpts);
          if (values.length) {
            values.push('/', dim.replace(/^calc/, ''));
          } else {
            values.push('1', '/', dim.replace(/^calc/, ''));
          }
        }
      }
    }
    if (this.#hasEtc) {
      if (this.#etcMul.length) {
        if (!values.length && num !== undefined) {
          values.push(num);
        }
        const mul = this.sort(this.#etcMul).join(' * ');
        if (values.length) {
          values.push(`* ${mul}`);
        } else {
          values.push(`${mul}`);
        }
      }
      if (this.#etcDiv.length) {
        const div = this.sort(this.#etcDiv).join(' * ');
        if (div.includes('*')) {
          if (values.length) {
            values.push(`/ (${div})`);
          } else {
            values.push(`1 / (${div})`);
          }
        } else if (values.length) {
          values.push(`/ ${div}`);
        } else {
          values.push(`1 / ${div}`);
        }
      }
    }
    if (values.length) {
      return values.join(' ');
    }
    return '';
  }

  /**
   * Sums the stored values and returns the resolved expression string.
   * @returns The resolved sum expression
   */
  sum(): string {
    const values = [];
    if (this.#hasNum) {
      let num = 0;
      for (const i of this.#numSum) {
        num += i;
        if (!Number.isFinite(num) || Number.isNaN(num)) {
          break;
        }
      }
      values.push(num);
    }
    if (this.#hasPct) {
      let num: number | string = 0;
      for (const i of this.#pctSum) {
        num += i;
        if (!Number.isFinite(num)) {
          break;
        }
      }
      if (Number.isFinite(num)) {
        num = `${num}%`;
      }
      if (values.length) {
        values.push(`+ ${num}`);
      } else {
        values.push(num);
      }
    }
    if (this.#hasDim) {
      let dim, sum, sub;
      if (this.#dimSum.length) {
        sum = this.sort(this.#dimSum).join(' + ');
      }
      if (this.#dimSub.length) {
        sub = this.sort(this.#dimSub).join(' + ');
      }
      if (sum) {
        if (sub) {
          if (sub.includes('-')) {
            dim = calc(`calc(${sum} - (${sub}))`, this.#calcOpts);
          } else {
            dim = calc(`calc(${sum} - ${sub})`, this.#calcOpts);
          }
        } else {
          dim = calc(`calc(${sum})`, this.#calcOpts);
        }
      } else {
        dim = calc(`calc(-1 * (${sub}))`, this.#calcOpts);
      }
      if (values.length) {
        values.push('+', dim.replace(/^calc/, ''));
      } else {
        values.push(dim.replace(/^calc/, ''));
      }
    }
    if (this.#hasEtc) {
      if (this.#etcSum.length) {
        const sum = this.sort(this.#etcSum)
          .map(item => {
            let res;
            const hasOperator = tokenize({ css: item }).some(
              ([type, val]) => type === DELIM && OPERATORS.has(val)
            );
            if (hasOperator && !item.startsWith('(') && !item.endsWith(')')) {
              res = `(${item})`;
            } else {
              res = item;
            }
            return res;
          })
          .join(' + ');
        if (values.length) {
          if (this.#etcSum.length > 1) {
            values.push(`+ (${sum})`);
          } else {
            values.push(`+ ${sum}`);
          }
        } else {
          values.push(`${sum}`);
        }
      }
      if (this.#etcSub.length) {
        const sub = this.sort(this.#etcSub)
          .map(item => {
            let res;
            const hasOperator = tokenize({ css: item }).some(
              ([type, val]) => type === DELIM && OPERATORS.has(val)
            );
            if (hasOperator && !item.startsWith('(') && !item.endsWith(')')) {
              res = `(${item})`;
            } else {
              res = item;
            }
            return res;
          })
          .join(' + ');
        if (values.length) {
          if (this.#etcSub.length > 1) {
            values.push(`- (${sub})`);
          } else {
            values.push(`- ${sub}`);
          }
        } else if (this.#etcSub.length > 1) {
          values.push(`-1 * (${sub})`);
        } else {
          values.push(`-1 * ${sub}`);
        }
      }
    }
    if (values.length) {
      return values.join(' ');
    }
    return '';
  }
}

/**
 * Sorts calculation values.
 * @param values - The values to sort
 * @param [finalize] - Whether to finalize the values
 * @returns The sorted calculation expression string
 */
export const sortCalcValues = (
  values: (number | string)[] = [],
  finalize: boolean = false
): string => {
  if (values.length < TRIA) {
    throw new Error(`Unexpected array length ${values.length}.`);
  }
  const start = values.shift();
  if (!isString(start) || !start.endsWith('(')) {
    throw new Error(`Unexpected token ${start}.`);
  }
  const end = values.pop();
  if (end !== ')') {
    throw new Error(`Unexpected token ${end}.`);
  }
  if (values.length === 1) {
    const [value] = values;
    if (!isStringOrNumber(value)) {
      throw new Error(`Unexpected token ${value}.`);
    }
    return `${start}${value}${end}`;
  }
  const sortedValues = [];
  const cal = new Calculator();
  let operator: string = '';
  let hasAddSub = false;
  for (const value of values) {
    if (!isStringOrNumber(value)) {
      throw new Error(`Unexpected token ${value}.`);
    }
    if (value === '*' || value === '/') {
      operator = value;
    } else if (value === '+' || value === '-') {
      const sortedValue = cal.multiply();
      if (sortedValue) {
        sortedValues.push(sortedValue, value);
      }
      // Mark the presence of + or -
      hasAddSub = true;
      cal.clear();
      operator = '';
    } else {
      const numValue = Number(value);
      const strValue = `${value}`;
      const parsedTerm = parseTerm(strValue);
      const isDim =
        parsedTerm && parsedTerm.unit !== '' && parsedTerm.unit !== '%';
      switch (operator) {
        case '/': {
          if (Number.isFinite(numValue)) {
            cal.hasNum = true;
            cal.numMul.push(1 / numValue);
          } else if (parsedTerm && parsedTerm.unit === '%') {
            cal.hasPct = true;
            cal.pctMul.push((MAX_PCT * MAX_PCT) / parsedTerm.num);
          } else if (isDim) {
            cal.hasDim = true;
            cal.dimDiv.push(strValue);
          } else {
            cal.hasEtc = true;
            cal.etcDiv.push(strValue);
          }
          break;
        }
        case '*':
        default: {
          if (Number.isFinite(numValue)) {
            cal.hasNum = true;
            cal.numMul.push(numValue);
          } else if (parsedTerm && parsedTerm.unit === '%') {
            cal.hasPct = true;
            cal.pctMul.push(parsedTerm.num);
          } else if (isDim) {
            cal.hasDim = true;
            cal.dimMul.push(strValue);
          } else {
            cal.hasEtc = true;
            cal.etcMul.push(strValue);
          }
        }
      }
    }
  }
  const sortedValue = cal.multiply();
  if (sortedValue) {
    sortedValues.push(sortedValue);
  }
  let resolvedValue = '';
  if (finalize && hasAddSub) {
    const finalizedValues = [];
    cal.clear();
    operator = '';
    for (const value of sortedValues) {
      if (value === '+' || value === '-') {
        operator = value;
      } else {
        const numValue = Number(value);
        const strValue = `${value}`;
        const parsedTerm = parseTerm(strValue);
        const isDim =
          parsedTerm && parsedTerm.unit !== '' && parsedTerm.unit !== '%';
        switch (operator) {
          case '-': {
            if (Number.isFinite(numValue)) {
              cal.hasNum = true;
              cal.numSum.push(-1 * numValue);
            } else if (parsedTerm && parsedTerm.unit === '%') {
              cal.hasPct = true;
              cal.pctSum.push(-1 * parsedTerm.num);
            } else if (isDim) {
              cal.hasDim = true;
              cal.dimSub.push(strValue);
            } else {
              cal.hasEtc = true;
              cal.etcSub.push(strValue);
            }
            break;
          }
          case '+':
          default: {
            if (Number.isFinite(numValue)) {
              cal.hasNum = true;
              cal.numSum.push(numValue);
            } else if (parsedTerm && parsedTerm.unit === '%') {
              cal.hasPct = true;
              cal.pctSum.push(parsedTerm.num);
            } else if (isDim) {
              cal.hasDim = true;
              cal.dimSum.push(strValue);
            } else {
              cal.hasEtc = true;
              cal.etcSum.push(strValue);
            }
          }
        }
      }
    }
    const sortedValue = cal.sum();
    finalizedValues.push(sortedValue);
    cal.clear();
    operator = '';
    resolvedValue = finalizedValues.join(' ').replace(/\+\s-/g, '- ');
  } else {
    resolvedValue = sortedValues.join(' ').replace(/\+\s-/g, '- ');
  }
  if (
    resolvedValue.startsWith('(') &&
    resolvedValue.endsWith(')') &&
    resolvedValue.lastIndexOf('(') === 0 &&
    resolvedValue.indexOf(')') === resolvedValue.length - 1
  ) {
    resolvedValue = resolvedValue.substring(1, resolvedValue.length - 1);
  }
  return `${start}${resolvedValue}${end}`;
};

/**
 * Sorts terms within math functions.
 * @param expr - The unwrapped calc expression
 * @returns The sorted expression string
 */
export const sortMathFnTerms = (expr: string): string => {
  const tokens = tokenize({ css: expr });
  const terms: string[] = [];
  let currentTermTokens: string[] = [];
  let currentSign = '';
  let depth = 0;
  for (const token of tokens) {
    const [type, value] = token as [TokenType, string];
    if (type === PAREN_OPEN || type === FUNC) {
      depth++;
    } else if (type === PAREN_CLOSE) {
      depth--;
    }
    if (depth === 0 && (value === '+' || value === '-')) {
      const termStr = currentTermTokens.join('').trim();
      if (termStr) {
        terms.push((currentSign === '-' ? '-' : '') + termStr);
      }
      currentTermTokens = [];
      currentSign = value;
      continue;
    }
    currentTermTokens.push(value);
  }
  const finalTermStr = currentTermTokens.join('').trim();
  if (finalTermStr) {
    terms.push((currentSign === '-' ? '-' : '') + finalTermStr);
  }
  if (terms.length === 0) {
    return expr;
  }
  terms.sort((a, b) => {
    const parsedA = parseTerm(a);
    const parsedB = parseTerm(b);
    if (parsedA && parsedB) {
      if (parsedA.unit === parsedB.unit) {
        return parsedA.num - parsedB.num;
      }
      return parsedA.unit > parsedB.unit ? 1 : -1;
    }
    if (a === b) {
      return 0;
    }
    return a > b ? 1 : -1;
  });
  const [firstTerm, ...restTerms] = terms;
  let res = firstTerm as string;
  for (const term of restTerms) {
    if (term.startsWith('-')) {
      res += ` - ${term.substring(1)}`;
    } else {
      res += ` + ${term}`;
    }
  }
  return res;
};

/**
 * Resolves an AST node.
 * @param node - The AST node to resolve
 * @param isRoot - Whether this is the root node
 * @returns The resolved value string
 */
export const resolveNode = (node: CalcASTNode[], isRoot: boolean): string => {
  const firstItemRaw = node[0];
  const isCommaMathFnNode =
    isString(firstItemRaw) && REG_FN_MATH_START.test(firstItemRaw);
  const flatItems: string[] = [];
  for (const item of node) {
    if (Array.isArray(item)) {
      flatItems.push(resolveNode(item, isCommaMathFnNode));
    } else {
      flatItems.push(item);
    }
  }
  const hasComma = flatItems.includes(',');
  const firstItem = flatItems[0] ?? '';
  const isCommaMathFn =
    isString(firstItem) && REG_FN_MATH_START.test(firstItem);
  if (hasComma && isCommaMathFn && flatItems.at(-1) === ')') {
    const fnName = flatItems.shift() as string;
    const fnEnd = flatItems.pop() as string;
    const args: string[][] = [];
    let currentArg: string[] = [];
    for (const item of flatItems) {
      if (item === ',') {
        args.push(currentArg);
        currentArg = [];
      } else {
        currentArg.push(item);
      }
    }
    if (currentArg.length) {
      args.push(currentArg);
    }
    const resolvedArgs = args.map(argNodes => {
      if (argNodes.length >= TRIA) {
        const temp = [FN_CALC, ...argNodes, ')'];
        const sorted = sortCalcValues(temp, true);
        const unwrapped = sorted.substring(5, sorted.length - 1);
        return sortMathFnTerms(unwrapped);
      } else if (argNodes.length === 1) {
        const item = argNodes[0];
        if (isString(item) && item.startsWith(FN_CALC) && item.endsWith(')')) {
          const unwrapped = item.substring(5, item.length - 1);
          return sortMathFnTerms(unwrapped);
        }
      }
      return argNodes.join('');
    });
    return `${fnName}${resolvedArgs.join(', ')}${fnEnd}`;
  }
  if (isRoot) {
    if (!hasComma) {
      if (flatItems.length >= TRIA) {
        return sortCalcValues(flatItems, true);
      }
      const first = flatItems[0] ?? '';
      if (first.endsWith('(')) {
        return flatItems.join('');
      }
      return /^[a-z-]+\(/.test(first) ? first : `calc(${first})`;
    }
    return flatItems.join('').replace(/,\s*/g, ', ');
  }
  if (flatItems.length >= TRIA && !hasComma) {
    let serialized = sortCalcValues(flatItems, false);
    if (REG_FN_VAR_START.test(serialized)) {
      serialized = calc(serialized, { toCanonicalUnits: true });
    }
    return serialized;
  }
  return flatItems.join('').replace(/,\s*/g, ', ');
};

/**
 * Serializes a calc expression.
 * @param value - The CSS value to serialize
 * @param [opt] - Configuration options
 * @returns The serialized value string
 */
export const serializeCalc = (value: string, opt: Options = {}): string => {
  if (!isString(value)) {
    throw new TypeError(`${value} is not a string.`);
  }
  const { format = '' } = opt;
  if (!REG_FN_VAR_START.test(value) || format !== VAL_SPEC) {
    return value;
  }
  const str = value.trim();
  const cacheKey: string = createCacheKey(
    {
      namespace: NAMESPACE,
      name: 'serializeCalc',
      value: str
    },
    opt
  );
  const cachedResult = getCache(cacheKey);
  if (cachedResult !== false) {
    return cachedResult.item as string;
  }
  const tokens = tokenize({ css: str }).filter((token: CSSToken) => {
    const [type] = token as [TokenType, string];
    return type !== W_SPACE && type !== COMMENT && type !== EOF;
  });
  const stack: [CalcASTNode[], ...CalcASTNode[][]] = [[]];
  for (const token of tokens) {
    const [type, val] = token as [TokenType, string];
    if (type === PAREN_OPEN || type === FUNC) {
      const newNode: CalcASTNode[] = [val];
      const parent = stack.at(-1) as CalcASTNode[];
      parent.push(newNode);
      stack.push(newNode);
    } else if (type === PAREN_CLOSE) {
      if (stack.length > 1) {
        const currentLevel = stack.pop() as CalcASTNode[];
        currentLevel.push(val);
      } else {
        const root = stack[0] as CalcASTNode[];
        root.push(val);
      }
    } else {
      const parent = stack.at(-1) as CalcASTNode[];
      parent.push(val);
    }
  }
  let serializedCalc = '';
  const rootItems = stack[0];
  if (rootItems.length === 1 && Array.isArray(rootItems[0])) {
    serializedCalc = resolveNode(rootItems[0], true);
  } else {
    serializedCalc = resolveNode(rootItems, true);
  }
  setCache(cacheKey, serializedCalc);
  return serializedCalc;
};

/**
 * Resolves a dimension token to a pixel value or canonical unit.
 * @param token - The CSS token to resolve
 * @param [opt] - Configuration options
 * @returns The resolved dimension string, or null on failure
 */
export const resolveDimension = (
  token: CSSToken,
  opt: Options = {}
): string | null => {
  if (!Array.isArray(token)) {
    throw new TypeError(`${token} is not an array.`);
  }
  const [, , , , detail = {}] = token;
  const { unit, value } = detail as {
    unit: string;
    value: number;
  };
  if (unit === 'px') {
    return `${value}${unit}`;
  }
  const pixelValue = resolveLengthInPixels(Number(value), unit, opt);
  if (Number.isFinite(pixelValue)) {
    return `${roundToPrecision(pixelValue, HEX)}px`;
  }
  return null;
};

/**
 * Parses CSS calc() tokens.
 * @param tokens - The array of CSS tokens
 * @param [opt] - Configuration options
 * @returns An array of parsed token strings
 */
export const parseCalcTokens = (
  tokens: CSSToken[],
  opt: Options = {}
): string[] => {
  if (!Array.isArray(tokens)) {
    throw new TypeError(`${tokens} is not an array.`);
  }
  const { format = '' } = opt;
  const mathFunc = new Set();
  let nest = 0;
  const res: string[] = [];
  for (const token of tokens) {
    if (!Array.isArray(token)) {
      throw new TypeError(`${token} is not an array.`);
    }
    const [type = '', value = ''] = token as [TokenType, string];
    switch (type) {
      case DIM: {
        if (format === VAL_SPEC && !mathFunc.has(nest)) {
          res.push(value);
        } else {
          const resolvedValue = resolveDimension(token, opt);
          if (isString(resolvedValue)) {
            res.push(resolvedValue);
          } else {
            res.push(value);
          }
        }
        break;
      }
      case FUNC:
      case PAREN_OPEN: {
        res.push(value);
        nest++;
        if (REG_FN_MATH_START.test(value)) {
          mathFunc.add(nest);
        }
        break;
      }
      case PAREN_CLOSE: {
        if (res.length) {
          const lastValue = res.at(-1);
          if (lastValue === ' ') {
            res.splice(-1, 1, value);
          } else {
            res.push(value);
          }
        } else {
          res.push(value);
        }
        if (mathFunc.has(nest)) {
          mathFunc.delete(nest);
        }
        nest--;
        break;
      }
      case W_SPACE: {
        if (res.length) {
          const lastValue = res.at(-1);
          if (
            isString(lastValue) &&
            !lastValue.endsWith('(') &&
            lastValue !== ' '
          ) {
            res.push(value);
          }
        }
        break;
      }
      default: {
        if (type !== COMMENT && type !== EOF) {
          res.push(value);
        }
      }
    }
  }
  return res;
};

/**
 * Resolves CSS calc() expressions.
 * @param value - The CSS value including calc()
 * @param [opt] - Configuration options
 * @returns The resolved CSS value string
 */
export const cssCalc = (value: string, opt: Options = {}): string => {
  if (!isString(value)) {
    throw new TypeError(`${value} is not a string.`);
  }
  const options = {
    ...opt
  };
  const maxLength = getMaxLength(options);
  if (value.length > maxLength) {
    return '';
  }
  const { format } = options;
  if (REG_FN_VAR.test(value)) {
    if (format === VAL_SPEC) {
      return value;
    } else {
      const resolvedValue = resolveVar(value, options);
      if (isString(resolvedValue)) {
        return resolvedValue;
      } else {
        return '';
      }
    }
  } else if (!REG_FN_CALC.test(value)) {
    return value;
  }
  const str = value.trim();
  const cacheKey: string = createCacheKey(
    {
      namespace: NAMESPACE,
      name: 'cssCalc',
      value: str
    },
    options
  );
  const cachedResult = getCache(cacheKey);
  if (cachedResult !== false) {
    return cachedResult.item as string;
  }
  const tokens = tokenize({ css: str });
  const values = parseCalcTokens(tokens, options);
  let resolvedValue: string = calc(values.join(''), {
    toCanonicalUnits: true
  });
  if (REG_FN_VAR_START.test(value)) {
    const parsedResolved = parseTerm(resolvedValue);
    if (parsedResolved && parsedResolved.unit !== '') {
      const num = Number(parsedResolved.num);
      resolvedValue = `${roundToPrecision(num, HEX)}${parsedResolved.unit}`;
    }
    // Wrap with `calc()`
    if (
      resolvedValue &&
      !REG_FN_VAR_START.test(resolvedValue) &&
      format === VAL_SPEC
    ) {
      resolvedValue = `calc(${resolvedValue})`;
    }
  }
  if (format === VAL_SPEC) {
    if (REG_OPERATOR.test(resolvedValue) && !resolvedValue.includes('NaN')) {
      resolvedValue = serializeCalc(resolvedValue, options);
    } else if (REG_FN_CALC_NUM.test(resolvedValue)) {
      const [, val] = resolvedValue.match(REG_FN_CALC_NUM) as MatchedRegExp;
      resolvedValue = `calc(${roundToPrecision(Number(val), HEX)})`;
    }
  }
  setCache(cacheKey, resolvedValue);
  return resolvedValue;
};

/**
 * Resolves custom properties within tokens.
 * @param tokens - The array of CSS tokens
 * @param [opt] - Configuration options
 * @returns A tuple containing the remaining tokens and the resolved value string
 */
export function resolveCustomProperty(
  tokens: CSSToken[],
  opt: Options = {}
): [CSSToken[], string] {
  if (!Array.isArray(tokens)) {
    throw new TypeError(`${tokens} is not an array.`);
  }
  const { customProperty = {} } = opt;
  const items: string[] = [];
  while (tokens.length) {
    const token = tokens.shift();
    if (!token) {
      break;
    }
    if (!Array.isArray(token)) {
      throw new TypeError(`${token} is not an array.`);
    }
    const [type, value] = token as [TokenType, string];
    // End of var()
    if (type === PAREN_CLOSE) {
      break;
    }
    // Nested var()
    if (value === FN_VAR) {
      const [, item] = resolveCustomProperty(tokens, opt);
      if (item) {
        items.push(item);
      }
    } else if (value && type === IDENT) {
      if (value.startsWith('--')) {
        let item: string | undefined;
        if (customProperty[value]) {
          const resolver = customProperty[value];
          item = typeof resolver === 'function' ? resolver(value) : resolver;
        } else if (typeof customProperty.callback === 'function') {
          item = customProperty.callback(value);
        }
        if (item) {
          items.push(item);
        }
      } else {
        items.push(value);
      }
    }
  }
  let resolveAsColor = false;
  if (items.length > 1) {
    resolveAsColor = isValidColor(items.at(-1));
  }
  let resolvedValue = '';
  for (let item of items) {
    item = item.trim();
    if (REG_FN_VAR.test(item)) {
      // Recurse resolveVar()
      const resolvedItem = resolveVar(item, opt);
      if (
        isString(resolvedItem) &&
        (!resolveAsColor || isValidColor(resolvedItem))
      ) {
        resolvedValue = resolvedItem;
      }
    } else if (REG_FN_CALC.test(item)) {
      item = cssCalc(item, opt);
      if (!resolveAsColor || isValidColor(item)) {
        resolvedValue = item;
      }
    } else if (
      item &&
      !CSS_WIDE_KEYWORDS.has(item) &&
      (!resolveAsColor || isValidColor(item))
    ) {
      resolvedValue = item;
    }
    if (resolvedValue) {
      break;
    }
  }
  return [tokens, resolvedValue];
}

/**
 * Parses CSS var() tokens.
 * @param tokens - The array of CSS tokens
 * @param [opt] - Configuration options
 * @returns An array of parsed token strings, or null on failure
 */
export function parseVarTokens(
  tokens: CSSToken[],
  opt: Options = {}
): string[] | null {
  const res: string[] = [];
  while (tokens.length) {
    const token = tokens.shift();
    if (!token) {
      break;
    }
    const [type = '', value = ''] = token as [TokenType, string];
    if (value === FN_VAR) {
      const [, resolvedValue] = resolveCustomProperty(tokens, opt);
      if (!resolvedValue) {
        return null;
      }
      res.push(resolvedValue);
    } else {
      switch (type) {
        case PAREN_CLOSE: {
          if (res.length) {
            if (res.at(-1) === ' ') {
              res[res.length - 1] = value;
            } else {
              res.push(value);
            }
          } else {
            res.push(value);
          }
          break;
        }
        case W_SPACE: {
          if (res.length) {
            const lastValue = res.at(-1);
            if (
              isString(lastValue) &&
              !lastValue.endsWith('(') &&
              lastValue !== ' '
            ) {
              res.push(value);
            }
          }
          break;
        }
        default: {
          if (type !== COMMENT && type !== EOF) {
            res.push(value);
          }
        }
      }
    }
  }
  return res;
}

/**
 * Resolves CSS var() expressions.
 * @param value - The CSS value including var()
 * @param [opt] - Configuration options
 * @returns The resolved value string, or null on failure
 */
export function resolveVar(value: string, opt: Options = {}): string | null {
  if (!isString(value)) {
    throw new TypeError(`${value} is not a string.`);
  }
  const maxLength = getMaxLength(opt);
  if (value.length > maxLength) {
    return null;
  }
  if (!REG_FN_VAR.test(value) || opt.format === VAL_SPEC) {
    return value;
  }
  const str = value.trim();
  const cacheKey: string = createCacheKey(
    {
      namespace: NAMESPACE,
      name: 'resolveVar',
      value: str
    },
    opt
  );
  const cachedResult = getCache(cacheKey);
  if (cachedResult !== false) {
    return cachedResult.item as string | null;
  }
  const tokens = tokenize({ css: str });
  const values = parseVarTokens(tokens, opt);
  if (Array.isArray(values)) {
    let color = values.join('');
    // Check color length
    if (color.length > maxLength) {
      setCache(cacheKey, null);
      return null;
    }
    if (REG_FN_CALC.test(color)) {
      color = cssCalc(color, opt);
    }
    setCache(cacheKey, color);
    return color;
  } else {
    setCache(cacheKey, null);
    return null;
  }
}

/**
 * Evaluates CSS var() expressions.
 * @param value - The CSS value including var()
 * @param [opt] - Configuration options
 * @returns The resolved CSS value string
 */
export const cssVar = (value: string, opt: Options = {}): string => {
  const options = {
    ...opt
  };
  const resolvedValue = resolveVar(value, options);
  if (isString(resolvedValue)) {
    return resolvedValue;
  }
  return '';
};
