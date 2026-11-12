/**
 * CSS calc() and var() utilities.
 */
import { CSSToken } from '@csstools/css-tokenizer';
import { CalcASTNode, Options } from '../typedef';
/**
 * Parses a term string or number into a numeric value and its unit.
 * @param term - The term string or number to parse
 * @returns An object containing the parsed number and unit, or null on failure
 */
export declare const parseTerm: (term: string | number) => {
    num: number;
    unit: string;
} | null;
/**
 * Calculator class for handling calculation terms and operations.
 */
export declare class Calculator {
    #private;
    /**
     * Creates an instance of Calculator.
     */
    constructor();
    get hasNum(): boolean;
    set hasNum(value: boolean);
    get numSum(): number[];
    get numMul(): number[];
    get hasPct(): boolean;
    set hasPct(value: boolean);
    get pctSum(): number[];
    get pctMul(): number[];
    get hasDim(): boolean;
    set hasDim(value: boolean);
    get dimSum(): string[];
    get dimSub(): string[];
    get dimMul(): string[];
    get dimDiv(): string[];
    get hasEtc(): boolean;
    set hasEtc(value: boolean);
    get etcSum(): string[];
    get etcSub(): string[];
    get etcMul(): string[];
    get etcDiv(): string[];
    /**
     * Clears all stored values and resets states.
     */
    clear(): void;
    /**
     * Sorts an array of value strings.
     * @param values - The array of values to sort
     * @returns The sorted array of values
     */
    sort(values?: string[]): string[];
    /**
     * Multiplies the stored values and returns the resolved expression string.
     * @returns The resolved multiplication expression
     */
    multiply(): string;
    /**
     * Sums the stored values and returns the resolved expression string.
     * @returns The resolved sum expression
     */
    sum(): string;
}
/**
 * Sorts calculation values.
 * @param values - The values to sort
 * @param [finalize] - Whether to finalize the values
 * @returns The sorted calculation expression string
 */
export declare const sortCalcValues: (values?: (number | string)[], finalize?: boolean) => string;
/**
 * Sorts terms within math functions.
 * @param expr - The unwrapped calc expression
 * @returns The sorted expression string
 */
export declare const sortMathFnTerms: (expr: string) => string;
/**
 * Resolves an AST node.
 * @param node - The AST node to resolve
 * @param isRoot - Whether this is the root node
 * @returns The resolved value string
 */
export declare const resolveNode: (node: CalcASTNode[], isRoot: boolean) => string;
/**
 * Serializes a calc expression.
 * @param value - The CSS value to serialize
 * @param [opt] - Configuration options
 * @returns The serialized value string
 */
export declare const serializeCalc: (value: string, opt?: Options) => string;
/**
 * Resolves a dimension token to a pixel value or canonical unit.
 * @param token - The CSS token to resolve
 * @param [opt] - Configuration options
 * @returns The resolved dimension string, or null on failure
 */
export declare const resolveDimension: (token: CSSToken, opt?: Options) => string | null;
/**
 * Parses CSS calc() tokens.
 * @param tokens - The array of CSS tokens
 * @param [opt] - Configuration options
 * @returns An array of parsed token strings
 */
export declare const parseCalcTokens: (tokens: CSSToken[], opt?: Options) => string[];
/**
 * Resolves CSS calc() expressions.
 * @param value - The CSS value including calc()
 * @param [opt] - Configuration options
 * @returns The resolved CSS value string
 */
export declare const cssCalc: (value: string, opt?: Options) => string;
/**
 * Resolves custom properties within tokens.
 * @param tokens - The array of CSS tokens
 * @param [opt] - Configuration options
 * @returns A tuple containing the remaining tokens and the resolved value string
 */
export declare function resolveCustomProperty(tokens: CSSToken[], opt?: Options): [CSSToken[], string];
/**
 * Parses CSS var() tokens.
 * @param tokens - The array of CSS tokens
 * @param [opt] - Configuration options
 * @returns An array of parsed token strings, or null on failure
 */
export declare function parseVarTokens(tokens: CSSToken[], opt?: Options): string[] | null;
/**
 * Resolves CSS var() expressions.
 * @param value - The CSS value including var()
 * @param [opt] - Configuration options
 * @returns The resolved value string, or null on failure
 */
export declare function resolveVar(value: string, opt?: Options): string | null;
/**
 * Evaluates CSS var() expressions.
 * @param value - The CSS value including var()
 * @param [opt] - Configuration options
 * @returns The resolved CSS value string
 */
export declare const cssVar: (value: string, opt?: Options) => string;
