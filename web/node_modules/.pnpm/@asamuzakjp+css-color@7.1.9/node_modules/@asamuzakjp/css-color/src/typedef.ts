/**
 * Type definitions for CSS color parsing, conversion, and manipulation.
 */

import { CSSToken } from '@csstools/css-tokenizer';

/**
 * Supported CSS gradient function types.
 */
export type GradientType =
  | 'linear-gradient'
  | 'repeating-linear-gradient'
  | 'radial-gradient'
  | 'repeating-radial-gradient'
  | 'conic-gradient'
  | 'repeating-conic-gradient';

/**
 * Known standard CSS color spaces.
 */
type KnownColorSpace =
  | 'srgb'
  | 'srgb-linear'
  | 'display-p3'
  | 'a98-rgb'
  | 'prophoto-rgb'
  | 'rec2020'
  | 'xyz'
  | 'xyz-d50'
  | 'xyz-d65';

/**
 * Supported CSS color space string or a custom color space identifier.
 */
export type ColorSpace = KnownColorSpace | (string & {});

/**
 * Known CSS color output and input formats.
 */
type KnownColorFormat =
  | 'hex'
  | 'hexAlpha'
  | 'rgb'
  | 'hsl'
  | 'hwb'
  | 'lab'
  | 'lch'
  | 'oklab'
  | 'oklch'
  | 'computed'
  | 'specified'
  | 'mix';

/**
 * Color format option or a custom format string.
 */
export type ColorFormat = KnownColorFormat | (string & {});

/**
 * Hue interpolation methods for color spaces involving hue.
 */
export type HueArc = 'shorter' | 'longer' | 'increasing' | 'decreasing';

/**
 * Flags indicating whether channels are missing or powerless [c1, c2, c3, alpha].
 */
export type ChannelNoneFlags = [
  c1: boolean,
  c2: boolean,
  c3: boolean,
  alpha: boolean
];

/**
 * Abstract Syntax Tree (AST) node representation for the calc() expression parser.
 */
export type CalcASTNode = string | CalcASTNode[];

/**
 * Calculation token stream state, including terminal EOF tokens and the current cursor index.
 */
export interface CalculationState {
  tokens: CSSToken[];
  index: number;
}

/**
 * Custom property resolver value or callback function.
 */
type CustomPropertyResolver = string | ((key: string) => string | undefined);

/**
 * Dimension resolver value or callback function.
 */
type DimensionResolver = number | ((key: string) => number | undefined);

/**
 * CSS dimension unit resolution mapping configuration.
 */
interface DimensionMap {
  [key: string]: DimensionResolver | undefined;
  /** Custom dimension unit resolver callback function. */
  callback?: (key: string) => number | undefined;
  /** Font size of the element in pixels. */
  em?: number;
  /** Root element font size in pixels. */
  rem?: number;
  /** Viewport height in pixels. */
  vh?: number;
  /** Viewport width in pixels. */
  vw?: number;
}

/**
 * Custom property lookup map or callback handler.
 */
interface CustomPropertyMap {
  [key: string]: CustomPropertyResolver | undefined;
  /** Custom property resolver callback function. */
  callback?: (key: string) => string | undefined;
}

/**
 * Configuration options for color parsing and resolution.
 */
export interface Options {
  /** Forces alpha channel output. */
  alpha?: boolean;
  /** Preferred color scheme context (e.g., 'light' or 'dark'). */
  colorScheme?: string;
  /** Target color space for output conversion. */
  colorSpace?: ColorSpace;
  /** Fallback color value used for the `currentColor` keyword. */
  currentColor?: string;
  /** Custom property resolvers. */
  customProperty?: CustomPropertyMap;
  /** Adapts XYZ coordinates to the D50 illuminant. */
  d50?: boolean;
  /** Delimiter string or list used for separating channel values. */
  delimiter?: string | readonly string[];
  /** Dimension resolution settings for units like em/rem/vw/vh. */
  dimension?: DimensionMap;
  /** Desired output color format. */
  format?: ColorFormat;
  /** Maximum character length limit for the parsed input string. */
  maxLength?: number;
  /** Returns null instead of throwing an error on invalid input. */
  nullable?: boolean;
  /** Retains comment nodes during parsing. */
  preserveComment?: boolean;
}

/**
 * Three-element color channel tuple [x, y, z].
 */
export type TriColorChannels = [x: number, y: number, z: number];

/**
 * Read-only three-element color channel tuple [x, y, z].
 */
export type ReadonlyTriColorChannels = readonly [
  x: number,
  y: number,
  z: number
];

/**
 * Read-only 3x3 color conversion matrix tuple.
 */
export type ReadonlyColorMatrix = readonly [
  r1: ReadonlyTriColorChannels,
  r2: ReadonlyTriColorChannels,
  r3: ReadonlyTriColorChannels
];

/**
 * Four-element numeric color channel tuple [x, y, z, alpha].
 */
export type ColorChannels = [x: number, y: number, z: number, alpha: number];

/**
 * Four-element color channel tuple containing either numeric or string values [x, y, z, alpha].
 */
export type NumberOrStringColorChannels = [
  x: number | string,
  y: number | string,
  z: number | string,
  alpha: number | string | undefined
];

/**
 * Four-element numeric or string color channel tuple [x, y, z, alpha].
 */
export type NumStrColorChannels = [
  x: number | string,
  y: number | string,
  z: number | string,
  alpha: number | string
];

/**
 * Color channel tuple supporting missing or powerless string keywords.
 */
export type ColorChannelsWithPowerless = [
  x: number | string,
  y: number | string,
  z: number | string,
  alpha: number
];

/**
 * Color space name accompanied by three string channel values and an alpha value.
 */
export type StringColorSpacedChannels = [
  cs: string,
  x: string,
  y: string,
  z: string,
  alpha: string | undefined
];

/**
 * Resolved and computed color channel representation.
 */
export type ComputedColorChannels = [
  cs: string,
  x: number,
  y: number,
  z: number,
  alpha: number
];

/**
 * Resolved specified color channel representation (supporting keywords or mixed types).
 */
export type SpecifiedColorChannels = [
  cs: string,
  x: number | string,
  y: number | string,
  z: number | string,
  alpha: number | string
];

/**
 * Regular expression match result tuple containing 3 capture groups.
 */
export type MatchedRegExp3 = [
  match: string,
  gr1: string,
  gr2: string,
  gr3: string
];

/**
 * Regular expression match result tuple containing 4 capture groups.
 */
export type MatchedRegExp4 = [
  match: string,
  gr1: string,
  gr2: string,
  gr3: string,
  gr4: string
];

/**
 * Standard regular expression match result tuple alias (4 capture groups).
 */
export type MatchedRegExp = MatchedRegExp4;

/**
 * Configuration options for validating color components.
 */
export interface ValidateColorComponentsOptions {
  /** Validates the presence of the alpha channel. */
  alpha?: boolean;
  /** Minimum required number of components. */
  minLength?: number;
  /** Maximum allowed number of components. */
  maxLength?: number;
  /** Minimum allowed numerical value for components. */
  minRange?: number;
  /** Maximum allowed numerical value for components. */
  maxRange?: number;
  /** Determines whether to enforce component value range checks. */
  validateRange?: boolean;
}

/**
 * Options configuration for linear RGB color space conversion.
 */
export interface LinearRgbOptions {
  /** Target color space for conversion. */
  colorSpace?: ColorSpace;
  /** Desired output color format. */
  format?: ColorFormat;
}

/**
 * Function signature for converting a color string into channel values.
 */
export type ColorConvertFn = (
  value: string,
  opt?: Options
) => ColorChannels | ColorChannelsWithPowerless | null;

/**
 * Function signature for resolving complex CSS color expressions.
 */
export type ColorResolverFn = (
  value: string,
  opt?: Options
) => SpecifiedColorChannels | string | null;

/**
 * Parsed CSS gradient object structure.
 */
export interface Gradient {
  /** Raw gradient CSS string value. */
  value: string;
  /** Type of gradient function expression. */
  type: GradientType;
  /** Parsed gradient line parameters or angle/direction. */
  gradientLine?: string;
  /** Ordered list of color stops. */
  colorStopList: ColorStopList;
}

/**
 * Validation result for gradient color stops.
 */
export interface ValidateColorStops {
  /** Extracted color stop strings. */
  colorStops: string[];
  /** Flag indicating whether the color stop list is valid. */
  valid: boolean;
}

/**
 * Validation result for gradient lines.
 */
export interface ValidateGradientLine {
  /** Extracted gradient line configuration string. */
  line: string;
  /** Flag indicating whether the gradient line syntax is valid. */
  valid: boolean;
}

/**
 * Tuple representing a minimum of two color stops for gradients.
 */
export type ColorStopList = [string, string, ...string[]];

/**
 * Parsed color space parameter result specifically for the `color-mix()` function.
 */
export interface ColorSpaceParseResult {
  /** Target color space name. */
  colorSpace: ColorSpace;
  /** Hue interpolation arc or method. */
  hueArc: HueArc | string;
}

/**
 * Color and percentage weight pair used in `color-mix()`.
 */
export interface ColorAndPct {
  /** Raw color string input. */
  color: string;
  /** Raw percentage weight string input. */
  pct: string;
}

/**
 * Color and ratio pair used in `color-mix()`.
 */
export interface ColorAndRatio {
  /** Color string. */
  color: string;
  /** Ratio. */
  ratio: number;
}

/**
 * Colors and multipler pair used in `color-mix()`.
 */
export interface ColorsAndMultipler {
  /** Colors list. */
  colors: ColorAndRatio[];
  /** Multipler. */
  m: number;
}

/**
 * Parsed result of color-mix() inner content color item.
 */
export interface ColorMixInnerColorItem {
  /** Extracted colors. */
  colors: ColorAndPct[];
  /** Total amount of percentage. */
  totalPercentage: number;
}

/**
 * Parsed result of color-mix() inner content.
 */
export interface ColorMixInnerContent {
  /** Target color space name. */
  colorSpace: ColorSpace | null;
  /** Hue interpolation arc or methid. */
  hueArc: HueArc | string;
  /** Extracted colors. */
  colors: ColorAndPct[];
  /** Total amount of percentage. */
  totalPercentage: number;
}

/**
 * Normalized percentage weight values for color blending operations.
 */
export interface NormalizedPercentages {
  /** Normalized mixing weight ratio for the first color (0 to 1). */
  pA: number;
  /** Normalized mixing weight ratio for the second color (0 to 1). */
  pB: number;
  /** Multiplier factor compensating for unnormalized percentage sums. */
  m: number;
}

/**
 * Interpolated color components and combined alpha transparency output.
 */
export interface InterpolatedComponents {
  /** Three-element interpolated color component values. */
  comps: TriColorChannels;
  /** Resulting blended alpha transparency value. */
  alpha: number;
}
